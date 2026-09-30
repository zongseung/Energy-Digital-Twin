#![allow(clippy::panic, reason = "test failure reporting")]

use axum::extract::ws::{Message, WebSocketUpgrade};
use futures_util::StreamExt;
use tokio_tungstenite::{
    connect_async,
    tungstenite::{Message as ClientMessage, client::IntoClientRequest},
};

use super::*;

fn envelope(version: u64, demand: f64) -> Value {
    let mut data = observation("2026-09-29T00:00:00Z");
    data["demand_mw"] = json!(demand);
    json!({"type":"snapshot","schema_version":1,"observed_at":"2026-09-29T00:00:00Z",
        "sent_at":"2026-09-29T00:00:01Z","state_version":version,
        "source":protocol::SOURCE,"quality_flags":[],"data":data})
}

type Client =
    tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;

async fn next(socket: &mut Client) -> Value {
    tokio::time::timeout(Duration::from_secs(6), async {
        loop {
            match socket.next().await.unwrap().unwrap() {
                ClientMessage::Text(text) => return serde_json::from_str(&text).unwrap(),
                ClientMessage::Ping(_) | ClientMessage::Pong(_) => {}
                other => panic!("unexpected frame: {other:?}"),
            }
        }
    })
    .await
    .unwrap()
}

async fn version(socket: &mut Client, expected: u64) -> Value {
    for _ in 0..8 {
        let value = next(socket).await;
        if value["type"] == "snapshot" && value["state_version"] == expected {
            return value;
        }
    }
    panic!("missing snapshot version {expected}")
}

async fn live(tasks: &mut JoinSet<()>, first: Value) -> (watch::Sender<Value>, Bridge, String) {
    let (updates, updates_rx) = watch::channel(first);
    let upstream = Router::new().route(
        "/api/v1/jeju/ws",
        get(move |ws: WebSocketUpgrade| {
            let mut updates = updates_rx.clone();
            async move {
                ws.on_upgrade(move |mut socket| async move {
                    loop {
                        let value = updates.borrow_and_update().clone();
                        if value.is_null() {
                            socket.send(Message::Close(None)).await.unwrap();
                            return;
                        }
                        if socket
                            .send(Message::Text(value.to_string().into()))
                            .await
                            .is_err()
                        {
                            return;
                        }
                        if updates.changed().await.is_err() {
                            return;
                        }
                    }
                })
            }
        }),
    );
    let (base, bridge) = app(tasks, upstream, "redis://127.0.0.1:1/0").await;
    tasks.spawn(bridge.clone().run());
    (
        updates,
        bridge,
        base.replacen("http", "ws", 1) + "/api/v1/jeju/ws",
    )
}

#[tokio::test]
#[allow(clippy::panic, reason = "test failure reporting")]
async fn ws_preserves_corrections_disconnects_restarts_and_shutdown() {
    let mut tasks = JoinSet::new();
    let (updates, bridge, ws) = live(&mut tasks, envelope(9, 88.0)).await;
    let mut request = ws.clone().into_client_request().unwrap();
    request
        .headers_mut()
        .insert("origin", "https://untrusted.example".parse().unwrap());
    let failure = connect_async(request).await.unwrap_err();
    match failure {
        tokio_tungstenite::tungstenite::Error::Http(response) => {
            assert_eq!(response.status(), StatusCode::FORBIDDEN);
        }
        other => panic!("wrong rejection: {other}"),
    }
    let (mut client, _) = connect_async(&ws).await.unwrap();
    let first = version(&mut client, 9).await;
    assert_eq!(first["data"]["demand_mw"], 88.0);
    assert!(first["data"]["solar_mw"].is_null());
    updates.send_replace(envelope(10, 99.0));
    let correction = version(&mut client, 10).await;
    assert_eq!(correction["observed_at"], first["observed_at"]);
    assert_eq!(correction["data"]["demand_mw"], 99.0);
    let mut failure = envelope(11, 99.0);
    failure["type"] = json!("status");
    failure["quality_flags"] = json!(["source_unavailable"]);
    updates.send_replace(failure);
    let failure = next(&mut client).await;
    assert_eq!(failure["type"], "status");
    assert_eq!(failure["data"]["demand_mw"], 99.0);
    assert_eq!(failure["observed_at"], first["observed_at"]);
    updates.send_replace(Value::Null);
    let disconnected = next(&mut client).await;
    assert_eq!(disconnected["type"], "status");
    assert!(
        disconnected["quality_flags"]
            .as_array()
            .unwrap()
            .contains(&json!("bridge_disconnected"))
    );
    assert_eq!(disconnected["data"]["demand_mw"], 99.0);
    // The source counter resets after restart; it is not a global monotonic sequence.
    updates.send_replace(envelope(1, 101.0));
    let recovery = version(&mut client, 1).await;
    assert_eq!(recovery["data"]["demand_mw"], 101.0);
    assert!(
        !recovery["quality_flags"]
            .as_array()
            .unwrap()
            .contains(&json!("bridge_disconnected"))
    );
    let (mut second, _) = connect_async(&ws).await.unwrap();
    assert_eq!(version(&mut second, 1).await["data"], recovery["data"]);
    bridge.stop();
    assert!(matches!(
        tokio::time::timeout(Duration::from_secs(2), client.next())
            .await
            .unwrap(),
        Some(Ok(ClientMessage::Close(_)))
    ));
}

