use std::sync::atomic::{AtomicUsize, Ordering};

use super::*;
use axum::extract::State;

struct Fixture {
    _tasks: JoinSet<()>,
    bridge: Bridge,
    gate: Arc<Semaphore>,
    reads: Arc<AtomicUsize>,
    reply: watch::Sender<Reply>,
    health: watch::Sender<Reply>,
}

impl Fixture {
    async fn new(redis: &str) -> Self {
        let gate = Arc::new(Semaphore::new(0));
        let reads = Arc::new(AtomicUsize::new(0));
        let (reply, rx) = watch::channel((StatusCode::OK, geography()));
        let (health, health_rx) = watch::channel((
            StatusCode::OK,
            json!({"status":"ready","hub_ready":true,"demand_ready":true}),
        ));
        let gate_copy = gate.clone();
        let reads_copy = reads.clone();
        let source = Router::new()
            .route("/api/v1/health", endpoint(health_rx))
            .route(
                "/api/v1/jeju/assets",
                get(move || {
                    let (gate, reads, rx) = (gate_copy.clone(), reads_copy.clone(), rx.clone());
                    async move {
                        reads.fetch_add(1, Ordering::SeqCst);
                        gate.acquire().await.unwrap().forget();
                        let (status, body) = rx.borrow().clone();
                        (status, Json(body))
                    }
                }),
            );
        let mut tasks = JoinSet::new();
        let (_, bridge) = app(&mut tasks, source, redis).await;
        Self {
            _tasks: tasks,
            bridge,
            gate,
            reads,
            reply,
            health,
        }
    }

    async fn callers(&self, count: usize) -> JoinSet<Result<axum::response::Response, Error>> {
        let mut requests = JoinSet::new();
        for _ in 0..count {
            let bridge = self.bridge.clone();
            requests.spawn(super::super::http::assets(State(bridge)));
        }
        tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                let joined = self
                    .bridge
                    .assets_flight
                    .lock()
                    .await
                    .as_ref()
                    .and_then(futures_util::future::WeakShared::upgrade)
                    .and_then(|flight| flight.strong_count());
                if joined == Some(count + 1) {
                    break;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .unwrap();
        requests
    }
}

#[tokio::test]
async fn new_gis_caller_checks_health_before_joining_old_flight() {
    let fixture = Fixture::new("redis://127.0.0.1:1/0").await;
    let mut initial = fixture.callers(1).await;
    fixture.health.send_replace((
        StatusCode::SERVICE_UNAVAILABLE,
        json!({"status":"unavailable","hub_ready":false,"demand_ready":false}),
    ));
    assert!(matches!(
        super::super::http::assets(State(fixture.bridge.clone())).await,
        Err(Error::Unavailable)
    ));
    fixture.gate.add_permits(1);
    assert_eq!(
        initial
            .join_next()
            .await
            .unwrap()
            .unwrap()
            .unwrap()
            .status(),
        StatusCode::OK
    );
    assert_eq!(fixture.reads.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn gis_shares_source_reads_and_errors_even_without_redis_and_releases_cancelled_flight() {
    let fixture = Fixture::new("redis://127.0.0.1:1/0").await;
    let mut requests = fixture.callers(16).await;
    fixture.gate.add_permits(1);
    while let Some(reply) = requests.join_next().await {
        assert_eq!(reply.unwrap().unwrap().status(), StatusCode::OK);
    }
    assert_eq!(fixture.reads.load(Ordering::SeqCst), 1);

    fixture
        .reply
        .send_replace((StatusCode::SERVICE_UNAVAILABLE, json!({"error":"private"})));
    let mut requests = fixture.callers(16).await;
    fixture.gate.add_permits(1);
    while let Some(reply) = requests.join_next().await {
        assert!(matches!(reply.unwrap(), Err(Error::Unavailable)));
    }
    assert_eq!(fixture.reads.load(Ordering::SeqCst), 2);

    let mut cancelled = fixture.callers(2).await;
    cancelled.shutdown().await;
    assert!(
        fixture
            .bridge
            .assets_flight
            .lock()
            .await
            .as_ref()
            .and_then(futures_util::future::WeakShared::upgrade)
            .is_none()
    );
    fixture.reply.send_replace((StatusCode::OK, geography()));
    fixture.gate.add_permits(4);
    let response = super::super::http::assets(State(fixture.bridge.clone()))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
}

#[tokio::test]
#[ignore = "requires dedicated TEST_REDIS_URL"]
async fn gis_cold_requests_share_one_fill_and_later_hit_uses_cache() {
    let fixture = Fixture::new(&std::env::var("TEST_REDIS_URL").expect("TEST_REDIS_URL")).await;
    let mut requests = fixture.callers(16).await;
    fixture.gate.add_permits(1);
    while let Some(reply) = requests.join_next().await {
        assert_eq!(reply.unwrap().unwrap().headers()["x-cache"], "miss");
    }
    assert_eq!(fixture.reads.load(Ordering::SeqCst), 1);
    let response = super::super::http::assets(State(fixture.bridge.clone()))
        .await
        .unwrap();
    assert_eq!(response.headers()["x-cache"], "hit");
    assert_eq!(fixture.reads.load(Ordering::SeqCst), 1);
    let mut connection = fixture.bridge.probe.redis_connection().await.unwrap();
    let _: u64 = redis::cmd("DEL")
        .arg(&*fixture.bridge.cache_key)
        .query_async(&mut connection)
        .await
        .unwrap();
}
