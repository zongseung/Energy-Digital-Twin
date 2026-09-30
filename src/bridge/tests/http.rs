use super::*;

#[tokio::test]
async fn http_preserves_observations_and_rejects_wrong_times_and_schemas() {
    let (state, state_rx) = watch::channel((StatusCode::OK, observation("2026-09-29T00:00:00Z")));
    let upstream = Router::new().route("/api/v1/jeju/state", endpoint(state_rx));
    let mut tasks = JoinSet::new();
    let (base, _) = app(&mut tasks, upstream, "redis://127.0.0.1:1/0").await;
    let client = reqwest::Client::new();
    let url = format!("{base}/api/v1/jeju/state");
    let response = client
        .get(&url)
        .query(&[("at", "2026-09-29T09:00:00+09:00")])
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body: Value = response.json().await.unwrap();
    assert_eq!(body, observation("2026-09-29T00:00:00Z"));
    let mut corrected = observation("2026-09-29T00:00:00Z");
    corrected["demand_mw"] = json!(99.0);
    state.send_replace((StatusCode::OK, corrected.clone()));
    let body: Value = client.get(&url).send().await.unwrap().json().await.unwrap();
    assert_eq!(body["demand_mw"], 99.0);
    assert_eq!(body["wind_mw"], 0.0);
    assert!(body["solar_mw"].is_null());
    assert!(
        body["quality_flags"]
            .as_array()
            .unwrap()
            .contains(&json!("source_delayed"))
    );
    let wrong = client
        .get(&url)
        .query(&[("at", "2026-09-29T00:05:00Z")])
        .send()
        .await
        .unwrap();
    assert_eq!(wrong.status(), StatusCode::BAD_GATEWAY);
    state.send_replace((StatusCode::OK, observation("2026-09-29T00:05:00Z")));
    assert_eq!(
        client
            .get(&url)
            .query(&[("at", "2026-09-29T00:05:00Z")])
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::OK
    );
    for at in ["2026-09-29T09:00:00", "invalid"] {
        assert_eq!(
            client
                .get(&url)
                .query(&[("at", at)])
                .send()
                .await
                .unwrap()
                .status(),
            StatusCode::UNPROCESSABLE_ENTITY
        );
    }
    corrected["schema_version"] = json!(2);
    state.send_replace((StatusCode::OK, corrected));
    assert_eq!(
        client.get(&url).send().await.unwrap().status(),
        StatusCode::BAD_GATEWAY
    );
    for status in [StatusCode::NOT_FOUND, StatusCode::SERVICE_UNAVAILABLE] {
        state.send_replace((status, json!({"secret":"never expose upstream body"})));
        let response = client.get(&url).send().await.unwrap();
        assert_eq!(response.status(), status);
        assert!(!response.text().await.unwrap().contains("secret"));
    }
    let mut oversized = observation("2026-09-29T00:00:00Z");
    oversized["quality_flags"] = json!(["x".repeat(65_537)]);
    state.send_replace((StatusCode::OK, oversized));
    assert_eq!(
        client.get(&url).send().await.unwrap().status(),
        StatusCode::BAD_GATEWAY
    );
}

#[tokio::test]
async fn timeline_checks_half_open_range() {
    let (times, times_rx) = watch::channel((
        StatusCode::OK,
        json!(["2026-09-29T00:00:00Z", "2026-09-29T00:05:00Z"]),
    ));
    let upstream = Router::new().route("/api/v1/jeju/timeline", endpoint(times_rx));
    let mut tasks = JoinSet::new();
    let (base, _) = app(&mut tasks, upstream, "redis://127.0.0.1:1/0").await;
    let client = reqwest::Client::new();
    let timeline = format!("{base}/api/v1/jeju/timeline");
    let range = [
        ("start", "2026-09-29T09:00:00+09:00"),
        ("end", "2026-09-29T09:10:00+09:00"),
    ];
    let response = client.get(&timeline).query(&range).send().await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response
            .json::<Value>()
            .await
            .unwrap()
            .as_array()
            .unwrap()
            .len(),
        2
    );
    times.send_replace((StatusCode::OK, json!(["2026-09-29T00:10:00Z"])));
    assert_eq!(
        client
            .get(&timeline)
            .query(&range)
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::BAD_GATEWAY
    );
    assert_eq!(
        client
            .get(&timeline)
            .query(&[
                ("start", "2026-09-01T00:00:00Z"),
                ("end", "2026-09-09T00:00:00Z")
            ])
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::UNPROCESSABLE_ENTITY
    );
}
