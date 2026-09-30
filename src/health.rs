use std::{sync::Arc, time::Duration};

use axum::{Json, Router, extract::State, http::StatusCode, routing::get};
use serde::{Deserialize, Serialize};
use tokio::sync::OnceCell;

use crate::config::Settings;

const PROBE_TIMEOUT: Duration = Duration::from_secs(3);
const MAX_HEALTH_BYTES: usize = 16 * 1024;

#[derive(Clone)]
pub(crate) struct Probe {
    http: reqwest::Client,
    bridge_health_url: Option<reqwest::Url>,
    redis: redis::Client,
    redis_connection: Arc<OnceCell<redis::aio::ConnectionManager>>,
}

impl Probe {
    pub(crate) async fn bridge_ready(&self) -> bool {
        self.bridge().await == Dependency::Ok
    }

    pub(crate) fn new(settings: &Settings) -> Result<Self, reqwest::Error> {
        Ok(Self {
            http: reqwest::Client::builder()
                .timeout(PROBE_TIMEOUT)
                .redirect(reqwest::redirect::Policy::none())
                .no_proxy()
                .build()?,
            bridge_health_url: settings.bridge_health_url.clone(),
            redis: settings.redis.clone(),
            redis_connection: Arc::new(OnceCell::new()),
        })
    }

    pub(crate) async fn redis_connection(
        &self,
    ) -> redis::RedisResult<redis::aio::ConnectionManager> {
        self.redis_connection
            .get_or_try_init(|| {
                self.redis.get_connection_manager_with_config(
                    redis::aio::ConnectionManagerConfig::new()
                        .set_connection_timeout(Duration::from_secs(1))
                        .set_response_timeout(Duration::from_secs(1)),
                )
            })
            .await
            .cloned()
    }

    async fn bridge(&self) -> Dependency {
        let Some(url) = &self.bridge_health_url else {
            return Dependency::NotConfigured;
        };
        let result = async {
            let mut response = self.http.get(url.clone()).send().await.ok()?;
            if !response.status().is_success() {
                return None;
            }
            let mut bytes = Vec::new();
            while let Some(chunk) = response.chunk().await.ok()? {
                if bytes.len().saturating_add(chunk.len()) > MAX_HEALTH_BYTES {
                    return None;
                }
                bytes.extend_from_slice(&chunk);
            }
            let health: BridgeHealth = serde_json::from_slice(&bytes).ok()?;
            match health.status {
                BridgeStatus::Ready if health.hub_ready && health.demand_ready => {
                    Some(Dependency::Ok)
                }
                BridgeStatus::Ready | BridgeStatus::Unavailable => None,
            }
        };
        match tokio::time::timeout(PROBE_TIMEOUT, result).await {
            Ok(Some(status)) => status,
            Ok(None) | Err(_) => Dependency::Unavailable,
        }
    }

    async fn cache(&self) -> Dependency {
        let ping = async {
            let mut connection = self.redis_connection().await?;
            redis::cmd("PING")
                .query_async::<String>(&mut connection)
                .await
        };
        match tokio::time::timeout(PROBE_TIMEOUT, ping).await {
            Ok(Ok(reply)) if reply == "PONG" => Dependency::Ok,
            _ => Dependency::Unavailable,
        }
    }
}

#[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum Status {
    Ok,
    Degraded,
    Unavailable,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum Dependency {
    Ok,
    Unavailable,
    NotConfigured,
}

