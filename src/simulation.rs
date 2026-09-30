pub(crate) mod cli;
pub(crate) mod http;
mod types;
mod validation;
pub(crate) use types::*;

use chrono::Duration;
use std::collections::BTreeMap;

const STEP_HOURS: f64 = 1.0 / 12.0;

pub(crate) fn simulate(input: &SimulationInput) -> Result<SimulationResult<'_>, SimulationError> {
    validation::validate(input)?;
    let snapshots: BTreeMap<_, _> = input.snapshots.iter().map(|p| (p.observed_at, p)).collect();
    let dispatch: BTreeMap<_, _> = input
        .dispatch
        .iter()
        .flatten()
        .map(|p| (p.observed_at, p))
        .collect();
    let times: Vec<_> = std::iter::successors(Some(input.start), |time| {
        time.checked_add_signed(Duration::minutes(5))
            .filter(|next| *next < input.end)
    })
    .collect();
    let missing_intervals: Vec<_> = times
        .iter()
        .copied()
        .filter(|time| {
            snapshots.get(time).is_none_or(|p| {
                p.demand_mw.is_none() || p.wind_mw.is_none() || p.solar_mw.is_none()
            }) || (input.dispatch.is_some() && !dispatch.contains_key(time))
        })
        .collect();
    let mut result = SimulationResult {
        model_version: "regional-ess-v1",
        data_kind: "scenario",
        assumptions: [
            "constant power within each 5-minute interval",
            "transmission and ESS standby losses omitted",
            "baseline and scenario share dispatch and initial ESS conditions",
        ],
        input,
        status: if input.dispatch.is_some() {
            Status::Complete
        } else {
            Status::NetLoadOnly
        },
        missing_intervals,
        points: Vec::new(),
        final_baseline_mwh: None,
        final_scenario_mwh: None,
    };
    if !result.missing_intervals.is_empty() {
        result.status = Status::Incomplete;
        return Ok(result);
    }
    let mut baseline_energy = input.ess.as_ref().map(|ess| ess.initial_mwh);
    let mut scenario_energy = baseline_energy;
    for time in times {
        let point = snapshots
            .get(&time)
            .ok_or(SimulationError::Invalid("missing snapshot"))?;
        let (Some(demand), Some(wind), Some(solar)) =
            (point.demand_mw, point.wind_mw, point.solar_mw)
        else {
            return Err(SimulationError::Invalid("missing required MW"));
        };
        let generation = dispatch
            .get(&time)
            .map(|p| p.nonrenewable_mw + p.hvdc.iter().map(|link| link.power_mw).sum::<f64>());
        let baseline_load = demand - wind - solar;
        let scenario_load =
            demand * input.scales.demand - wind * input.scales.wind - solar * input.scales.solar;
        let baseline = balance(
            baseline_load,
            generation,
            input.ess.as_ref().zip(baseline_energy),
        )?;
        let scenario = balance(
            scenario_load,
            generation,
            input.ess.as_ref().zip(scenario_energy),
        )?;
        baseline_energy = baseline.energy_mwh;
        scenario_energy = scenario.energy_mwh;
        result.points.push(Point {
            observed_at: time,
            baseline,
            scenario,
        });
    }
    result.final_baseline_mwh = baseline_energy;
    result.final_scenario_mwh = scenario_energy;
    Ok(result)
}

fn balance(
    net_load_mw: f64,
    generation: Option<f64>,
    ess: Option<(&EssConfig, f64)>,
) -> Result<Balance, SimulationError> {
    let mut result = Balance {
        net_load_mw,
        residual_before_ess_mw: generation.map(|value| net_load_mw - value),
        charge_mw: None,
        discharge_mw: None,
        energy_mwh: None,
        soc_percent: None,
        residual_after_ess_mw: generation.map(|value| net_load_mw - value),
    };
    if let (Some(residual), Some((ess, energy))) = (result.residual_before_ess_mw, ess) {
        let (charge, discharge) = if residual > 0.0 {
            (
                0.0,
                residual
                    .min(ess.discharge_limit_mw)
                    .min((energy - ess.min_mwh) * ess.discharge_efficiency / STEP_HOURS),
            )
        } else {
            (
                (-residual)
                    .min(ess.charge_limit_mw)
                    .min((ess.max_mwh - energy) / (ess.charge_efficiency * STEP_HOURS)),
                0.0,
            )
        };
        let next = energy + ess.charge_efficiency * charge * STEP_HOURS
            - discharge * STEP_HOURS / ess.discharge_efficiency;
        if !next.is_finite() {
            return Err(SimulationError::NumericRange);
        }
        let next = next.clamp(ess.min_mwh, ess.max_mwh);
        result.charge_mw = Some(charge);
        result.discharge_mw = Some(discharge);
        result.energy_mwh = Some(next);
        result.soc_percent = Some(next / ess.capacity_mwh * 100.0);
        result.residual_after_ess_mw = Some(residual + charge - discharge);
    }
    if ![
        Some(result.net_load_mw),
        result.residual_before_ess_mw,
        result.charge_mw,
        result.discharge_mw,
        result.energy_mwh,
        result.soc_percent,
        result.residual_after_ess_mw,
    ]
    .into_iter()
    .flatten()
    .all(f64::is_finite)
    {
        return Err(SimulationError::NumericRange);
    }
    Ok(result)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod tests;
