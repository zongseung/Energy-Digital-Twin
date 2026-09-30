#[cfg(test)]
mod tests;

use std::{sync::Arc, time::Duration};

use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, State, rejection::JsonRejection},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
    routing::post,
};
use chrono::{DateTime, Utc};
use futures_util::{StreamExt, TryStreamExt, stream};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::sync::Semaphore;

use super::{
    Dispatch, EssConfig, Scales, SimulationInput, SimulationResult, Snapshot, simulate, validation,
};
use crate::bridge::{self, Bridge, Observation};

#[derive(Clone)]
struct Service {
    bridge: Bridge,
    slots: Arc<Semaphore>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    run_id: String,
    start: DateTime<Utc>,
    end: DateTime<Utc>,
    scales: Scales,
    dispatch: Option<Vec<Dispatch>>,
    ess: Option<EssConfig>,
}

#[derive(Serialize)]
struct Acquisition {
    started_at: DateTime<Utc>,
    finished_at: DateTime<Utc>,
    consistency: &'static str,
    source_timezone: &'static str,
}

#[derive(Serialize)]
struct Output<'a> {
    #[serde(flatten)]
    result: SimulationResult<'a>,
    acquisition: Acquisition,
}

#[derive(Debug, thiserror::Error)]
enum Error {
    #[error("invalid_simulation_request")]
    Body(StatusCode),
    #[error("{0}")]
    Input(#[from] super::SimulationError),
    #[error("{0}")]
    Bridge(#[from] bridge::Error),
    #[error("too_many_simulations")]
    Busy,
    #[error("invalid_simulation_source")]
    Source,
    #[error("simulation_failed")]
    Internal,
}

impl IntoResponse for Error {
    fn into_response(self) -> Response {
        let message = self.to_string();
        let status = match self {
            Self::Body(status) => status,
            Self::Input(_) => StatusCode::UNPROCESSABLE_ENTITY,
            Self::Bridge(error) => return error.into_response(),
            Self::Busy => StatusCode::TOO_MANY_REQUESTS,
            Self::Source => StatusCode::BAD_GATEWAY,
            Self::Internal => StatusCode::INTERNAL_SERVER_ERROR,
        };
        (status, Json(serde_json::json!({"error": message}))).into_response()
    }
}

pub(crate) fn router(bridge: Bridge) -> Router {
    routes(Service {
        bridge,
        slots: Arc::new(Semaphore::new(2)),
    })
}

fn routes(service: Service) -> Router {
    Router::new()
        .route(
            "/api/v1/jeju/simulate",
            post(run).layer(DefaultBodyLimit::max(256 * 1024)),
        )
        .with_state(service)
}

async fn run(
    State(service): State<Service>,
    request: Result<Json<Request>, JsonRejection>,
) -> Result<Response, Error> {
    let Json(request) = request.map_err(|error| {
        Error::Body(match error.status() {
            StatusCode::PAYLOAD_TOO_LARGE => StatusCode::PAYLOAD_TOO_LARGE,
            StatusCode::UNSUPPORTED_MEDIA_TYPE => StatusCode::UNSUPPORTED_MEDIA_TYPE,
            _ => StatusCode::UNPROCESSABLE_ENTITY,
        })
    })?;
    let mut input = SimulationInput {
        run_id: request.run_id,
        source_version: "pending".into(),
        start: request.start,
        end: request.end,
        scales: request.scales,
        snapshots: Vec::new(),
        dispatch: request.dispatch,
        ess: request.ess,
    };
    validation::validate(&input)?;
    let permit = Arc::clone(&service.slots)
        .try_acquire_owned()
        .map_err(|_| Error::Busy)?;
    let started_at = Utc::now();
    let times = std::iter::successors(Some(input.start), |time| {
        time.checked_add_signed(chrono::Duration::minutes(5))
            .filter(|next| *next < input.end)
    });
    let bridge = &service.bridge;
    let profile = stream::iter(times.map(|at| async move {
        match bridge.observation_at(at).await {
            Ok(point) => Ok(Some(point)),
            Err(bridge::Error::NotFound) => Ok(None),
            Err(error) => Err(error),
        }
    }))
    .buffer_unordered(2)
    .try_collect::<Vec<_>>();
    let observations = tokio::time::timeout(Duration::from_secs(30), profile)
        .await
        .map_err(|_| bridge::Error::Unavailable)??;
    input.snapshots = observations
        .into_iter()
        .flatten()
        .map(Snapshot::from)
        .collect();
    input.snapshots.sort_by_key(|point| point.observed_at);
    validation::validate(&input).map_err(|_| Error::Source)?;
    let acquisition = Acquisition {
        started_at,
        finished_at: Utc::now(),
        consistency: "individual_uncached_http_reads",
        source_timezone: "Asia/Seoul",
    };
    let bytes = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        let bytes = serde_json::to_vec(&input.snapshots).map_err(|_| Error::Internal)?;
        input.source_version = format!("sha256:{:x}", Sha256::digest(bytes));
        let result = simulate(&input)?;
        serde_json::to_vec(&Output {
            result,
            acquisition,
        })
        .map_err(|_| Error::Internal)
    })
    .await
    .map_err(|_| Error::Internal)??;
    Ok((
        [
            (header::CONTENT_TYPE, "application/json"),
            (header::CACHE_CONTROL, "no-store"),
        ],
        bytes,
    )
        .into_response())
}

impl From<Observation> for Snapshot {
    fn from(point: Observation) -> Self {
        Self {
            schema_version: point.schema_version,
            observed_at: point.observed_at,
            source: point.source,
            quality_flags: point.quality_flags,
            demand_mw: point.demand_mw,
            supply_capacity_mw: point.supply_capacity_mw,
            wind_mw: point.wind_mw,
            solar_mw: point.solar_mw,
            renewable_total_mw: point.renewable_total_mw,
        }
    }
}
