#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

use super::*;
use axum::{extract::Query, routing::get};
use serde_json::{Value, json};
use std::sync::atomic::{AtomicUsize, Ordering};
use tokio::{sync::watch, task::JoinSet};

struct Fixture {
    _tasks: JoinSet<()>,
    url: String,
    service: Service,
    source: watch::Sender<(StatusCode, Vec<Value>)>,
    reads: Arc<AtomicUsize>,
}

fn input() -> Value {
    let mut value: Value =
        serde_json::from_str(include_str!("../../../examples/scenario.json")).unwrap();
    value.as_object_mut().unwrap().remove("snapshots");
    value.as_object_mut().unwrap().remove("source_version");
    value
}

async fn listen(tasks: &mut JoinSet<()>, app: Router) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    tasks.spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    base
}

impl Fixture {
    async fn new() -> Self {
        let sample: Value =
            serde_json::from_str(include_str!("../../../examples/scenario.json")).unwrap();
        let mut snapshots = sample["snapshots"].as_array().unwrap().clone();
        for snapshot in &mut snapshots {
            snapshot["source"] = json!("demand-postgres.public.jeju_supply_demand");
            snapshot["source_timezone"] = json!("Asia/Seoul");
        }
        let (source, rx) = watch::channel((StatusCode::OK, snapshots));
        let reads = Arc::new(AtomicUsize::new(0));
        let counter = Arc::clone(&reads);
        let upstream = Router::new().route(
            "/api/v1/jeju/state",
            get(
                move |Query(query): Query<std::collections::HashMap<String, String>>| {
                    let rx = rx.clone();
                    let counter = Arc::clone(&counter);
                    async move {
                        counter.fetch_add(1, Ordering::SeqCst);
                        let at = DateTime::parse_from_rfc3339(&query["at"]).unwrap().to_utc();
                        let (status, values) = rx.borrow().clone();
                        if status != StatusCode::OK {
                            return (status, Json(json!({"secret":"never leak"})));
                        }
                        let value = values.into_iter().find(|v| {
                            DateTime::parse_from_rfc3339(v["observed_at"].as_str().unwrap())
                                .unwrap()
                                .to_utc()
                                == at
                        });
                        match value {
                            Some(value) => (StatusCode::OK, Json(value)),
                            None => (
                                StatusCode::NOT_FOUND,
                                Json(json!({"error":"observation_not_found"})),
                            ),
                        }
                    }
                },
            ),
        );
        let mut tasks = JoinSet::new();
        let base = listen(&mut tasks, upstream).await;
        let settings = crate::config::Settings::parse(|key| match key {
            "BRIDGE_BASE_URL" => Some(base.clone()),
            "REDIS_URL" => Some("redis://127.0.0.1:1/0".into()),
            _ => None,
        })
        .unwrap();
        let bridge = Bridge::new(&settings, crate::health::Probe::new(&settings).unwrap()).unwrap();
        let service = Service {
            bridge,
            slots: Arc::new(Semaphore::new(2)),
        };
        let url = listen(&mut tasks, routes(service.clone())).await + "/api/v1/jeju/simulate";
        Self {
            _tasks: tasks,
            url,
            service,
            source,
            reads,
        }
    }

    async fn post(&self, value: &Value) -> reqwest::Response {
        reqwest::Client::new()
            .post(&self.url)
            .json(value)
            .send()
            .await
            .unwrap()
    }
}

#[tokio::test]
async fn api_fetches_exact_observations_and_returns_ess_provenance() {
    let fixture = Fixture::new().await;
    let response = fixture.post(&input()).await;
    assert_eq!(response.status(), StatusCode::OK);
    let body: Value = response.json().await.unwrap();
    assert_eq!(body["data_kind"], "scenario");
    assert_eq!(body["status"], "complete");
    assert!((body["final_scenario_mwh"].as_f64().unwrap() - 4.65).abs() < 1e-9);
    assert_eq!(fixture.reads.load(Ordering::SeqCst), 2);
    assert_eq!(
        body["input"]["snapshots"][0]["source"],
        "demand-postgres.public.jeju_supply_demand"
    );
    assert_eq!(
        body["acquisition"]["consistency"],
        "individual_uncached_http_reads"
    );
    assert_eq!(body["input"]["source_version"].as_str().unwrap().len(), 71);
    let mut scaled = input();
    scaled["scales"] = json!({"demand":1.1,"wind":0.8,"solar":1.0});
    let changed: Value = fixture.post(&scaled).await.json().await.unwrap();
    assert!((changed["final_scenario_mwh"].as_f64().unwrap() - 3.083_333_333_333_333).abs() < 1e-9);
    assert_eq!(
        changed["input"]["source_version"],
        body["input"]["source_version"]
    );
    assert_eq!(
        fixture.reads.load(Ordering::SeqCst),
        4,
        "simulation must bypass observation cache"
    );
}

#[tokio::test]
async fn missing_and_null_profiles_stay_incomplete_and_source_failure_is_503() {
    let fixture = Fixture::new().await;
    let original = fixture.source.borrow().1.clone();
    for values in [original[..1].to_vec(), {
        let mut values = original.clone();
        values[1]["solar_mw"] = Value::Null;
        values
    }] {
        fixture.source.send_replace((StatusCode::OK, values));
        let body: Value = fixture.post(&input()).await.json().await.unwrap();
        assert_eq!(body["status"], "incomplete");
        assert!(body["points"].as_array().unwrap().is_empty());
        assert_eq!(body["missing_intervals"].as_array().unwrap().len(), 1);
        assert!(body["final_scenario_mwh"].is_null());
    }
    let mut invalid = original;
    invalid[0]["demand_mw"] = json!(-1.0);
    fixture.source.send_replace((StatusCode::OK, invalid));
    assert_eq!(
        fixture.post(&input()).await.status(),
        StatusCode::BAD_GATEWAY
    );
    fixture
        .source
        .send_replace((StatusCode::SERVICE_UNAVAILABLE, Vec::new()));
    let response = fixture.post(&input()).await;
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert!(!response.text().await.unwrap().contains("secret"));
}

#[tokio::test]
async fn invalid_or_oversized_requests_never_read_source() {
    let fixture = Fixture::new().await;
    for (field, value) in [
        ("snapshots", json!([])),
        ("start", json!("2026-01-01T00:00:01Z")),
        ("dispatch", Value::Null),
        ("scales", json!({"demand":-1,"wind":1,"solar":1})),
    ] {
        let mut request = input();
        request[field] = value;
        assert_eq!(
            fixture.post(&request).await.status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
    }
    let mut large = input();
    large["run_id"] = json!("x".repeat(262_144));
    assert_eq!(
        fixture.post(&large).await.status(),
        StatusCode::PAYLOAD_TOO_LARGE
    );
    assert_eq!(fixture.reads.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn saturated_runs_return_429_then_release_capacity() {
    let fixture = Fixture::new().await;
    let held = Arc::clone(&fixture.service.slots)
        .acquire_many_owned(2)
        .await
        .unwrap();
    assert_eq!(
        fixture.post(&input()).await.status(),
        StatusCode::TOO_MANY_REQUESTS
    );
    assert_eq!(fixture.reads.load(Ordering::SeqCst), 0);
    drop(held);
    assert_eq!(fixture.post(&input()).await.status(), StatusCode::OK);
    assert_eq!(fixture.service.slots.available_permits(), 2);
}
