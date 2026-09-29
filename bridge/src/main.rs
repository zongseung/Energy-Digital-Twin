use axum::{
    Json, Router,
    extract::{
        Query, State,
        rejection::QueryRejection,
        ws::{Message, WebSocket, WebSocketUpgrade},
    },
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::get,
};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sqlx::{
    PgPool, Postgres, Row,
    postgres::{PgArguments, PgPoolOptions, PgRow},
};
use std::{env, net::SocketAddr, process::ExitCode, sync::Arc, time::Duration as StdDuration};
use tokio::sync::{OwnedSemaphorePermit, Semaphore, watch};

const SELECT_STATE: &str = "SELECT ts AT TIME ZONE 'Asia/Seoul' AS observed_at, demand_mw, supply_mw, wind_mw, solar_mw, renewable_total_mw FROM public.jeju_supply_demand";

#[derive(Clone)]
struct AppState {
    hub: PgPool,
    demand: PgPool,
    live: watch::Sender<LiveState>,
    stop: watch::Sender<bool>,
    allowed_origins: Vec<String>,
    ws_slots: Arc<Semaphore>,
}

fn pool(url: &str) -> Result<PgPool, &'static str> {
    PgPoolOptions::new()
        .max_connections(4)
        .acquire_timeout(StdDuration::from_secs(3))
        .after_connect(|conn, _| {
            Box::pin(async move {
                sqlx::query("SET default_transaction_read_only = on")
                    .execute(&mut *conn)
                    .await?;
                sqlx::query("SET statement_timeout = '10s'")
                    .execute(&mut *conn)
                    .await?;
                Ok(())
            })
        })
        .connect_lazy(url)
        .map_err(|_| "invalid_database_url")
}

fn settings() -> Result<(AppState, SocketAddr, StdDuration), &'static str> {
    let hub = env::var("HUB_DATABASE_URL")
        .ok()
        .filter(|s| !s.is_empty())
        .ok_or("HUB_DATABASE_URL is required")?;
    let demand = env::var("DEMAND_DATABASE_URL")
        .ok()
        .filter(|s| !s.is_empty())
        .ok_or("DEMAND_DATABASE_URL is required")?;
    if env::var("SOURCE_TIMEZONE").as_deref() != Ok("Asia/Seoul") {
        return Err("SOURCE_TIMEZONE must explicitly be Asia/Seoul for the current source");
    }
    let bind = env::var("BIND_ADDR")
        .unwrap_or_else(|_| "127.0.0.1:8091".into())
        .parse()
        .map_err(|_| "invalid BIND_ADDR")?;
    let seconds: u64 = env::var("POLL_INTERVAL_SECONDS")
        .unwrap_or_else(|_| "60".into())
        .parse()
        .map_err(|_| "invalid POLL_INTERVAL_SECONDS")?;
    if !(1..=300).contains(&seconds) {
        return Err("POLL_INTERVAL_SECONDS must be in 1..=300");
    }
    let (live, _) = watch::channel(LiveState {
        state_version: 0,
        available: false,
        data: None,
    });
    Ok((
        AppState {
            hub: pool(&hub).map_err(|_| "invalid HUB_DATABASE_URL")?,
            demand: pool(&demand).map_err(|_| "invalid DEMAND_DATABASE_URL")?,
            live,
            stop: watch::channel(false).0,
            allowed_origins: env::var("ALLOWED_ORIGINS")
                .unwrap_or_default()
                .split(',')
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(str::to_owned)
                .collect(),
            ws_slots: Arc::new(Semaphore::new(32)),
        },
        bind,
        StdDuration::from_secs(seconds),
    ))
}

#[derive(Debug)]
struct ApiError(StatusCode, &'static str);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error":self.1}))).into_response()
    }
}

fn unavailable<T>(_error: T) -> ApiError {
    // Database driver errors may contain credentials or connection strings.
    ApiError(StatusCode::SERVICE_UNAVAILABLE, "source_unavailable")
}

fn invalid(message: &'static str) -> ApiError {
    ApiError(StatusCode::UNPROCESSABLE_ENTITY, message)
}

async fn source_read<T>(
    query: impl std::future::Future<Output = Result<T, sqlx::Error>>,
) -> Result<T, ApiError> {
    // Server statement_timeout cannot bound a stalled network connection.
    tokio::time::timeout(StdDuration::from_secs(12), query)
        .await
        .map_err(unavailable)?
        .map_err(unavailable)
}

