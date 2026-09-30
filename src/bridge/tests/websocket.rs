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

#[tokio::test]
#[allow(clippy::panic, reason = "test failure reporting")]
async fn ws_preserves_corrections_disconnects_restarts_and_shutdown() {
    let (updates, updates_rx) = watch::channel(envelope(9, 88.0));
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
    let mut tasks = JoinSet::new();
    let (base, bridge) = app(&mut tasks, upstream, "redis://127.0.0.1:1/0").await;
    tasks.spawn(bridge.clone().run());
    let ws = base.replacen("http", "ws", 1) + "/api/v1/jeju/ws";
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
