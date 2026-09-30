use std::{
    hash::{Hash, Hasher},
    sync::atomic::Ordering,
};

use super::*;

#[tokio::test]
#[ignore = "requires dedicated TEST_REDIS_URL"]
async fn observation_cache_normalizes_offsets_and_invalidates_on_correction() {
    let ready = watch::channel((
        StatusCode::OK,
        json!({"status":"ready","hub_ready":true,"demand_ready":true}),
    ));
    let (states, states_rx) = watch::channel((StatusCode::OK, observation("2026-09-29T00:00:00Z")));
    let upstream = Router::new()
        .route("/api/v1/health", endpoint(ready.1))
        .route("/api/v1/jeju/state", endpoint(states_rx));
    let mut tasks = JoinSet::new();
    let redis = std::env::var("TEST_REDIS_URL").expect("TEST_REDIS_URL");
    let (base, bridge) = app(&mut tasks, upstream, &redis).await;
    bridge.live.send_modify(|live| {
        live.kind = protocol::MessageType::Snapshot;
    });
    let client = reqwest::Client::new();
    let url = format!("{base}/api/v1/jeju/state");
    let latest: Value = client.get(&url).send().await.unwrap().json().await.unwrap();
    assert_eq!(latest["demand_mw"], 88.0);
    let historical: Value = client
        .get(&url)
        .query(&[("at", "2026-09-29T09:00:00+09:00")])
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(historical["demand_mw"], 88.0);
    states.send_replace((
        StatusCode::SERVICE_UNAVAILABLE,
        json!({"error":"source_unavailable"}),
    ));
    assert_eq!(
        client.get(&url).send().await.unwrap().status(),
        StatusCode::OK
    );
    assert_eq!(
        client
            .get(&url)
            .query(&[("at", "2026-09-29T00:00:00Z")])
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::OK
    );
    let mut conn = bridge
        .redis
        .get_multiplexed_async_connection()
        .await
        .unwrap();
    let mut keys = Vec::new();
    for (path, ttl) in [
        ("/api/v1/jeju/state", 30),
        (
            "/api/v1/jeju/state?at=2026-09-29T00%3A00%3A00%2B00%3A00",
            300,
        ),
    ] {
        let mut source = bridge.base.clone().unwrap();
        source.set_path("/");
        let source = source.join(path).unwrap();
        let mut hash = std::collections::hash_map::DefaultHasher::new();
        source.hash(&mut hash);
        let key = format!(
            "{}:{}:0:{:x}",
            bridge.cache_key,
            bridge.cache_session,
            hash.finish()
        );
        let actual: i64 = redis::cmd("TTL")
            .arg(&key)
            .query_async(&mut conn)
            .await
            .unwrap();
        assert!((1..=ttl).contains(&actual), "TTL for {path}: {actual}");
        keys.push(key);
    }
    let mut correction = observation("2026-09-29T00:00:00Z");
    correction["demand_mw"] = json!(101.0);
    states.send_replace((StatusCode::OK, correction));
    bridge.cache_epoch.fetch_add(1, Ordering::SeqCst);
    let fresh: Value = client.get(&url).send().await.unwrap().json().await.unwrap();
    assert_eq!(fresh["demand_mw"], 101.0);
    ready.0.send_replace((
        StatusCode::SERVICE_UNAVAILABLE,
        json!({"status":"unavailable"}),
    ));
    assert_eq!(
        client.get(&url).send().await.unwrap().status(),
        StatusCode::SERVICE_UNAVAILABLE
    );
    keys.push(keys[0].replacen(":0:", ":1:", 1));
    let _: u64 = redis::cmd("DEL")
        .arg(keys)
        .query_async(&mut conn)
        .await
        .unwrap();
}
