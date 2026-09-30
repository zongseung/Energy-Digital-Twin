use std::{sync::Arc, time::Duration};

use axum::{
    extract::{
        State,
        ws::{Message, WebSocket, WebSocketUpgrade},
    },
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use tokio::sync::OwnedSemaphorePermit;

use super::Weather;

pub(super) async fn upgrade(
    State(weather): State<Weather>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Response {
    if *weather.stop.borrow() {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    }
    if let Some(origin) = headers.get("origin")
        && !origin.to_str().ok().is_some_and(|value| {
            weather
                .allowed_origins
                .iter()
                .any(|allowed| value == allowed)
        })
    {
        return StatusCode::FORBIDDEN.into_response();
    }
    let Ok(permit) = Arc::clone(&weather.ws_slots).try_acquire_owned() else {
        return StatusCode::TOO_MANY_REQUESTS.into_response();
    };
    ws.max_message_size(16 * 1024)
        .max_frame_size(16 * 1024)
        .on_upgrade(move |socket| serve(socket, weather, permit))
        .into_response()
}

async fn send(socket: &mut WebSocket, message: Message) -> bool {
    matches!(
        tokio::time::timeout(Duration::from_secs(5), socket.send(message)).await,
        Ok(Ok(()))
    )
}

async fn send_snapshot(socket: &mut WebSocket, weather: &Weather) -> bool {
    let response = weather.stations_response(chrono::Utc::now()).await;
    match serde_json::to_string(&response) {
        Ok(json) => send(socket, Message::Text(json.into())).await,
        Err(_) => false,
    }
}

async fn serve(mut socket: WebSocket, weather: Weather, _permit: OwnedSemaphorePermit) {
    let mut updates = weather.updates.subscribe();
    let mut stop = weather.stop.subscribe();
    if *stop.borrow_and_update() {
        return;
    }
    updates.borrow_and_update();
    if !send_snapshot(&mut socket, &weather).await {
        return;
    }
    let mut heartbeat = tokio::time::interval_at(
        tokio::time::Instant::now() + Duration::from_secs(30),
        Duration::from_secs(30),
    );
    loop {
        tokio::select! {
            _ = stop.changed() => {
                if !send(&mut socket, Message::Close(None)).await {
                    tracing::debug!("weather subscriber close failed");
                }
                break;
            },
            changed = updates.changed() => {
                updates.borrow_and_update();
                if changed.is_err() || !send_snapshot(&mut socket, &weather).await { break; }
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
