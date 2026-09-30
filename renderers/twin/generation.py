#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Offline validation: public KOSPO 한경풍력 hourly generation (data.go.kr 15043410) against DB plant 51.

uv run renderers/twin/generation.py --self-test   # synthetic CSVs, offline
uv run renderers/twin/generation.py [--csv PATH]  # -> var/research/generation/hankyung_hourly.json (+ DB plant 51 comparison)

Generation is served by the bridge (/api/v1/jeju/pv/generation), never from files on the GPU host: this output stays in
var/research for identity checks and must not be copied under var/rendering.

Source: 「한국남부발전(주)_한경풍력발전실적」, CP949, one row per day and 단계 (년월일, 단계, 총량, 평균, 최대, 최소,
최대(시간별), 최소(시간별), 1 .. 24) in kWh. 단계 1 = 한경 1~4호기, 단계 2 = 한경 5~9호기 (dataset note).

Output (schema_version 1):
  start_utc   "YYYY-MM-DDTHH:MM:SSZ", label of index 0; step_s 3600; index i is labelled start_utc + i * step_s (UTC)
  total_kwh   [number | null] farm total = 단계 1 + 단계 2; null when either is null
  stage_kwh   {"1": [...], "2": [...]}; null = blank or "-" cell, no row for that day, or conflicting duplicate rows
  provenance  dataset, file sha256, label rule, counts, the DB plant 51 comparison and notes
Labels: row 년월일 D, column h (1..24) = D + h hours KST (h 24 = next day 00:00), converted to UTC and not shifted.
Farm level only: the unit-to-position mapping is unpublished, so nothing may be attributed to individual turbines.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "var/research/generation/kospo_hankyung_hourly_20260331.csv"
OUT = ROOT / "var/research/generation/hankyung_hourly.json"
KST = timezone(timedelta(hours=9))
HEADER = ["년월일", "단계", "총량", "평균", "최대", "최소", "최대(시간별)", "최소(시간별)", *map(str, range(1, 25))]
STAGES = {"1": "한경 1~4호기 (KOSPO: 1.5 MW x 4, 2004)", "2": "한경 5~9호기 (KOSPO: 3 MW x 5, 2008)"}
DATASET = {"id": "data.go.kr 15043410", "title": "한국남부발전(주)_한경풍력발전실적_20260331", "publisher": "한국남부발전(주)",
           "url": "https://www.data.go.kr/data/15043410/fileData.do", "license": "공공데이터포털 이용허락범위 제한 없음 (무료)",
           "modified": "2026-04-14", "note": "한경풍력 1단계는 1~4호기, 2단계는 5~9호기입니다."}
# DB audit row `generation|51|Hangyoung|wind|106608|2013-01-01 01:00:00|2025-03-01 00:00:00|0|0|23780` (rows, first, last, nulls, negatives, max).
DB_51 = {"plant_id": 51, "rows": 106608, "first": "2013-01-01T01:00:00+09:00", "last": "2025-03-01T00:00:00+09:00",
         "null_values": 0, "negative_values": 0, "max_kwh": 23780, "source": "exa-results/jeju-grid-audit-2026-09-29/database_audit.txt"}
NOTES = ["Time labels are ±1 h uncertain: the source counts hours 1..24 and whether a label ends or starts the hour is unconfirmed; "
         "labels are converted to UTC but not shifted.",
         "Farm-level energy per labelled hour (단계 1 = 1~4호기, 단계 2 = 5~9호기). The unit-to-position mapping is unpublished: "
         "never attribute a value to one turbine. Not real time and not turbine telemetry.",
         "Values are as published, including hours above the 21 MW nameplate (see exa-results/hangyoung-identity-2026-09-30.md). "
         "Units in operation changed over time (4호기 appears to stop around 2019-2020).",
         "'-' and blank cells are null: the published daily 평균 divides by the non-dash hours only (e.g. 2025-07-01 단계 1)."]