async fn source_rows(
    pool: &PgPool,
    query: sqlx::query::Query<'_, Postgres, PgArguments>,
) -> Result<Vec<PgRow>, ApiError> {
    let mut connection = source_read(pool.acquire()).await?;
    // ponytail: close each SELECT connection, including cancellations; recycle only
    // after request volume warrants a bounded connection-return implementation.
    connection.close_on_drop();
    source_read(query.fetch_all(&mut *connection)).await
}

fn router(state: AppState) -> Router {
    Router::new()
        .route("/api/v1/health", get(health))
        .route("/api/v1/jeju/assets", get(assets))
        .route("/api/v1/jeju/state", get(observation))
        .route("/api/v1/jeju/timeline", get(timeline))
        .route("/api/v1/jeju/ws", get(ws_upgrade))
        .with_state(state)
}

async fn health(State(state): State<AppState>) -> Response {
    let (hub, demand) = tokio::join!(
        source_rows(
            &state.hub,
            sqlx::query("SELECT id FROM public.power_line LIMIT 1")
        ),
        source_rows(
            &state.demand,
            sqlx::query("SELECT ts FROM public.jeju_supply_demand LIMIT 1")
        )
    );
    let status = if hub.is_ok() && demand.is_ok() {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };
    (status, Json(json!({"status": if status == StatusCode::OK { "ready" } else { "unavailable" }, "hub_ready":hub.is_ok(), "demand_ready":demand.is_ok()}))).into_response()
}

async fn read_state(
    pool: &PgPool,
    at: Option<DateTime<Utc>>,
) -> Result<Option<Snapshot>, ApiError> {
    let sql = if at.is_some() {
        format!("{SELECT_STATE} WHERE ts = ($1::timestamptz AT TIME ZONE 'Asia/Seoul')")
    } else {
        format!("{SELECT_STATE} ORDER BY ts DESC LIMIT 1")
    };
    let query = sqlx::query(&sql);
    let query = match at {
        Some(at) => query.bind(at),
        None => query,
    };
    let row = source_rows(pool, query).await?.into_iter().next();
    row.map(|row| {
        let values = [
            "demand_mw",
            "supply_mw",
            "wind_mw",
            "solar_mw",
            "renewable_total_mw",
        ]
        .map(|name| row.try_get::<Option<f64>, _>(name));
        let [demand, supply, wind, solar, renewable] = values;
        Ok(snapshot(
            row.try_get("observed_at").map_err(unavailable)?,
            [
                demand.map_err(unavailable)?,
                supply.map_err(unavailable)?,
                wind.map_err(unavailable)?,
                solar.map_err(unavailable)?,
                renewable.map_err(unavailable)?,
            ],
        ))
    })
    .transpose()
}

#[derive(Deserialize)]
struct StateQuery {
    at: Option<String>,
}

async fn observation(
    State(state): State<AppState>,
    query: Result<Query<StateQuery>, QueryRejection>,
) -> Result<Json<Snapshot>, ApiError> {
    let Query(query) = query.map_err(|_| invalid("invalid_query"))?;
    let at = query
        .at
        .as_deref()
        .map(parse_at)
        .transpose()
        .map_err(invalid)?;
    let value = read_state(&state.demand, at)
        .await?
        .ok_or(ApiError(StatusCode::NOT_FOUND, "observation_not_found"))?;
    Ok(Json(if at.is_none() {
        with_delay(value, Utc::now())
    } else {
        value
    }))
}

#[derive(Deserialize)]
struct RangeQuery {
    start: String,
    end: String,
}

async fn timeline(
    State(state): State<AppState>,
    query: Result<Query<RangeQuery>, QueryRejection>,
) -> Result<Json<Vec<DateTime<Utc>>>, ApiError> {
    let Query(query) = query.map_err(|_| invalid("start_and_end_are_required"))?;
    let (start, end) = range(&query.start, &query.end).map_err(invalid)?;
    let values: Vec<DateTime<Utc>> = source_rows(&state.demand, sqlx::query("SELECT ts AT TIME ZONE 'Asia/Seoul' FROM public.jeju_supply_demand WHERE ts >= ($1::timestamptz AT TIME ZONE 'Asia/Seoul') AND ts < ($2::timestamptz AT TIME ZONE 'Asia/Seoul') ORDER BY ts LIMIT 2017")
        .bind(start).bind(end)).await?.into_iter().map(|row| row.try_get(0)).collect::<Result<_, _>>().map_err(unavailable)?;
    if values.len() > 2016 {
        return Err(invalid("too_many_observations"));
    }
    Ok(Json(values))
}

