# Planning-spec document revision evidence

- Owner: team jeju-real-assets-weather member A (`/root/planning_spec`).
- Assigned product file: `jeju_power_grid_digital_twin_design.md` only. Product code, dependencies and commits were not changed by this member.
- Date: 2026-09-30. No active attempt directory: `omo-agent-toolkit ulw-loop status --json` returned `ULW_LOOP_PLAN_MISSING`; evidence therefore uses `.omo/evidence/`.
- Revised document SHA256 at verification: `c15dbf4d9a0deda67066486cca10de9ee078fb4416baabaf417adab177f31d41`.
- Scope: v0.4 introduction, goals/requirements/MVP, architecture diagram, coordinates, model priority, gates and acceptance criteria; historical notices for 12 and 14–18; new governing section 19. Historical sections 14–18 remain verbatim except their added historical notice.

## Executed check and binary observations

Invocation from repository root:

```bash
set -o pipefail
python3 .omo/evidence/planning-spec-check.py | tee .omo/evidence/planning-spec-check.txt
git diff -- jeju_power_grid_digital_twin_design.md > .omo/evidence/planning-spec.patch
git diff --stat -- jeju_power_grid_digital_twin_design.md
```

Observed exit code 0, `checks: 16/16`; all checks printed PASS. The scoped diff is 139 insertions, 49 deletions. `git diff --check` is also invoked by the check script and passed. The captured diff was read directly, including a separate read of its architecture/gates portion to avoid truncation.

| Success criterion / exact document scenario | Invocation | Binary observable | Captured artifact |
|---|---|---|---|
| Early goals/MVP target current scene; no assumed electrical boundary; beneficiaries explicit | Check script above | First three named checks PASS | `.omo/evidence/planning-spec-check.txt` |
| Real PV sources, geometry/angles and matching camera acceptance; common renderer/statistics/model specs | Check script above | Geometry and shared-spec checks PASS | `.omo/evidence/planning-spec-check.txt` |
| Weather freshness/QC, separate irradiance with time/units, POA/temperature/DC/AC and inverter limits | Check script above | Weather, irradiance and PV-chain checks PASS | `.omo/evidence/planning-spec-check.txt` |
| Feasible statistics, missing history limits, honest curtailment and data-kind labels | Check script above | Statistics, no-false-curtailment and label checks PASS | `.omo/evidence/planning-spec-check.txt` |
| Existing stack retained, generic PV facts match JSON, historical evidence preserved, local links resolve | Check script above | Last five substantive checks PASS | `.omo/evidence/planning-spec-check.txt` |
| Minimal assigned document diff is inspectable and whitespace clean | Check script and scoped git diff above | `git diff --check` PASS; nonempty scoped patch | `.omo/evidence/planning-spec.patch` |

These are document/source-consistency checks, not runtime implementation or geometry certification. Manual review verified that the proposal distinguishes present generic PV geometry, planned real geometry, measured weather inputs and modeled outputs, and does not claim missing drawings/irradiance time series were acquired.

## Source verification and limits

Directly read `renderers/twin/grid-spec.json`, `src/weather.rs`, `docs/estimated-twin.md`, `docs/simulation.md`, and the peer source ledger. Generic PV 4×8/25° and scene bounds are compared against parsed JSON by the runnable check. Runtime report claims were used only as labeled implementation-history references; no earlier logs were treated as freshly executed runtime evidence.

Opened official DOE PV modeling, NIST digital twins, ASOS time-data and KISTEP announcement URLs with the web tool. DOE states the need for system specifications and meteorological inputs; the proposal cites that limited claim. KISTEP template `seq=2` could not render in the web tool, so the document links it for submission-format checking without asserting its contents were inspected. Official links are in section 19.

Weather implementation began concurrently after this document assignment. At handoff the opening and 19.1/19.3 intentionally retain pending/wind-only product status. The leader must update those status sentences only after the weather member's runtime validation. This document check does not assert temperature/humidity/rain have been deployed. Irradiance acquisition and actual PV geometry remain unmet implementation gates.

The Python evidence file triggered an unavailable `basedpyright` notice; no installation was attempted. The evidence script itself ran successfully under Python.
