use std::{sync::Arc, time::Duration};

use axum::{
    extract::{
        State,
        ws::{Message, WebSocket, WebSocketUpgrade},
    },
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use futures_util::{SinkExt, StreamExt};
use tokio::sync::{OwnedSemaphorePermit, watch};
use tokio_tungstenite::{
    connect_async_with_config,
    tungstenite::{Message as UpstreamMessage, protocol::WebSocketConfig},
};

use super::{
    Bridge, Error,
    protocol::{Envelope, MessageType},
};

pub(super) async fn receive(bridge: Bridge) {
    let Ok(mut url) = bridge.url("/api/v1/jeju/ws") else {
        return;
    };
    let scheme = if url.scheme() == "https" { "wss" } else { "ws" };
    if url.set_scheme(scheme).is_err() {
        return;
    }
    let mut backoff = 1;
    loop {
        let config = WebSocketConfig::default()
            .max_message_size(Some(64 * 1024))
            .max_frame_size(Some(64 * 1024));
        let connection = tokio::time::timeout(
            Duration::from_secs(15),
            connect_async_with_config(url.as_str(), Some(config), false),
        )
        .await;
        if let Ok(Ok((mut socket, _))) = connection {
            let mut first = true;
            loop {
                let deadline = Duration::from_secs(if first { 15 } else { 75 });
                let incoming = tokio::time::timeout(deadline, socket.next()).await;
                match incoming {
                    Ok(Some(Ok(UpstreamMessage::Text(text)))) => {
                        let Ok(mut envelope) = serde_json::from_str::<Envelope>(&text) else {
                            break;
                        };
                        if envelope.validate().is_err() {
                            break;
                        }
                        match envelope.kind {
                            MessageType::Status if envelope.data.is_none() => {
                                envelope.data.clone_from(&bridge.live.borrow().data);
                                envelope.observed_at =
                                    envelope.data.as_ref().map(|s| s.observed_at);
                            }
                            MessageType::Snapshot | MessageType::Status => {}
                        }
                        envelope.refresh();
                        bridge
                            .cache_epoch
                            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                        bridge.live.send_replace(envelope);
                        first = false;
                        backoff = 1;
                    }
                    Ok(Some(Ok(UpstreamMessage::Ping(data)))) => {
                        if !matches!(
                            tokio::time::timeout(
                                Duration::from_secs(5),
                                socket.send(UpstreamMessage::Pong(data))
                            )
                            .await,
                            Ok(Ok(()))
                        ) {
                            break;
                        }
                    }
                    Ok(Some(Ok(UpstreamMessage::Pong(_)))) => {}
                    Ok(
                        Some(
                            Ok(
                                UpstreamMessage::Binary(_)
                                | UpstreamMessage::Close(_)
                                | UpstreamMessage::Frame(_),
                            )
                            | Err(_),
                        )
                        | None,
                    )
                    | Err(_) => break,
                }
            }
        }
        bridge
            .cache_epoch
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        bridge.live.send_modify(|state| {
            state.kind = MessageType::Status;
            if !state
                .quality_flags
                .iter()
                .any(|f| f == "bridge_disconnected")
            {
                state.quality_flags.push("bridge_disconnected".into());
            }
            state.refresh();
        });
        tracing::warn!(retry_seconds = backoff, "bridge stream disconnected");
        tokio::time::sleep(Duration::from_secs(backoff)).await;
        backoff = (backoff * 2).min(30);
    }
}

pub(super) async fn upgrade(
    State(bridge): State<Bridge>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Result<Response, Error> {
    if *bridge.stop.borrow() {
        return Err(Error::Unavailable);
    }
    if let Some(origin) = headers.get("origin")
        && !origin
            .to_str()
            .ok()
            .is_some_and(|o| bridge.allowed_origins.iter().any(|allowed| o == allowed))
    {
        return Ok(StatusCode::FORBIDDEN.into_response());
    }
    let Ok(permit) = Arc::clone(&bridge.ws_slots).try_acquire_owned() else {
        return Ok(StatusCode::TOO_MANY_REQUESTS.into_response());
    };
    Ok(ws
        .max_message_size(16 * 1024)
        .max_frame_size(16 * 1024)
        .on_upgrade(move |socket| serve(socket, bridge, permit)))
}

async fn send(socket: &mut WebSocket, message: Message) -> bool {
    matches!(
        tokio::time::timeout(Duration::from_secs(5), socket.send(message)).await,
        Ok(Ok(()))
    )
}

async fn send_state(socket: &mut WebSocket, live: &mut watch::Receiver<Envelope>) -> bool {
    let mut envelope = live.borrow_and_update().clone();
    envelope.refresh();
    match serde_json::to_string(&envelope) {
        Ok(json) => send(socket, Message::Text(json.into())).await,
        Err(_) => false,
    }
}

async fn serve(mut socket: WebSocket, bridge: Bridge, _permit: OwnedSemaphorePermit) {
    let mut live = bridge.live.subscribe();
    let mut stop = bridge.stop.subscribe();
    if *stop.borrow_and_update() || !send_state(&mut socket, &mut live).await {
        return;
    }
    let mut heartbeat = tokio::time::interval_at(
        tokio::time::Instant::now() + Duration::from_secs(30),
        Duration::from_secs(30),
    );
    loop {
        tokio::select! {
            _ = stop.changed() => {
                if !send(&mut socket, Message::Close(None)).await { tracing::debug!("subscriber close failed"); }
                break;
            },
            changed = live.changed() => {
                if changed.is_err() || !send_state(&mut socket, &mut live).await { break; }
            },
            incoming = socket.recv() => match incoming {
                Some(Ok(Message::Ping(data))) => { if !send(&mut socket, Message::Pong(data)).await { break; } },
                Some(Ok(Message::Pong(_))) => {},
                Some(Ok(Message::Text(_) | Message::Binary(_) | Message::Close(_)) | Err(_)) | None => break,
            },
            _ = heartbeat.tick() => {
                if !send(&mut socket, Message::Ping(Vec::new().into())).await { break; }
            },
        }
    }
}
