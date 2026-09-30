mod cache;
mod http;
mod protocol;
mod stream;
#[cfg(test)]
mod tests;

use std::{
    sync::{Arc, atomic::AtomicU64},
    time::Duration,
};

use axum::{
    Json, Router,
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
};
use tokio::sync::{Mutex, Semaphore, watch};

use crate::{config::Settings, health::Probe};
use protocol::Envelope;
pub(crate) use protocol::Snapshot as Observation;

#[derive(Clone, Copy, Debug, thiserror::Error)]
pub(crate) enum Error {
    #[error("invalid_query")]
    InvalidQuery,
    #[error("observation_not_found")]
    NotFound,
    #[error("bridge_unavailable")]
    Unavailable,
    #[error("invalid_bridge_response")]
    InvalidResponse,
}

impl IntoResponse for Error {
    fn into_response(self) -> Response {
        let status = match self {
            Self::InvalidQuery => StatusCode::UNPROCESSABLE_ENTITY,
            Self::NotFound => StatusCode::NOT_FOUND,
            Self::Unavailable => StatusCode::SERVICE_UNAVAILABLE,
            Self::InvalidResponse => StatusCode::BAD_GATEWAY,
        };
        (status, Json(serde_json::json!({"error": self.to_string()}))).into_response()
    }
}

#[derive(Clone)]
pub(crate) struct Bridge {
    http: reqwest::Client,
    base: Option<reqwest::Url>,
    probe: Probe,
    #[cfg(test)]
    redis: redis::Client,
    assets_flight: Arc<Mutex<Option<http::AssetsFlight>>>,
    cache_key: Arc<str>,
    cache_epoch: Arc<AtomicU64>,
    cache_session: i64,
    live: watch::Sender<Envelope>,
    stop: watch::Sender<bool>,
    allowed_origins: Arc<[String]>,
    ws_slots: Arc<Semaphore>,
}

impl Bridge {
    pub(crate) async fn observation_at(
        &self,
        at: chrono::DateTime<chrono::Utc>,
    ) -> Result<Observation, Error> {
        http::observation_at(self, at).await
    }

    pub(crate) fn new(settings: &Settings, probe: Probe) -> Result<Self, reqwest::Error> {
        let base = settings.bridge_health_url.clone();
        let mut hash = std::collections::hash_map::DefaultHasher::new();
        std::hash::Hash::hash(&base, &mut hash);
        let cache_key = format!("edt:dev:v1:assets:{:x}", std::hash::Hasher::finish(&hash)).into();
        Ok(Self {
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(15))
                .redirect(reqwest::redirect::Policy::none())
                .no_proxy()
                .build()?,
            base,
            probe,
            #[cfg(test)]
            redis: settings.redis.clone(),
            assets_flight: Arc::new(Mutex::new(None)),
            cache_key,
            cache_epoch: Arc::new(AtomicU64::new(0)),
            cache_session: chrono::Utc::now().timestamp_micros(),
            live: watch::channel(Envelope::unavailable()).0,
            stop: watch::channel(false).0,
            allowed_origins: settings.allowed_origins.clone().into(),
            ws_slots: Arc::new(Semaphore::new(32)),
        })
    }

    fn url(&self, path: &str) -> Result<reqwest::Url, Error> {
        let mut url = self.base.clone().ok_or(Error::Unavailable)?;
        url.set_path(path);
        Ok(url)
    }

    pub(crate) fn stop(&self) {
        self.stop.send_replace(true);
    }

    pub(crate) async fn drain_subscribers(&self) {
        self.stop();
        // on_upgrade tasks outlive axum::serve; keep the runtime alive for their Close frames.
        if !matches!(
            tokio::time::timeout(
                Duration::from_secs(11),
                Arc::clone(&self.ws_slots).acquire_many_owned(32)
            )
            .await,
            Ok(Ok(_))
        ) {
            tracing::warn!("subscriber shutdown deadline exceeded");
        }
    }

    pub(crate) async fn run(self) {
        let mut stop = self.stop.subscribe();
        if *stop.borrow_and_update() {
            return;
        }
        tokio::select! {
            () = stream::receive(self.clone()) => {},
            _ = stop.changed() => {},
        }
    }
}

pub(crate) fn router(bridge: Bridge) -> Router {
    Router::new()
        .route("/api/v1/jeju/state", get(http::state))
        .route("/api/v1/jeju/timeline", get(http::timeline))
        .route("/api/v1/jeju/assets", get(http::assets))
        .route("/api/v1/jeju/ws", get(stream::upgrade))
        .with_state(bridge)
}
