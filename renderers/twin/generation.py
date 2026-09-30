#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Exported plant 51 ("Hangyoung") hourly generation CSV -> compact static series for the viewer.

uv run renderers/twin/generation.py --self-test                  # synthetic CSVs, offline
uv run renderers/twin/generation.py [--csv PATH] [--plants PATH] # -> var/rendering/local/facility/generation_51.json

Viewer fetch: GET /local/facility/generation_51.json (schema_version 1)
  plant_id     51
  start_utc    "YYYY-MM-DDTHH:MM:SSZ", label of values_kwh[0]
  step_s       3600; values_kwh[i] is labelled start_utc + i * step_s (UTC)
  values_kwh   [number | null]; null = no row or an empty value in the export. Negative values are kept as exported.
  provenance   csv path/sha256, detected columns and unit, rows, first/last (UTC and as exported), counts of nulls,
               missing hours, negatives and identical duplicate rows, source values, plants CSV row, notes.
Labels are the exported ones: whether a label is the hour's start or end is unconfirmed (±1 h), and the series is
not confirmed to be 한경풍력. The viewer must show both notes and must not call the values turbine telemetry.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "var/rendering/local/facility"
KST = timezone(timedelta(hours=9))
# ponytail: long format only (one row per hour). The data.go.kr wide layout (년월일, 단계, 1..24) is rejected for lack of a time column;
# add a reshaper if the public file ever has to be loaded directly.
TIME = {"timestamp", "ts", "time", "datetime", "date_time", "일시", "시각"}
NULL = {"", "\\n", "null", "none", "nan"}
NOTES = ["Time labels are ±1 h uncertain: the source counts hours 1..24 and whether a label is the interval start or end is "
         "unconfirmed; labels are converted to UTC but not shifted.",
         "Plant 51 ('Hangyoung') is not confirmed as 한경풍력: the plants row (name, operator, capacity, coordinates) does not identify "
         "the site; see exa-results/hangyoung-identity-2026-09-30.md.",
         "The DB series ends at label 2025-03-01 00:00 (through 2025-02-28); nothing later is in this export.",
         "Plant-level energy per labelled hour: not per-turbine telemetry and not real time."]


def table(path: Path) -> tuple[list[list[str]], str]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")  # data.go.kr-style exports
    try:
        dialect = csv.Sniffer().sniff(text.split("\n", 1)[0], delimiters=",\t;|")
    except csv.Error as error:
        raise ValueError(f"{path}: header has no , tab ; or | delimiter") from error
    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in row)]
    return rows, hashlib.sha256(raw).hexdigest()