async fn assets(State(state): State<AppState>) -> Result<Json<Value>, ApiError> {
    let rows = source_rows(&state.hub, sqlx::query(include_str!("assets.sql"))).await?;
    let sqlx::types::Json(features): sqlx::types::Json<Value> = rows
        .first()
        .ok_or_else(|| unavailable(()))?
        .try_get(0)
        .map_err(unavailable)?;
    Ok(Json(
        json!({"type":"FeatureCollection", "schema_version":1, "source":"energy-hub-db.public", "generated_at":Utc::now(), "features":features}),
    ))
}

async fn poll(state: AppState, period: StdDuration) {
    let mut timer = tokio::time::interval(period);
    timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        timer.tick().await;
        let next = read_state(&state.demand, None)
            .await
            .ok()
            .flatten()
            .map(|s| with_delay(s, Utc::now()));
        state.live.send_if_modified(|live| update(live, next));
    }
}

async fn ws_upgrade(
    State(state): State<AppState>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Result<Response, ApiError> {
    if *state.stop.borrow() {
        return Err(ApiError(StatusCode::SERVICE_UNAVAILABLE, "bridge_stopping"));
    }
    if let Some(origin) = headers.get("Origin")
        && !origin.to_str().ok().is_some_and(|origin| {
            state
                .allowed_origins
                .iter()
                .any(|allowed| allowed == origin)
        })
    {
        return Err(ApiError(StatusCode::FORBIDDEN, "origin_not_allowed"));
    }
    let permit = state
        .ws_slots
        .clone()
        .try_acquire_owned()
        .map_err(|_| ApiError(StatusCode::TOO_MANY_REQUESTS, "too_many_subscribers"))?;
    Ok(ws
        .max_message_size(16384)
        .max_frame_size(16384)
        .on_upgrade(move |socket| {
            ws_stream(
                socket,
                state.live.subscribe(),
                state.stop.subscribe(),
                permit,
            )
        }))
}

fn envelope(live: &LiveState) -> String {
    let now = Utc::now();
    let data = live.data.clone().map(|s| with_delay(s, now));
    let mut flags = data
        .as_ref()
        .map(|s| s.quality_flags.clone())
        .unwrap_or_default();
    if !live.available {
        flags.push("source_unavailable".into());
    }
    json!({
        "type": if live.available {"snapshot"} else {"status"}, "schema_version":1,
        "observed_at":data.as_ref().map(|s| s.observed_at), "sent_at":now,
        "state_version":live.state_version, "source":"demand-postgres.public.jeju_supply_demand",
        "quality_flags":flags, "data":data
    })
    .to_string()
}

async fn send(socket: &mut WebSocket, message: Message) -> bool {
    matches!(
        tokio::time::timeout(StdDuration::from_secs(5), socket.send(message)).await,
        Ok(Ok(()))
    )
}

async fn ws_stream(
    mut socket: WebSocket,
    mut receiver: watch::Receiver<LiveState>,
    mut stop: watch::Receiver<bool>,
    _permit: OwnedSemaphorePermit,
) {
    if *stop.borrow_and_update() {
        let _ = send(&mut socket, Message::Close(None)).await;
        return;
    }
    // ponytail: retain only latest state; add a durable stream only if replay is required.
    let initial = receiver.borrow_and_update().clone();
    if !send(&mut socket, Message::Text(envelope(&initial).into())).await {
        return;
    }
    let mut heartbeat = tokio::time::interval_at(
        tokio::time::Instant::now() + StdDuration::from_secs(30),
        StdDuration::from_secs(30),
    );
    loop {
        tokio::select! {
            _ = stop.changed() => {
                let _ = send(&mut socket, Message::Close(None)).await;
                break;
            }
            changed = receiver.changed() => {
                if changed.is_err() { break; }
                let state = receiver.borrow_and_update().clone();
                if !send(&mut socket, Message::Text(envelope(&state).into())).await { break; }
            }
            incoming = socket.recv() => match incoming {
                Some(Ok(Message::Ping(data))) => { if !send(&mut socket, Message::Pong(data)).await { break; } }
                Some(Ok(Message::Pong(_))) => {}
                _ => break,
            },
            _ = heartbeat.tick() => {
                if !send(&mut socket, Message::Ping(Vec::new().into())).await { break; }
            }
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
struct Snapshot {
    schema_version: u8,
    observed_at: DateTime<Utc>,
    source: String,
    source_timezone: String,
    quality_flags: Vec<String>,
    demand_mw: Option<f64>,
    supply_capacity_mw: Option<f64>,
    wind_mw: Option<f64>,
    solar_mw: Option<f64>,
    renewable_total_mw: Option<f64>,
}

fn parse_at(value: &str) -> Result<DateTime<Utc>, &'static str> {
    DateTime::parse_from_rfc3339(value)
        .map(|value| value.to_utc())
        .map_err(|_| "time_requires_rfc3339_offset")
}

fn range(start: &str, end: &str) -> Result<(DateTime<Utc>, DateTime<Utc>), &'static str> {
    let (start, end) = (parse_at(start)?, parse_at(end)?);
    if end <= start || end - start > Duration::days(7) {
        return Err("range_must_be_positive_and_at_most_seven_days");
    }
    Ok((start, end))
}

fn snapshot(at: DateTime<Utc>, mut values: [Option<f64>; 5]) -> Snapshot {
    let mut flags = Vec::new();
    let names = [
        "demand_mw",
        "supply_capacity_mw",
        "wind_mw",
        "solar_mw",
        "renewable_total_mw",
    ];
    for (value, name) in values.iter_mut().zip(names) {
        match *value {
            None => flags.push(format!("{name}:missing")),
            Some(v) if !v.is_finite() => {
                flags.push(format!("{name}:nonfinite"));
                *value = None;
            }
            Some(v) if v < 0.0 => flags.push(format!("{name}:negative")),
            _ => {}
        }
    }
    if let [_, _, Some(wind), Some(solar), Some(total)] = values
        && wind + solar > total + 1e-6
    {
        flags.push("renewable_components_exceed_total".into());
    }
    Snapshot {
        schema_version: 1,
        observed_at: at,
        source: "demand-postgres.public.jeju_supply_demand".into(),
        source_timezone: "Asia/Seoul".into(),
        quality_flags: flags,
        demand_mw: values[0],
        supply_capacity_mw: values[1],
        wind_mw: values[2],
        solar_mw: values[3],
        renewable_total_mw: values[4],
    }
}

fn with_delay(mut value: Snapshot, now: DateTime<Utc>) -> Snapshot {
    value
        .quality_flags
        .retain(|f| f != "source_delayed" && f != "source_in_future");
    if now - value.observed_at >= Duration::minutes(15) {
        value.quality_flags.push("source_delayed".into());
    } else if value.observed_at > now {
        value.quality_flags.push("source_in_future".into());
    }
    value
}

#[derive(Clone, Debug, PartialEq, Serialize)]
struct LiveState {
    state_version: u64,
    available: bool,
    data: Option<Snapshot>,
}

fn update(current: &mut LiveState, next: Option<Snapshot>) -> bool {
    let available = next.is_some();
    let data = next.or_else(|| current.data.clone());
    if current.available == available && current.data == data {
        return false;
    }
    current.state_version += 1;
    current.available = available;
    current.data = data;
    true
}

#[tokio::main]
async fn main() -> ExitCode {
    let (state, bind, period) = match settings() {
        Ok(value) => value,
        Err(message) => {
            eprintln!("{message}");
            return ExitCode::FAILURE;
        }
    };
    let listener = match tokio::net::TcpListener::bind(bind).await {
        Ok(listener) => listener,
        Err(_) => {
            eprintln!("bridge_bind_failed");
            return ExitCode::FAILURE;
        }
    };
    let poller = tokio::spawn(poll(state.clone(), period));
    eprintln!("jeju-data-bridge listening on {bind}");
    let stop = state.stop.clone();
    let result = axum::serve(listener, router(state.clone()))
        .with_graceful_shutdown(async move {
            shutdown().await;
            stop.send_replace(true);
        })
        .await;
    poller.abort();
    if tokio::time::timeout(StdDuration::from_secs(3), async {
        tokio::join!(state.hub.close(), state.demand.close());
    })
    .await
    .is_err()
    {
        eprintln!("bridge_pool_shutdown_timeout");
    }
    if result.is_err() {
        eprintln!("bridge_server_failed");
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

async fn shutdown() {
    #[cfg(unix)]
    {
        let mut terminate =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                .expect("install SIGTERM handler");
        tokio::select! { _ = tokio::signal::ctrl_c() => {}, _ = terminate.recv() => {} }
    }
    #[cfg(not(unix))]
    let _ = tokio::signal::ctrl_c().await;
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{body::Body, http::Request};
    use futures_util::StreamExt;
    use tower::ServiceExt;

    #[tokio::test(start_paused = true)]
    async fn source_deadline_bounds_stalled_queries() {
        let start = tokio::time::Instant::now();
        let response = tokio::time::timeout(
            StdDuration::from_secs(13),
            source_read(std::future::pending::<Result<(), sqlx::Error>>()),
        )
        .await
        .expect("stalled source must fail within the 12 second deadline")
        .unwrap_err();
        assert_eq!(response.0, StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(response.1, "source_unavailable");
        assert_eq!(start.elapsed(), StdDuration::from_secs(12));
    }

    fn state() -> AppState {
        let (live, _) = watch::channel(LiveState {
            state_version: 0,
            available: false,
            data: None,
        });
        AppState {
            hub: pool("postgresql://unused:never-log-this@127.0.0.1:1/unused").unwrap(),
            demand: pool("postgresql://unused:never-log-this@127.0.0.1:1/unused").unwrap(),
            live,
            stop: watch::channel(false).0,
            allowed_origins: vec![],
            ws_slots: Arc::new(Semaphore::new(32)),
        }
    }

    #[tokio::test]
    async fn http_rejects_invalid_inputs_before_querying_and_redacts_failures() {
        let app = router(state());
        for uri in [
            "/api/v1/jeju/state?at=2026-09-29T09:00:00",
            "/api/v1/jeju/timeline",
            "/api/v1/jeju/timeline?start=2026-09-01T00:00:00Z&end=2026-09-09T00:00:00Z",
        ] {
            let response = app
                .clone()
                .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
        }
        let response = app
            .oneshot(
                Request::builder()
                    .uri("/api/v1/health")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
        let body = axum::body::to_bytes(response.into_body(), 16384)
            .await
            .unwrap();
        assert!(!String::from_utf8_lossy(&body).contains("never-log-this"));
    }

    #[tokio::test]
    #[ignore = "requires explicit HUB_DATABASE_URL, DEMAND_DATABASE_URL, SOURCE_TIMEZONE"]
    async fn real_sources_are_read_only_and_http_preserves_observations_and_geometry() {
        let (state, _, _) = settings().expect("explicit source configuration");
        for connection in [&state.hub, &state.demand] {
            let readonly: String = sqlx::query_scalar("SHOW default_transaction_read_only")
                .fetch_one(connection)
                .await
                .unwrap();
            assert_eq!(readonly, "on");
        }
        let observed = read_state(&state.demand, None).await.unwrap().unwrap();
        let raw_rows = sqlx::query("SELECT ts, demand_mw, supply_mw, wind_mw, solar_mw, renewable_total_mw FROM public.jeju_supply_demand ORDER BY ts DESC LIMIT 2")
            .fetch_all(&state.demand).await.unwrap();
        assert_eq!(raw_rows.len(), 2, "verify two distinct actual observations");
        let app = router(state.clone());
        for raw in raw_rows {
            let raw_at: chrono::NaiveDateTime = raw.get("ts");
            let at = raw_at
                .and_local_timezone(chrono::FixedOffset::east_opt(9 * 3600).unwrap())
                .single()
                .unwrap()
                .to_utc();
            let uri = format!(
                "/api/v1/jeju/state?at={}",
                at.to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
            );
            let response = app
                .clone()
                .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::OK);
            let returned: Snapshot = serde_json::from_slice(
                &axum::body::to_bytes(response.into_body(), 16384)
                    .await
                    .unwrap(),
            )
            .unwrap();
            assert_eq!(returned.observed_at, at);
            assert_eq!(
                [
                    returned.demand_mw,
                    returned.supply_capacity_mw,
                    returned.wind_mw,
                    returned.solar_mw,
                    returned.renewable_total_mw
                ],
                [
                    "demand_mw",
                    "supply_mw",
                    "wind_mw",
                    "solar_mw",
                    "renewable_total_mw"
                ]
                .map(|key| raw.get::<Option<f64>, _>(key))
            );
        }
        let uri = format!(
            "/api/v1/jeju/timeline?start={}&end={}",
            (observed.observed_at - Duration::minutes(10))
                .to_rfc3339_opts(chrono::SecondsFormat::Secs, true),
            (observed.observed_at + Duration::minutes(5))
                .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
        );
        let response = app
            .clone()
            .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let times: Vec<DateTime<Utc>> = serde_json::from_slice(
            &axum::body::to_bytes(response.into_body(), 16384)
                .await
                .unwrap(),
        )
        .unwrap();
        assert!(times.contains(&observed.observed_at));
        let uri = format!(
            "/api/v1/jeju/state?at={}",
            (observed.observed_at + Duration::days(365))
                .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
        );
        assert_eq!(
            app.clone()
                .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
                .await
                .unwrap()
                .status(),
            StatusCode::NOT_FOUND
        );
        let response = app
            .oneshot(
                Request::builder()
                    .uri("/api/v1/jeju/assets")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let geojson: Value = serde_json::from_slice(
            &axum::body::to_bytes(response.into_body(), 8 * 1024 * 1024)
                .await
                .unwrap(),
        )
        .unwrap();
        let features = geojson["features"].as_array().unwrap();
        assert!(!features.is_empty());
        let ids: std::collections::BTreeSet<_> =
            features.iter().map(|f| f["id"].as_str().unwrap()).collect();
        assert_eq!(ids.len(), features.len());
        assert!(features.iter().all(|f| !f["geometry"].is_null()));
        assert!(features.iter().any(|f| {
            f["geometry"]["type"] == "LineString"
                && f["geometry"]["coordinates"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|p| p[1].as_f64().unwrap() > 34.0)
        }));
        println!(
            "Read-only real-source verification: two HTTP/DB observations, {} assets, {} actual time slots",
            features.len(),
            times.len()
        );
    }

    #[tokio::test]
    async fn websocket_sends_initial_snapshot_correction_and_reconnect_snapshot() {
        let state = state();
        let initial = snapshot(
            at(),
            [
                Some(800.0),
                Some(1400.0),
                Some(20.0),
                Some(10.0),
                Some(35.0),
            ],
        );
        state
            .live
            .send_if_modified(|live| update(live, Some(initial.clone())));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let app = router(state.clone());
        let handle = tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });
        let url = format!("ws://{address}/api/v1/jeju/ws");
        let result = tokio_tungstenite::connect_async(&url).await;
        assert!(
            result.is_ok(),
            "WebSocket endpoint must accept a native client"
        );
        let (mut ws, _) = result.unwrap();
        let initial_message = tokio::time::timeout(StdDuration::from_secs(2), ws.next())
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        let initial_json: serde_json::Value =
            serde_json::from_str(initial_message.to_text().unwrap()).unwrap();
        assert_eq!(initial_json["type"], "snapshot");
        assert_eq!(initial_json["data"]["demand_mw"], 800.0);
        assert_eq!(initial_json["state_version"], 1);
        let mut corrected = initial;
        corrected.demand_mw = Some(801.0);
        state
            .live
            .send_if_modified(|live| update(live, Some(corrected)));
        let message = tokio::time::timeout(StdDuration::from_secs(2), ws.next())
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        let corrected_json: serde_json::Value =
            serde_json::from_str(message.to_text().unwrap()).unwrap();
        assert_eq!(corrected_json["data"]["demand_mw"], 801.0);
        assert_eq!(corrected_json["observed_at"], initial_json["observed_at"]);
        assert_eq!(corrected_json["state_version"], 2);
        state.live.send_if_modified(|live| update(live, None));
        let message = tokio::time::timeout(StdDuration::from_secs(2), ws.next())
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        let failed_json: serde_json::Value =
            serde_json::from_str(message.to_text().unwrap()).unwrap();
        assert_eq!(failed_json["type"], "status");
        assert_eq!(failed_json["observed_at"], initial_json["observed_at"]);
        assert!(
            failed_json["quality_flags"]
                .as_array()
                .unwrap()
                .contains(&serde_json::json!("source_unavailable"))
        );
        let (mut reconnected, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
        let message = tokio::time::timeout(StdDuration::from_secs(2), reconnected.next())
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        let restored: serde_json::Value = serde_json::from_str(message.to_text().unwrap()).unwrap();
        assert_eq!(restored["data"]["demand_mw"], 801.0);
        assert_eq!(restored["state_version"], 3);
        use tokio_tungstenite::tungstenite::client::IntoClientRequest;
        let mut request = url.clone().into_client_request().unwrap();
        request
            .headers_mut()
            .insert("Origin", "https://untrusted.invalid".parse().unwrap());
        match tokio_tungstenite::connect_async(request).await {
            Err(tokio_tungstenite::tungstenite::Error::Http(response)) => {
                assert_eq!(response.status(), StatusCode::FORBIDDEN)
            }
            _ => panic!("unlisted Origin must be rejected"),
        }
        state.stop.send(true).unwrap();
        let closed = tokio::time::timeout(StdDuration::from_secs(1), reconnected.next()).await;
        assert!(
            closed.is_ok(),
            "shutdown must release active WebSockets promptly"
        );
        match tokio_tungstenite::connect_async(&url).await {
            Err(tokio_tungstenite::tungstenite::Error::Http(response)) => {
                assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE)
            }
            _ => panic!("shutdown must reject new WebSocket subscribers"),
        }
        handle.abort();
    }

    fn at() -> DateTime<Utc> {
        DateTime::parse_from_rfc3339("2026-09-29T00:00:00Z")
            .unwrap()
            .to_utc()
    }

    #[test]
    fn offsets_are_equivalent_and_naive_input_is_rejected() {
        assert_eq!(parse_at("2026-09-29T09:00:00+09:00").unwrap(), at());
        assert_eq!(parse_at("2026-09-29T00:00:00Z").unwrap(), at());
        assert!(parse_at("2026-09-29T09:00:00").is_err());
    }

    #[test]
    fn range_is_bounded_and_does_not_invent_missing_slots() {
        assert!(range("2026-09-01T00:00:00Z", "2026-09-08T00:00:00Z").is_ok());
        assert!(range("2026-09-01T00:00:00Z", "2026-09-08T00:00:01Z").is_err());
        assert!(range("2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z").is_err());
    }

    #[test]
    fn zero_null_nonfinite_and_capacity_keep_their_meaning() {
        let s = snapshot(
            at(),
            [Some(0.0), Some(1400.0), None, Some(f64::NAN), Some(12.0)],
        );
        assert_eq!(s.demand_mw, Some(0.0));
        assert_eq!(s.supply_capacity_mw, Some(1400.0));
        assert_eq!(s.wind_mw, None);
        assert_eq!(s.solar_mw, None);
        assert!(s.quality_flags.contains(&"wind_mw:missing".into()));
        assert!(s.quality_flags.contains(&"solar_mw:nonfinite".into()));
        let json = serde_json::to_value(s).unwrap();
        assert!(json.get("supply_mw").is_none());
        assert_eq!(json["solar_mw"], serde_json::Value::Null);
    }

    #[test]
    fn correction_failure_recovery_and_delay_preserve_observed_time() {
        let s = snapshot(
            at(),
            [
                Some(800.0),
                Some(1400.0),
                Some(20.0),
                Some(10.0),
                Some(35.0),
            ],
        );
        let mut live = LiveState {
            state_version: 0,
            available: false,
            data: None,
        };
        assert!(update(&mut live, Some(s.clone())));
        assert!(!update(&mut live, Some(s.clone())));
        let mut corrected = s.clone();
        corrected.demand_mw = Some(801.0);
        assert!(update(&mut live, Some(corrected.clone())));
        assert_eq!(live.state_version, 2);
        assert!(update(&mut live, None));
        assert!(!live.available);
        assert_eq!(live.data.as_ref().unwrap().observed_at, at());
        assert!(!update(&mut live, None));
        assert!(update(&mut live, Some(corrected)));
        assert_eq!(live.state_version, 4);
        assert!(
            !with_delay(s.clone(), at() + Duration::minutes(14))
                .quality_flags
                .contains(&"source_delayed".into())
        );
        assert!(
            with_delay(s, at() + Duration::minutes(15))
                .quality_flags
                .contains(&"source_delayed".into())
        );
    }
}
