#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

mod cache;
mod coalescing;
mod http;
mod websocket;

use axum::{Json, Router, http::StatusCode, routing::get};
use serde_json::{Value, json};
use tokio::{sync::watch, task::JoinSet};

use super::*;

type Reply = (StatusCode, Value);

fn observation(at: &str) -> Value {
    json!({"schema_version":1,"observed_at":at,"source":protocol::SOURCE,
        "source_timezone":"Asia/Seoul","quality_flags":["solar_mw:missing"],
        "demand_mw":88.0,"supply_capacity_mw":200.0,"wind_mw":0.0,
        "solar_mw":null,"renewable_total_mw":0.0})
}

fn geography() -> Value {
    json!({"type":"FeatureCollection","schema_version":1,"source":"energy-hub-db.public",
        "generated_at":"2026-09-29T00:00:00Z", "features":[{"type":"Feature",
        "id":"hub:power_line:42","geometry":{"type":"LineString","coordinates":[[126.5,33.4],[126.4,34.3]]},
        "properties":{"source_id":42,"facility_kind":"power_line","coordinate_system":"EPSG:4326",
        "quality_flags":["electrical_topology_unavailable"],"voltage":154_000}}]})
}

fn endpoint(receiver: watch::Receiver<Reply>) -> axum::routing::MethodRouter {
    get(move || {
        let receiver = receiver.clone();
        async move {
            let (status, value) = receiver.borrow().clone();
            (status, Json(value))
        }
    })
}

async fn start(tasks: &mut JoinSet<()>, router: Router) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    tasks.spawn(async move {
        axum::serve(listener, router).await.unwrap();
    });
    url
}

async fn app(tasks: &mut JoinSet<()>, upstream: Router, redis: &str) -> (String, Bridge) {
    let base = start(tasks, upstream).await;
    let settings = Settings::parse(|key| match key {
        "BRIDGE_BASE_URL" => Some(base.clone()),
        "REDIS_URL" => Some(redis.into()),
        _ => None,
    })
    .unwrap();
    let probe = Probe::new(&settings).unwrap();
    let bridge = Bridge::new(&settings, probe.clone()).unwrap();
    let url = start(
        tasks,
        router(bridge.clone()).merge(crate::health::router(probe)),
    )
    .await;
    (url, bridge)
}

#[tokio::test]
async fn redis_failure_keeps_gis_and_nulls_intact() {
    cache_scenario("redis://127.0.0.1:1/0", false).await;
}

#[tokio::test]
#[ignore = "requires dedicated TEST_REDIS_URL"]
async fn gis_cache_hit_miss_and_source_failure_use_real_redis() {
    cache_scenario(
        &std::env::var("TEST_REDIS_URL").expect("TEST_REDIS_URL"),
        true,
    )
    .await;
}

async fn cache_scenario(redis: &str, available: bool) {
    let (health, health_rx) = watch::channel((
        StatusCode::OK,
        json!({"status":"ready","hub_ready":true,"demand_ready":true}),
    ));
    let (assets, assets_rx) = watch::channel((StatusCode::OK, geography()));
    let upstream = Router::new()
        .route("/api/v1/health", endpoint(health_rx))
        .route("/api/v1/jeju/assets", endpoint(assets_rx));
    let mut tasks = JoinSet::new();
    let (base, bridge) = app(&mut tasks, upstream, redis).await;
    let client = reqwest::Client::new();
    let url = format!("{base}/api/v1/jeju/assets");
    let response = client.get(&url).send().await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response.headers()["x-cache"],
        if available { "miss" } else { "bypass" }
    );
    assert_eq!(response.json::<Value>().await.unwrap(), geography());
    assets.send_replace((
        StatusCode::SERVICE_UNAVAILABLE,
        json!({"error":"source_unavailable"}),
    ));
    let response = client.get(&url).send().await.unwrap();
    if available {
        assert_eq!(response.headers()["x-cache"], "hit");
        assert_eq!(response.json::<Value>().await.unwrap(), geography());
    } else {
        assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    }
    health.send_replace((
        StatusCode::SERVICE_UNAVAILABLE,
        json!({"status":"unavailable"}),
    ));
    assert_eq!(
        client.get(&url).send().await.unwrap().status(),
        StatusCode::SERVICE_UNAVAILABLE
    );
    if available {
        let mut connection = bridge
            .redis
            .get_multiplexed_async_connection()
            .await
            .unwrap();
        let ttl: i64 = redis::cmd("TTL")
            .arg(&*bridge.cache_key)
            .query_async(&mut connection)
            .await
            .unwrap();
        assert!((1..=3600).contains(&ttl));
        let _: u64 = redis::cmd("DEL")
            .arg(&*bridge.cache_key)
            .query_async(&mut connection)
            .await
            .unwrap();
    }
}

#[test]
fn delay_begins_at_exactly_fifteen_minutes() {
    let mut point: protocol::Snapshot =
        serde_json::from_value(observation("2026-09-29T00:00:00Z")).unwrap();
    for (seconds, expected) in [
        (899, None),
        (900, Some("source_delayed")),
        (-1, Some("source_in_future")),
        (0, None),
    ] {
        point.refresh_delay(point.observed_at + chrono::Duration::seconds(seconds));
        assert_eq!(
            point.quality_flags.contains(&"source_delayed".into()),
            expected == Some("source_delayed")
        );
        assert_eq!(
            point.quality_flags.contains(&"source_in_future".into()),
            expected == Some("source_in_future")
        );
        assert!(point.quality_flags.contains(&"solar_mw:missing".into()));
    }
}

#[test]
fn malformed_protocol_is_rejected_at_boundary() {
    let mut value = observation("2026-09-29T00:00:00Z");
    value.as_object_mut().unwrap().remove("demand_mw");
    assert!(serde_json::from_value::<protocol::Snapshot>(value).is_err());
    let mut assets = geography();
    assets["features"][0]["id"] = json!("invented:42");
    assert!(protocol::validate_assets(&serde_json::to_vec(&assets).unwrap()).is_err());
    let mut assets = geography();
    assets["features"][0]["geometry"]["coordinates"] = json!(["bad"]);
    assert!(protocol::validate_assets(&serde_json::to_vec(&assets).unwrap()).is_err());
    for geometry in [
        json!({"type":"LineString","coordinates":[[126.5,33.4]]}),
        json!({"type":"Polygon","coordinates":[[[126.0,33.0],[126.1,33.0],[126.1,33.1],[126.0,33.1]]]}),
        json!({"type":"Point","coordinates":[126.0,93.0]}),
    ] {
        let mut assets = geography();
        assets["features"][0]["geometry"] = geometry;
        assert!(protocol::validate_assets(&serde_json::to_vec(&assets).unwrap()).is_err());
    }
}
