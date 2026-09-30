use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SimulationInput {
    pub run_id: String,
    pub source_version: String,
    pub start: DateTime<Utc>,
    pub end: DateTime<Utc>,
    pub scales: Scales,
    pub snapshots: Vec<Snapshot>,
    pub dispatch: Option<Vec<Dispatch>>,
    pub ess: Option<EssConfig>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Scales {
    pub demand: f64,
    pub wind: f64,
    pub solar: f64,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Snapshot {
    pub schema_version: u8,
    pub observed_at: DateTime<Utc>,
    pub source: String,
    pub quality_flags: Vec<String>,
    pub demand_mw: Option<f64>,
    pub wind_mw: Option<f64>,
    pub solar_mw: Option<f64>,
    pub supply_capacity_mw: Option<f64>,
    pub renewable_total_mw: Option<f64>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Dispatch {
    pub observed_at: DateTime<Utc>,
    pub nonrenewable_mw: f64,
    pub hvdc: [Hvdc; 3],
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Hvdc {
    pub id: String,
    pub power_mw: f64,
    pub available: bool,
    pub min_mw: f64,
    pub max_mw: f64,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct EssConfig {
    pub capacity_mwh: f64,
    pub charge_limit_mw: f64,
    pub discharge_limit_mw: f64,
    pub charge_efficiency: f64,
    pub discharge_efficiency: f64,
    pub initial_mwh: f64,
    pub min_mwh: f64,
    pub max_mwh: f64,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Status {
    Complete,
    NetLoadOnly,
    Incomplete,
}

#[derive(Debug, Serialize)]
pub(crate) struct SimulationResult<'a> {
    pub model_version: &'static str,
    pub data_kind: &'static str,
    pub assumptions: [&'static str; 3],
    pub input: &'a SimulationInput,
    pub status: Status,
    pub missing_intervals: Vec<DateTime<Utc>>,
    pub points: Vec<Point>,
    pub final_baseline_mwh: Option<f64>,
    pub final_scenario_mwh: Option<f64>,
}

#[derive(Debug, Serialize)]
pub(crate) struct Point {
    pub observed_at: DateTime<Utc>,
    pub baseline: Balance,
    pub scenario: Balance,
}

#[derive(Debug, Serialize)]
pub(crate) struct Balance {
    pub net_load_mw: f64,
    pub residual_before_ess_mw: Option<f64>,
    pub charge_mw: Option<f64>,
    pub discharge_mw: Option<f64>,
    pub energy_mwh: Option<f64>,
    pub soc_percent: Option<f64>,
    pub residual_after_ess_mw: Option<f64>,
}

#[derive(Debug, Error)]
pub(crate) enum SimulationError {
    #[error("invalid simulation input: {0}")]
    Invalid(&'static str),
    #[error("simulation arithmetic exceeds finite numeric range")]
    NumericRange,
}
