use super::*;

fn input() -> SimulationInput {
    serde_json::from_str(include_str!("../../examples/scenario.json")).unwrap()
}

#[test]
fn efficiency_preserves_energy_for_charge_and_discharge() {
    let input = input();
    let result = simulate(&input).unwrap();
    let charge = &result.points[0].scenario;
    let discharge = &result.points[1].scenario;
    assert!((charge.energy_mwh.unwrap() - 5.9).abs() < 1e-10);
    assert!((discharge.energy_mwh.unwrap() - 4.65).abs() < 1e-10);
    assert!(charge.discharge_mw.unwrap().abs() < 1e-10);
    assert!(discharge.charge_mw.unwrap().abs() < 1e-10);
}

#[test]
fn missing_interval_does_not_produce_a_partial_ess_trajectory() {
    let mut input = input();
    input.snapshots.remove(0);
    let result = simulate(&input).unwrap();
    assert_eq!(result.status, Status::Incomplete);
    assert_eq!(result.missing_intervals, vec![input.start]);
    assert!(result.points.is_empty());
    assert!(result.final_scenario_mwh.is_none());
}

#[test]
fn supply_capacity_is_not_generation() {
    let mut input = input();
    input.ess = None;
    input.dispatch = None;
    input.scales.demand = 1.1;
    input.scales.wind = 0.8;
    input.snapshots[0].supply_capacity_mw = Some(99999.0);
    let result = simulate(&input).unwrap();
    assert_eq!(result.status, Status::NetLoadOnly);
    assert!((result.points[0].scenario.net_load_mw - 58.4).abs() < 1e-10);
    assert!(result.points[0].scenario.residual_before_ess_mw.is_none());
}

#[test]
fn empty_ess_cannot_discharge() {
    let mut input = input();
    input.ess.as_mut().unwrap().initial_mwh = 0.0;
    input.snapshots[0].demand_mw = Some(112.0);
    let result = simulate(&input).unwrap();
    assert!(result.points[0].scenario.discharge_mw.unwrap().abs() < 1e-10);
    assert!((result.points[0].scenario.residual_after_ess_mw.unwrap() - 12.0).abs() < 1e-10);
}

#[test]
fn full_ess_cannot_charge() {
    let mut input = input();
    input.ess.as_mut().unwrap().initial_mwh = 10.0;
    let result = simulate(&input).unwrap();
    assert!(result.points[0].scenario.charge_mw.unwrap().abs() < 1e-10);
    assert!((result.points[0].scenario.energy_mwh.unwrap() - 10.0).abs() < 1e-10);
}

#[test]
fn energy_and_power_bounds_limit_dispatch() {
    let mut input = input();
    let ess = input.ess.as_mut().unwrap();
    ess.initial_mwh = 9.9;
    ess.discharge_limit_mw = 3.0;
    let result = simulate(&input).unwrap();
    assert!((result.points[0].scenario.charge_mw.unwrap() - 4.0 / 3.0).abs() < 1e-10);
    assert!((result.points[1].scenario.discharge_mw.unwrap() - 3.0).abs() < 1e-10);
}

#[test]
fn zero_is_valid_and_null_is_incomplete() {
    let mut input = input();
    input.snapshots[0].demand_mw = Some(0.0);
    assert_eq!(simulate(&input).unwrap().status, Status::Complete);
    input.snapshots[0].demand_mw = None;
    assert_eq!(simulate(&input).unwrap().status, Status::Incomplete);
}

#[test]
fn missing_dispatch_is_incomplete_and_ess_without_dispatch_is_invalid() {
    let mut input = input();
    input.dispatch.as_mut().unwrap().remove(0);
    assert_eq!(
        simulate(&input).unwrap().missing_intervals,
        vec![input.start]
    );
    input.dispatch = None;
    assert!(simulate(&input).is_err());
}

#[test]
fn invalid_efficiencies_are_rejected() {
    for value in [0.0, -0.1, 1.01, f64::NAN, f64::INFINITY, f64::from_bits(1)] {
        let mut input = input();
        input.ess.as_mut().unwrap().charge_efficiency = value;
        assert!(simulate(&input).is_err());
    }
}

#[test]
fn stopped_hvdc_requires_zero_flow() {
    let mut input = input();
    assert_eq!(simulate(&input).unwrap().status, Status::Complete);
    input.dispatch.as_mut().unwrap()[0].hvdc[2].power_mw = 1.0;
    assert!(simulate(&input).is_err());
}

#[test]
fn hvdc_export_increases_residual_and_bounds_are_enforced() {
    let mut input = input();
    input.ess = None;
    input.dispatch.as_mut().unwrap()[0].hvdc[1].power_mw = -5.0;
    let result = simulate(&input).unwrap();
    assert!((result.points[0].scenario.residual_before_ess_mw.unwrap() + 7.0).abs() < 1e-10);
    input.dispatch.as_mut().unwrap()[0].hvdc[1].power_mw = -11.0;
    assert!(simulate(&input).is_err());
}

#[test]
fn duplicate_times_are_rejected_after_utc_normalization() {
    let mut input = input();
    input.snapshots[1].observed_at = "2026-01-01T09:00:00+09:00".parse().unwrap();
    assert!(simulate(&input).is_err());
}

#[test]
fn range_and_grid_are_enforced() {
    for end in [
        "2026-01-02T00:05:00Z",
        "2026-01-01T00:10:01Z",
        "2026-01-01T00:00:00Z",
    ] {
        let mut input = input();
        input.end = end.parse().unwrap();
        assert!(simulate(&input).is_err());
    }
}

#[test]
fn overflow_returns_error_instead_of_null_results() {
    let mut input = input();
    input.scales.demand = f64::MAX;
    assert!(matches!(
        simulate(&input),
        Err(SimulationError::NumericRange)
    ));
}

#[test]
fn nan_source_is_rejected_at_the_boundary() {
    let mut input = input();
    input.snapshots[0].wind_mw = Some(f64::NAN);
    assert!(simulate(&input).is_err());
}

#[test]
fn baseline_and_scenario_start_from_same_energy() {
    let mut input = input();
    input.scales.demand = 2.0;
    let result = simulate(&input).unwrap();
    assert!((result.points[0].baseline.energy_mwh.unwrap() - 5.9).abs() < 1e-10);
    assert!((result.points[0].scenario.energy_mwh.unwrap() - 3.75).abs() < 1e-10);
}

#[test]
fn full_day_contains_288_intervals() {
    let mut input = input();
    input.end = input.start + chrono::Duration::hours(24);
    input.dispatch = None;
    input.ess = None;
    input.snapshots = (0..288)
        .map(|i| Snapshot {
            schema_version: 1,
            observed_at: input.start + chrono::Duration::minutes(i * 5),
            source: "synthetic".into(),
            quality_flags: vec![],
            demand_mw: Some(100.0),
            wind_mw: Some(40.0),
            solar_mw: Some(10.0),
            supply_capacity_mw: None,
            renewable_total_mw: None,
        })
        .collect();
    let result = simulate(&input).unwrap();
    assert_eq!(result.points.len(), 288);
    assert!(
        result
            .points
            .iter()
            .all(|p| (p.scenario.net_load_mw - 50.0).abs() < 1e-10)
    );
}
