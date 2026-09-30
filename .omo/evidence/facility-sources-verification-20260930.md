# Facility source ledger verification

Executed 2026-09-30T05:18:31Z in `/home/user/Energy-Digital-Twin`.

Artifact: `docs/real-facility-weather-sources.md`.

Scenarios: independently compare the three PV registration records and coordinate duplication counts; validate the ledger's local links and size; make a fresh unauthenticated GET to the official AWS source. Assertions fail the process if records, null shape fields, links, size, HTTP status, or required weather fields differ.

Invocation: `python3 - <<'PY'` with the following executed code:

```python
import json,pathlib,collections,re,urllib.request,datetime
p=pathlib.Path('docs/real-facility-weather-sources.md'); text=p.read_text(); rows=[json.loads(x) for x in pathlib.Path('.worktrees/data/var/data/geography/source-03e02ef/pv_facility.geojsonl').read_text().splitlines()]; counts=collections.Counter(tuple(x['geometry']['coordinates']) for x in rows)
print('Verified UTC',datetime.datetime.now(datetime.timezone.utc).isoformat())
for id,expected in [(93955,13),(93709,3),(93187,25)]:
 r=next(x for x in rows if x['properties']['id']==id)
 assert counts[tuple(r['geometry']['coordinates'])]==expected
 assert r['properties']['install_area_m2'] is None and r['properties']['install_type'] is None
 print('PASS source',id,'coordinate_multiplicity',expected,'unknown area/type')
for target in re.findall(r'\]\(([^)]+)\)',text):
 if not target.startswith('http'): assert (p.parent/target).exists(),target
assert 0<len(text.splitlines())<200
print('PASS local links, nonempty ledger, lines',len(text.splitlines()))
u='https://www.weather.go.kr/w/observation/land/aws-obs-data.do?db=MINDB_01M&stnId=0&sidoCode=5000000000'
r=urllib.request.urlopen(u,timeout=20);d=json.load(r);h=next(x for x in d['items'] if x['awsStnId']==779)
assert r.status==200 and all(x in h for x in ['awsTmp','awsReh','awsPcpHr1','awsWs10','awsWd10','tm','lat','lon'])
print('PASS AWS HTTP',r.status,'station_count',len(d['items']))
print('Hallim sample',json.dumps({k:h[k] for k in ['awsStnId','tm','lat','lon','awsTmp','awsReh','awsPcpHr1','awsWs10','awsWd10']},ensure_ascii=False))
print('LIMIT: no verified PV site geometry or actual ASOS irradiation time series acquired; ledger states missing inputs.')
```

Actual command output (exit code 0):

```text
Verified UTC 2026-09-30T05:18:31.631959+00:00
PASS source 93955 coordinate_multiplicity 13 unknown area/type
PASS source 93709 coordinate_multiplicity 3 unknown area/type
PASS source 93187 coordinate_multiplicity 25 unknown area/type
PASS local links, nonempty ledger, lines 67
PASS AWS HTTP 200 station_count 43
Hallim sample {"awsStnId": 779, "tm": "202609301413", "lat": "33.39268", "lon": "126.25809", "awsTmp": "25.2", "awsReh": "81", "awsPcpHr1": "0", "awsWs10": "2.8", "awsWd10": "북서"}
LIMIT: no verified PV site geometry or actual ASOS irradiation time series acquired; ledger states missing inputs.
```

Verdict: source ledger exists and the direct record/weather checks pass. The ledger's earlier weather sample is explicitly dated and intentionally differs from this fresh observation. Geometry acquisition, irradiation-series acquisition, and product implementation are not claimed complete. Official unit/document retrieval findings and exact URLs are preserved in the ledger; the present runnable check does not certify physical facility geometry or official QC.