#[tokio::test]
async fn ws_does_not_resend_identical_upstream_state() {
    let mut tasks = JoinSet::new();
    let (updates, _bridge, ws) = live(&mut tasks, envelope(9, 88.0)).await;
    let (mut client, _) = connect_async(&ws).await.unwrap();
    version(&mut client, 9).await;
    let mut same = envelope(9, 88.0);
    same["sent_at"] = json!("2026-09-29T00:00:02Z");
    updates.send_replace(same);
    assert!(
        tokio::time::timeout(Duration::from_millis(500), next(&mut client))
            .await
            .is_err(),
        "identical state was resent"
    );
    updates.send_replace(envelope(10, 99.0));
    assert_eq!(next(&mut client).await["state_version"], 10);
    // A restarted source reuses low versions; different content must still arrive.
    updates.send_replace(envelope(1, 101.0));
    let restart = next(&mut client).await;
    assert_eq!(restart["state_version"], 1);
    assert_eq!(restart["data"]["demand_mw"], 101.0);
}

#[tokio::test]
async fn ws_initial_snapshot_never_hides_immediate_correction() {
    let mut tasks = JoinSet::new();
    let (updates, _bridge, ws) = live(&mut tasks, envelope(99, 88.0)).await;
    for i in 0..30_u32 {
        let (mut client, _) = connect_async(&ws).await.unwrap();
        updates.send_replace(envelope(100 + u64::from(i), f64::from(i)));
        let correction = version(&mut client, 100 + u64::from(i)).await;
        assert_eq!(correction["data"]["demand_mw"], f64::from(i));
    }
}

#[tokio::test]
async fn ws_slow_subscriber_is_dropped_without_blocking_others() {
    let mut tasks = JoinSet::new();
    let (updates, bridge, ws) = live(&mut tasks, envelope(1, 88.0)).await;
    let socket = tokio::net::TcpSocket::new_v4().unwrap();
    socket.set_recv_buffer_size(4096).unwrap();
    let addr = ws.trim_start_matches("ws://").split('/').next().unwrap();
    let stream = socket.connect(addr.parse().unwrap()).await.unwrap();
    // Never read: the kernel buffers fill and the server's 5 s send timeout ends the session.
    let (_slow, _) = tokio_tungstenite::client_async(&ws, stream).await.unwrap();
    let (mut fast, _) = connect_async(&ws).await.unwrap();
    assert_eq!(bridge.ws_slots.available_permits(), 30);
    let mut last = 1;
    tokio::time::timeout(Duration::from_secs(20), async {
        while bridge.ws_slots.available_permits() < 31 {
            last += 1;
            let mut big = envelope(last, 88.0);
            // data.source must equal SOURCE, so the ~8 KiB payload rides in a quality flag.
            big["quality_flags"] = json!(["x".repeat(8 * 1024)]);
            updates.send_replace(big);
            version(&mut fast, last).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(bridge.ws_slots.available_permits(), 31);
    updates.send_replace(envelope(last + 1, 99.0));
    assert_eq!(
        version(&mut fast, last + 1).await["data"]["demand_mw"],
        99.0
    );
}
