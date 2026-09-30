use super::{SimulationError, SimulationInput};
use SimulationError::Invalid;
use chrono::{DateTime, Timelike, Utc};
use std::collections::BTreeSet;

pub(super) fn validate(input: &SimulationInput) -> Result<(), SimulationError> {
    let duration = input.end.signed_duration_since(input.start).num_seconds();
    if !(300..=86400).contains(&duration) || !aligned(input.start) || !aligned(input.end) {
        return Err(Invalid(
            "start/end must be on a 5-minute grid, within 24 hours",
        ));
    }
    if input.run_id.trim().is_empty() || input.source_version.trim().is_empty() {
        return Err(Invalid("run_id and source_version are required"));
    }
    if ![input.scales.demand, input.scales.wind, input.scales.solar]
        .into_iter()
        .all(nonnegative)
    {
        return Err(Invalid("scales must be finite and nonnegative"));
    }
    check_times(input, input.snapshots.iter().map(|point| point.observed_at))?;
    for point in &input.snapshots {
        if point.schema_version != 1 || point.source.trim().is_empty() {
            return Err(Invalid("snapshot requires schema_version=1 and source"));
        }
        if ![
            point.demand_mw,
            point.wind_mw,
            point.solar_mw,
            point.supply_capacity_mw,
            point.renewable_total_mw,
        ]
        .into_iter()
        .flatten()
        .all(nonnegative)
        {
            return Err(Invalid("snapshot MW values must be finite and nonnegative"));
        }
    }
    if let Some(dispatch) = &input.dispatch {
        check_times(input, dispatch.iter().map(|point| point.observed_at))?;
        let mut first_ids = None;
        for point in dispatch {
            let ids: BTreeSet<&str> = point.hvdc.iter().map(|link| link.id.as_str()).collect();
            if ids.len() != 3 || ids.iter().any(|id| id.trim().is_empty()) {
                return Err(Invalid("three distinct HVDC IDs are required"));
            }
            if let Some(expected) = &first_ids {
                if expected != &ids {
                    return Err(Invalid("HVDC IDs must be stable across intervals"));
                }
            } else {
                first_ids = Some(ids);
            }
            if !nonnegative(point.nonrenewable_mw) {
                return Err(Invalid("nonrenewable_mw must be finite and nonnegative"));
            }
            for link in &point.hvdc {
                if ![link.power_mw, link.min_mw, link.max_mw]
                    .into_iter()
                    .all(f64::is_finite)
                    || link.min_mw > link.max_mw
                    || (link.available && !(link.min_mw..=link.max_mw).contains(&link.power_mw))
                    || (!link.available && link.power_mw.abs() > 0.0)
                {
                    return Err(Invalid(
                        "HVDC flow must respect bounds; unavailable links require zero MW",
                    ));
                }
            }
        }
    }
    if let Some(ess) = &input.ess {
        if input.dispatch.is_none() {
            return Err(Invalid("ESS requires explicit G/H dispatch"));
        }
        if ![
            ess.capacity_mwh,
            ess.charge_limit_mw,
            ess.discharge_limit_mw,
            ess.initial_mwh,
            ess.min_mwh,
            ess.max_mwh,
        ]
        .into_iter()
        .all(nonnegative)
            || ess.capacity_mwh <= 0.0
            || ess.min_mwh > ess.initial_mwh
            || ess.initial_mwh > ess.max_mwh
            || ess.max_mwh > ess.capacity_mwh
        {
            return Err(Invalid(
                "ESS energy bounds/capacity/power limits are invalid",
            ));
        }
        if ![ess.charge_efficiency, ess.discharge_efficiency]
            .into_iter()
            .all(|value| value.is_finite() && value * super::STEP_HOURS > 0.0 && value <= 1.0)
        {
            return Err(Invalid("ESS efficiencies must be in (0, 1]"));
        }
    }
    Ok(())
}

fn check_times(
    input: &SimulationInput,
    times: impl Iterator<Item = DateTime<Utc>>,
) -> Result<(), SimulationError> {
    let mut seen = BTreeSet::new();
    for time in times {
        if !aligned(time) || time < input.start || time >= input.end || !seen.insert(time) {
            return Err(Invalid(
                "timestamps must be unique 5-minute intervals in [start, end)",
            ));
        }
    }
    Ok(())
}

fn aligned(time: DateTime<Utc>) -> bool {
    time.timestamp().rem_euclid(300) == 0 && time.nanosecond() == 0
}

fn nonnegative(value: f64) -> bool {
    value.is_finite() && value >= 0.0
}
