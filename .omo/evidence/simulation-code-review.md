# Offline regional/ESS simulation code review

- codeQualityStatus: CLEAR
- recommendation: APPROVE
- blockers: none
- Scope: src/simulation.rs, src/simulation/{types,validation,cli,tests}.rs, src/main.rs CLI integration, tests/simulation_cli.rs, examples/scenario.json, docs/simulation.md.
- Requirements inspected: jeju_power_grid_digital_twin_design.md sections 17.3–17.4 and docs/superpowers/plans/2026-09-29-simulation-core.md.
- Reviewed current working files (new implementation is untracked, so git diff alone does not contain it). Approval applies to these inspected working contents, not a later commit.

## Findings by severity

- CRITICAL: none.
- HIGH: none.
- MEDIUM: none.
- LOW: none.

## Correctness and scope

Charge/discharge formulas match the specification's grid-side power and efficiency conventions. Initial energy is independently applied to baseline and scenario. Power, energy, and SOC bounds are checked; non-finite calculations fail before result serialization. Required missing source/dispatch data produces an incomplete result without a fabricated trajectory. Supply capacity is preserved but not substituted for generation. HVDC signed flows, per-link bounds, outage zero-flow condition, and stable distinct identifiers are enforced. Input timestamps are normalized to UTC, checked on the five-minute grid, and bounded to 24 hours. No model, Redis, or bridge access is needed for the CLI path.

Quality flags are retained but not interpreted, which is explicitly documented; the offline caller owns selecting usable profiles. Actual bridge/HTTP integration, operating optimization, terminal SOC targets, and network power flow remain outside this approved offline scope. Floating-point reporting (for example 59.00000000000001 percent) is expected roundoff and does not change the model semantics.

## Skill-perspective check

Consulted remove-ai-slops and programming SKILL.md perspectives before judging test relevance and maintainability. Applied read-only slop/overfit pass to production and tests. No needless extraction/normalization, untyped production escape hatch, unnecessary abstraction, deletion-only test, prompt-text test, or implementation-constant mirroring test was found. JSON input validation is at the actual trust boundary. Tests exercise physical arithmetic, missingness, limits, timestamps, invalid numbers, and the executable interface rather than textual implementation details. Test-only serde_json::Value usage and unwrap calls are appropriate to assertions.

## Independently executed evidence

- `cargo test --locked`: 23 unit tests passed, 1 existing real-Redis test ignored, 3 CLI integration tests passed. This is 24 unit tests discovered, not 24 unit tests passed.
- `cargo clippy --locked --all-targets -- -D warnings`: exit 0.
- `target/debug/jeju-twin simulate examples/scenario.json`: exit 0; complete scenario JSON, 12 MW charge then 12 MW discharge; energy 5 -> 5.9 -> 4.65 MWh, final SOC 46.5%, zero post-ESS residual in both intervals.
- `target/debug/jeju-twin --help`: exit 0; simulation command included.
- CLI integration tests execute the binary for missing demand (incomplete, null final energy) and malformed/oversized input (nonzero exit, no stdout JSON).

`omo-agent-toolkit ulw-loop status --json` reported ULW_LOOP_PLAN_MISSING, so this report uses the prescribed fallback evidence path. No source changes, secrets reads, commits, or pushes were performed.

Follow-up verification with the explicitly available dedicated Redis: `TEST_REDIS_URL=redis://127.0.0.1:6380/0 cargo test --locked -- --include-ignored` independently passed all 24 unit tests and all 3 CLI tests. The earlier default-run ignored test was not treated as a defect. Full 24+3 passing evidence is now independently confirmed.