def utc(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def number(cell: str, line: int) -> float | None:
    text = cell.strip()
    if text in ("", "-"):
        return None
    try:
        value = float(text)
    except ValueError:
        value = math.inf
    if not math.isfinite(value):
        raise ValueError(f"line {line}: bad value {cell!r}")
    return int(value) if value.is_integer() else value


def load(path: Path) -> dict:
    raw = path.read_bytes()
    rows = [r for r in csv.reader(io.StringIO(raw.decode("cp949"))) if any(c.strip() for c in r)]
    if [c.strip() for c in rows[0]] != HEADER:
        raise ValueError(f"header {rows[0]} is not the 15043410 layout {HEADER}")
    days, conflicts, identical, blank, total_mismatch = {}, set(), 0, 0, []
    for line, row in enumerate(rows[1:], 2):
        if len(row) != len(HEADER):
            raise ValueError(f"line {line}: {len(row)} fields, header has {len(HEADER)}")
        stage = row[1].strip()
        if stage not in STAGES:
            raise ValueError(f"line {line}: unknown 단계 {row[1]!r}")
        key, values = (date.fromisoformat(row[0].strip()), stage), [number(c, line) for c in row[8:]]
        blank += values.count(None)
        daily = number(row[2], line)
        if None not in values and daily is not None and abs(daily - sum(values)) > .5:
            total_mismatch.append({"date": key[0].isoformat(), "stage": stage, "총량": daily, "hour_sum": sum(values)})
        if key in days and days[key] != values:
            conflicts.add(key)
        identical += key in days and days[key] == values
        days[key] = values
    if not days:
        raise ValueError("no data rows")
    first, last = min(d for d, _ in days), max(d for d, _ in days)
    n = ((last - first).days + 1) * 24
    stage_kwh = {s: [None] * n for s in STAGES}
    for (day, stage), values in days.items():
        if (day, stage) not in conflicts:  # ponytail: conflicting duplicate rows (2023-01-30 단계 1) -> null day; no row wins
            i = (day - first).days * 24
            stage_kwh[stage][i:i + 24] = values
    total = [None if a is None or b is None else a + b for a, b in zip(stage_kwh["1"], stage_kwh["2"])]
    start = datetime.combine(first, datetime.min.time(), KST) + timedelta(hours=1)
    label = lambda i: f"{(start + timedelta(hours=i) - timedelta(hours=1)).date()} {i % 24 + 1}시 KST"
    return {"schema_version": 1, "start_utc": utc(start), "step_s": 3600, "unit": "kWh", "total_kwh": total, "stage_kwh": stage_kwh,
            "stages": STAGES,
            "provenance": {"dataset": DATASET, "csv": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                           "sha256": hashlib.sha256(raw).hexdigest(), "encoding": "cp949",
                           "label_rule": "row 년월일 D, column h (1..24) -> D + h hours KST (h 24 = next day 00:00) -> UTC, not shifted",
                           "first_label": label(0), "last_label": label(n - 1), "first_utc": utc(start), "last_utc": utc(start + timedelta(hours=n - 1)),
                           "rows": len(rows) - 1, "days": n // 24, "hours": n, "identical_duplicate_rows": identical,
                           "conflicting_duplicates": [{"date": d.isoformat(), "stage": s, "policy": "whole day null"} for d, s in sorted(conflicts)],
                           "missing_rows": [{"date": d.isoformat(), "stage": s} for d in (first + timedelta(days=k) for k in range(n // 24))
                                            for s in STAGES if (d, s) not in days],
                           "null_cells": blank, "null_hours": {s: v.count(None) for s, v in stage_kwh.items()} | {"total": total.count(None)},
                           "negative_values": sum(v is not None and v < 0 for s in stage_kwh.values() for v in s),
                           "daily_total_mismatches": total_mismatch, "notes": NOTES}}


def compare(series: dict, db: dict = DB_51) -> dict:
    """The DB plant 51 aggregates against this file's farm totals over the same label window."""
    start = datetime.fromisoformat(series["start_utc"].replace("Z", "+00:00"))
    i0, i1 = (int((datetime.fromisoformat(db[k]) - start).total_seconds()) // series["step_s"] for k in ("first", "last"))
    window = series["total_kwh"][i0:i1 + 1] if 0 <= i0 <= i1 < len(series["total_kwh"]) else []
    values = [v for v in window if v is not None]
    ours = {"hours": len(window), "null_hours": len(window) - len(values), "negative_values": sum(v < 0 for v in values),
            "max_kwh": max(values, default=None)}
    match = (ours["hours"], ours["negative_values"], ours["max_kwh"]) == (db["rows"], db["negative_values"], db["max_kwh"])
    return {**db, "this_file_same_window": ours, "match": match,
            "scope": "Aggregate check only (hour count over the same label window, negatives, maximum farm total); not row by row. "
                     "The DB has no nulls where this file has null hours, so the DB's handling of blanks and the conflicting "
                     "2023-01-30 rows is unknown. The plants row (Hangyoung, 33.35/126.18 근사) does not identify the site on its own."}


def self_test() -> None:
    head = ",".join(HEADER) + "\n"
    hours = lambda *v: ",".join(map(str, v))
    day = lambda d, s, v, daily=None: f"{d},{s},{sum(x for x in v if isinstance(x, (int, float))) if daily is None else daily},0,0,0,0,0,{hours(*v)}\n"
    one, two = list(range(1, 25)), [10] * 24
    gaps = [" - ", "", *range(3, 23), "56.32", "          -"]
    text = (head + day("2013-01-01", 1, one) + day("2013-01-01", 2, two, daily=241)  # 총량 disagrees with the hours: recorded
            + day("2013-01-02", 1, gaps, daily=999)                            # dash, blank, decimal
            + day("2013-01-02", 1, gaps, daily=999)                            # identical duplicate collapsed
            + day("2013-01-03", 1, one) + day("2013-01-03", 1, two)            # conflicting duplicate -> null day
            + day("2013-01-03", 2, two) + "\n")                               # 2013-01-02 단계 2 has no row
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "k.csv"
        path.write_bytes(text.encode("cp949"))
        s = load(path)
        assert s["provenance"]["sha256"] == hashlib.sha256(text.encode("cp949")).hexdigest()
        for bad, why in ((text.replace("단계", "stage", 1), "header"), (head + "2013-01-01,3" + ",0" * 30 + "\n", "단계"),
                         (head + "2013-01-01,1,1\n", "fields"), (head + day("2013-01-01", 1, ["abc"] * 24, 0), "bad value"),
                         (head + day("2013-01-01", 1, ["inf"] * 24, 0), "bad value")):
            path.write_bytes(bad.encode("cp949"))
            try:
                load(path)
            except ValueError as error:
                assert why in str(error), (why, error)
            else:
                raise AssertionError(f"accepted: {why}")
    p, st = s["provenance"], s["stage_kwh"]
    assert s["start_utc"] == "2012-12-31T16:00:00Z" and s["step_s"] == 3600 and len(s["total_kwh"]) == 72  # 2013-01-01 1시 KST
    assert p["last_utc"] == "2013-01-03T15:00:00Z" and p["last_label"] == "2013-01-03 24시 KST"             # 24시 = next day 00:00 KST
    assert st["1"][:24] == one and st["2"][:24] == two and s["total_kwh"][:24] == [h + 10 for h in one]
    assert st["1"][24:26] == [None, None] and st["1"][46:48] == [56.32, None] and st["2"][24:48] == [None] * 24
    assert st["1"][48:] == [None] * 24 and st["2"][48:] == two and s["total_kwh"][24:] == [None] * 48
    assert p["conflicting_duplicates"] == [{"date": "2013-01-03", "stage": "1", "policy": "whole day null"}]
    assert p["missing_rows"] == [{"date": "2013-01-02", "stage": "2"}] and p["identical_duplicate_rows"] == 1
    assert p["null_cells"] == 6 and p["null_hours"] == {"1": 27, "2": 24, "total": 48} and p["negative_values"] == 0
    assert p["daily_total_mismatches"] == [{"date": "2013-01-01", "stage": "2", "총량": 241, "hour_sum": 240}]
    db = {**DB_51, "rows": 24, "first": "2013-01-01T01:00:00+09:00", "last": "2013-01-02T00:00:00+09:00", "max_kwh": 34}
    assert compare(s, db)["match"] and compare(s, db)["this_file_same_window"] == {"hours": 24, "null_hours": 0, "negative_values": 0, "max_kwh": 34}
    assert not compare(s, {**db, "max_kwh": 35})["match"] and not compare(s)["match"]  # DB window outside the file
    print("PASS generation: CP949 15043410 layout, h 1..24 KST -> UTC (24시 = next day), '-'/blank/missing rows/conflicting "
          "duplicates null, identical duplicates collapsed, decimals kept, 총량 mismatches recorded, farm total null unless both 단계, "
          "malformed header/단계/fields/values rejected, sha256, DB window comparison")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--csv", type=Path, default=CSV)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    series = load(args.csv)
    series["provenance"]["db_plant_51"] = compare(series)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(series, ensure_ascii=False, separators=(",", ":")) + "\n")
    p = series["provenance"]
    print(json.dumps({"output": str(OUT.relative_to(ROOT)), "bytes": OUT.stat().st_size,
                      **{k: p[k] for k in ("sha256", "first_label", "last_label", "first_utc", "last_utc", "rows", "hours", "null_hours",
                                           "identical_duplicate_rows", "conflicting_duplicates", "missing_rows", "negative_values",
                                           "daily_total_mismatches", "db_plant_51")}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