def utc(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def load(path: Path, plant_id: int = 51, plants: Path | None = None) -> dict:
    rows, digest = table(path)
    header = rows[0]
    lower = [h.lower() for h in header]
    t = next((i for i, h in enumerate(lower) if h in TIME), None)
    if t is None:
        raise ValueError(f"no time column in header {header}; expected one of {sorted(TIME)}")
    units = [(i, m[1]) for i, h in enumerate(lower) if (m := re.search(r"(?<![a-z])([km])wh(?![a-z])", h))]
    if len(units) != 1:
        raise ValueError(f"cannot identify one kWh/MWh value column (unit) in header {header}")
    (v, unit), pid, src = units[0], next((i for i, h in enumerate(lower) if h == "plant_id"), None), next((i for i, h in enumerate(lower) if h == "source"), None)
    series, labels, sources, zones, count, duplicates = {}, {}, set(), set(), 0, 0
    for line, row in enumerate(rows[1:], 2):
        if len(row) != len(header):
            raise ValueError(f"line {line}: {len(row)} fields, header has {len(header)}")
        if pid is not None and row[pid] != str(plant_id):
            continue
        try:
            stamp = datetime.fromisoformat(row[t])
        except ValueError as error:
            raise ValueError(f"line {line}: bad timestamp {row[t]!r}") from error
        zones.add(stamp.tzinfo is None)
        if len(zones) > 1:
            raise ValueError(f"line {line}: mixed naive and offset timestamps")
        # ponytail: naive labels are taken as KST (the DB column has no zone; Korea has had no DST since 1988). Add a --tz flag if an export is ever UTC.
        stamp = (stamp.replace(tzinfo=KST) if stamp.tzinfo is None else stamp).astimezone(timezone.utc)
        if stamp.minute or stamp.second or stamp.microsecond:
            raise ValueError(f"line {line}: {row[t]!r} is not on a UTC hour")
        try:
            value = None if row[v].lower() in NULL else float(row[v]) * (1000 if unit == "m" else 1)
        except ValueError:
            value = math.inf
        if value is not None and not math.isfinite(value):
            raise ValueError(f"line {line}: bad value {row[v]!r}")
        count += 1
        if stamp in series:
            if series[stamp] != value:
                raise ValueError(f"line {line}: conflicting duplicate for {row[t]!r}: {series[stamp]} vs {value}")
            duplicates += 1
            continue
        series[stamp], labels[stamp] = value, row[t]
        if src is not None:
            sources.add(row[src])
    if not series:
        raise ValueError(f"no rows for plant {plant_id}")
    start, end = min(series), max(series)
    values = [series.get(start + timedelta(hours=i)) for i in range(int((end - start).total_seconds()) // 3600 + 1)]
    compact = lambda x: x if x is None else (int(r) if (r := round(x, 3)).is_integer() else r)
    record = None
    if plants:
        prows, psha = table(plants)
        key = [h.lower() for h in prows[0]].index("plant_id")
        row = next((dict(zip(prows[0], r)) for r in prows[1:] if r[key] == str(plant_id)), None)
        record = {"path": str(plants), "sha256": psha, "row": row}
    return {"schema_version": 1, "plant_id": plant_id, "start_utc": utc(start), "step_s": 3600,
            "values_kwh": [compact(x) for x in values],
            "provenance": {"csv": str(path), "sha256": digest, "columns": {"time": header[t], "value": header[v], "unit": f"{unit.upper()}Wh",
                                                                          "plant_id": pid is not None and header[pid], "source": src is not None and header[src]},
                           "naive_labels_time_zone": "+09:00 (KST), assumed" if True in zones else None,
                           "rows": count, "unique_hours": len(series), "first_label": labels[start], "last_label": labels[end],
                           "first_utc": utc(start), "last_utc": utc(end), "null_values": sum(x is None for x in series.values()),
                           "missing_hours": len(values) - len(series), "negative_values": sum(x is not None and x < 0 for x in series.values()),
                           "identical_duplicates": duplicates, "sources": sorted(sources), "plant_record": record, "notes": NOTES}}


def self_test() -> None:
    def run(text: str, encoding: str = "utf-8", **kwargs) -> dict:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "g.csv"
            path.write_bytes(text.encode(encoding))
            return load(path, **kwargs)

    def rejects(text: str, why: str, **kwargs) -> None:
        try:
            run(text, **kwargs)
        except ValueError as error:
            assert why in str(error), (why, error)
        else:
            raise AssertionError(f"accepted: {why}")

    head = "timestamp,plant_id,gen_kwh,source\n"
    text = (head + "2013-01-01 01:00:00,51,100,kospo\n"
            "2013-01-01 02:00:00,51,,kospo\n"          # explicit null
            "2013-01-01 02:00:00,3,999,other\n"        # another plant: ignored
            "2013-01-01 04:00:00,51,-2.5,kospo\n"      # 03:00 missing (gap), negative kept
            "2013-01-01 04:00:00,51,-2.5,kospo\n"      # identical duplicate collapsed
            "2013-01-01 05:00:00,51,1.23456,kospo\n")
    s = run(text)
    p = s["provenance"]
    assert s["start_utc"] == "2012-12-31T16:00:00Z" and s["step_s"] == 3600  # naive labels are KST (UTC+9)
    assert s["values_kwh"] == [100, None, None, -2.5, 1.235], s["values_kwh"]
    assert (p["rows"], p["null_values"], p["missing_hours"], p["negative_values"], p["identical_duplicates"]) == (5, 1, 1, 1, 1)
    assert p["first_label"] == "2013-01-01 01:00:00" and p["last_utc"] == "2012-12-31T20:00:00Z" and p["sources"] == ["kospo"]
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "g.csv"
        path.write_text(text)
        assert load(path)["provenance"]["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    # Explicit offsets, MWh -> kWh, pipe delimiter, no plant_id column.
    s = run("ts|gen_mwh\n2013-01-01T01:00:00+09|1.5\n2013-01-01T01:00:00+08:00|\\N\n2012-12-31T18:00:00Z|0.002\n")
    assert s["start_utc"] == "2012-12-31T16:00:00Z" and s["values_kwh"] == [1500, None, 2]
    # CP949 export with Korean header.
    assert run("일시,발전량(kWh)\n2013-01-01 01:00,7\n", "cp949")["values_kwh"] == [7]
    # Plants CSV row is carried as-is.
    with TemporaryDirectory() as tmp:
        (Path(tmp) / "p.csv").write_text("plant_id,plant_name,lat,lon\n3,x,1,2\n51,Hangyoung,33.35,126.18\n")
        s = run(text, plants=Path(tmp) / "p.csv")
    assert s["provenance"]["plant_record"]["row"] == {"plant_id": "51", "plant_name": "Hangyoung", "lat": "33.35", "lon": "126.18"}
    rejects(head + "2013-01-01 01:00:00,51,1,a\n2013-01-01 01:00:00,51,2,a\n", "conflicting duplicate")
    rejects("timestamp,gen\n2013-01-01 01:00:00,1\n", "unit")
    rejects("timestamp,gen_kwh,gen_mwh\n2013-01-01 01:00:00,1,1\n", "unit")
    rejects("when,gen_kwh\n2013-01-01 01:00:00,1\n", "time column")
    rejects(head + "2013-01-01 01:00:00,51,1\n", "fields")
    rejects(head + "2013-01-01 01:30:00,51,1,a\n", "hour")
    rejects(head + "2013-01-01 01:00:00,51,abc,a\n", "value")
    rejects(head + "2013-01-01 01:00:00,51,inf,a\n", "value")
    rejects(head + "2013-01-01 01:00:00,51,1,a\n2013-01-01 02:00:00+09,51,1,a\n", "mixed")
    rejects(head + "2013-01-01 01:00:00,3,1,a\n", "no rows for plant 51")
    print("PASS generation: KST naive labels -> UTC, explicit offsets, gaps/nulls as null, negatives kept, identical duplicates "
          "collapsed, conflicting duplicates/unknown units/malformed rows/non-hourly/mixed zones rejected, MWh, CP949, sha256, plants row")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--plants", type=Path)
    parser.add_argument("--plant-id", type=int, default=51)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    dirs = (ROOT / "var/data/generation", ROOT / ".worktrees/data/var/data/generation")
    path = args.csv or next((d / "hangyoung_51_generation.csv" for d in dirs if (d / "hangyoung_51_generation.csv").exists()), None)
    if path is None:
        sys.exit("generation.py: no plant 51 export yet (looked for hangyoung_51_generation.csv in "
                 + ", ".join(str(d.relative_to(ROOT)) for d in dirs) + "). On the data server run e.g.\n"
                 "  \\copy (SELECT timestamp, plant_id, gen_kwh, source FROM generation WHERE plant_id = 51 ORDER BY timestamp) "
                 "TO 'hangyoung_51_generation.csv' CSV HEADER\nthen copy it here or pass --csv PATH (and --plants plants_3_51.csv).")
    plants = args.plants or next((p for p in (path.with_name("plants_3_51.csv"),) if p.exists()), None)
    series = load(path, args.plant_id, plants)
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"generation_{args.plant_id}.json"
    out.write_text(json.dumps(series, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"output": str(out.relative_to(ROOT)), "bytes": out.stat().st_size,
                      **{k: v for k, v in series["provenance"].items() if k != "notes"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