#[derive(Deserialize)]
struct BridgeHealth {
    status: BridgeStatus,
    hub_ready: bool,
    demand_ready: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum BridgeStatus {
    Ready,
    Unavailable,
}

#[derive(Serialize)]
struct Health {
    schema_version: u8,
    status: Status,
    bridge: Dependency,
    redis: Dependency,
}

pub(crate) fn router(probe: Probe) -> Router {
    Router::new()
        .route(
            "/health/live",
            get(|| async { Json(serde_json::json!({"status": "ok"})) }),
        )
        .route("/api/v1/health", get(readiness))
        .with_state(probe)
}

async fn readiness(State(probe): State<Probe>) -> (StatusCode, Json<Health>) {
    let (bridge, redis) = tokio::join!(probe.bridge(), probe.cache());
    let (code, status) = match (&bridge, &redis) {
        (Dependency::Unavailable | Dependency::NotConfigured, _) => {
            (StatusCode::SERVICE_UNAVAILABLE, Status::Unavailable)
        }
        (Dependency::Ok, Dependency::Ok) => (StatusCode::OK, Status::Ok),
        (Dependency::Ok, _) => (StatusCode::OK, Status::Degraded),
    };
    (
        code,
        Json(Health {
            schema_version: 1,
            status,
            bridge,
            redis,
        }),
    )
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod tests {
    use super::*;
    use crate::config::Settings;

    #[tokio::test]
    async fn missing_bridge_is_unavailable_but_process_is_live() {
        let settings = Settings::parse(|_| None).unwrap();
        let app = router(Probe::new(&settings).unwrap());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move { axum::serve(listener, app).await });
        let client = reqwest::Client::new();
        let live = client
            .get(format!("{base}/health/live"))
            .send()
            .await
            .unwrap();
        let ready = client
            .get(format!("{base}/api/v1/health"))
            .send()
            .await
            .unwrap();
        assert_eq!(live.status(), StatusCode::OK);
        assert_eq!(ready.status(), StatusCode::SERVICE_UNAVAILABLE);
        let body: serde_json::Value = ready.json().await.unwrap();
        assert_eq!(body["bridge"], "not_configured");
        assert_eq!(body["status"], "unavailable");
        server.abort();
    }

    #[tokio::test]
    async fn bridge_success_with_failed_cache_is_degraded() {
        let bridge = Router::new().route(
            "/api/v1/health",
            get(|| async {
                Json(
                    serde_json::json!({"status": "ready", "hub_ready": true, "demand_ready": true}),
                )
            }),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move { axum::serve(listener, bridge).await });
        let settings = Settings::parse(|key| match key {
            "BRIDGE_BASE_URL" => Some(address.clone()),
            "REDIS_URL" => Some("redis://127.0.0.1:1/0".into()),
            _ => None,
        })
        .unwrap();
        let (status, Json(body)) = readiness(State(Probe::new(&settings).unwrap())).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body.status, Status::Degraded);
        assert_eq!(body.redis, Dependency::Unavailable);
        server.abort();
    }

    #[tokio::test]
    async fn invalid_bridge_health_never_becomes_ready() {
        for (code, body) in [
            (
                StatusCode::SERVICE_UNAVAILABLE,
                r#"{"status":"ok"}"#.to_owned(),
            ),
            (StatusCode::OK, r#"{"status":"unavailable"}"#.to_owned()),
            (StatusCode::OK, r#"{"status":"unknown"}"#.to_owned()),
            (
                StatusCode::OK,
                r#"{"status":"ready","hub_ready":false,"demand_ready":true}"#.to_owned(),
            ),
            (StatusCode::OK, r#"{"status":"ready"}"#.to_owned()),
            (StatusCode::OK, "not JSON".to_owned()),
            (StatusCode::OK, " ".repeat(MAX_HEALTH_BYTES + 1)),
        ] {
            let bridge =
                Router::new().route("/api/v1/health", get(move || async move { (code, body) }));
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let address = format!("http://{}", listener.local_addr().unwrap());
            let server = tokio::spawn(async move { axum::serve(listener, bridge).await });
            let settings =
                Settings::parse(|key| (key == "BRIDGE_BASE_URL").then(|| address.clone())).unwrap();
            assert_eq!(
                Probe::new(&settings).unwrap().bridge().await,
                Dependency::Unavailable
            );
            server.abort();
        }
    }

    #[tokio::test]
    async fn stalled_bridge_is_bounded_by_timeout() {
        let bridge = Router::new().route(
            "/api/v1/health",
            get(|| async { std::future::pending::<StatusCode>().await }),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move { axum::serve(listener, bridge).await });
        let settings =
            Settings::parse(|key| (key == "BRIDGE_BASE_URL").then(|| address.clone())).unwrap();
        let probe = Probe::new(&settings).unwrap();
        let result = tokio::time::timeout(Duration::from_secs(4), probe.bridge())
            .await
            .unwrap();
        assert_eq!(result, Dependency::Unavailable);
        server.abort();
    }

    #[tokio::test]
    #[ignore = "requires a dedicated real Redis at TEST_REDIS_URL"]
    async fn cache_ping_uses_real_redis() {
        let url = std::env::var("TEST_REDIS_URL").expect("set TEST_REDIS_URL");
        let settings = Settings::parse(|key| (key == "REDIS_URL").then(|| url.clone())).unwrap();
        assert_eq!(Probe::new(&settings).unwrap().cache().await, Dependency::Ok);
    }
}
