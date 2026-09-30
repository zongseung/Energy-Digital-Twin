#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

use futures_util::StreamExt;
use std::{
    process::{Child, Command, Stdio},
    time::Duration,
};
use tokio_tungstenite::{connect_async, tungstenite::Message};

struct Process(Child);
impl Drop for Process {
    fn drop(&mut self) {
        if matches!(self.0.try_wait(), Ok(None)) {
            self.0.kill().expect("stop test process");
            self.0.wait().expect("reap test process");
        }
    }
}

#[tokio::test]
async fn sigterm_closes_all_real_process_subscribers() {
    let reservation = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let address = reservation.local_addr().unwrap();
    drop(reservation);
    let mut child = Process(
        Command::new(env!("CARGO_BIN_EXE_jeju-twin"))
            .env("BIND_ADDR", address.to_string())
            .env("BRIDGE_BASE_URL", "")
            .env("REDIS_URL", "redis://127.0.0.1:1/0")
            .env("ALLOWED_ORIGINS", "")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap(),
    );
    let http = reqwest::Client::new();
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if http
                .get(format!("http://{address}/health/live"))
                .send()
                .await
                .is_ok()
            {
                break;
            }
            assert!(
                child.0.try_wait().unwrap().is_none(),
                "API exited before ready"
            );
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    let mut sockets = Vec::new();
    for _ in 0..32 {
        let (mut socket, _) = connect_async(format!("ws://{address}/api/v1/jeju/ws"))
            .await
            .unwrap();
        assert!(matches!(socket.next().await, Some(Ok(Message::Text(_)))));
        sockets.push(socket);
    }
    assert!(
        Command::new("kill")
            .arg("-TERM")
            .arg(child.0.id().to_string())
            .status()
            .unwrap()
            .success()
    );
    let mut closed = 0;
    for socket in &mut sockets {
        if matches!(
            tokio::time::timeout(Duration::from_secs(12), socket.next()).await,
            Ok(Some(Ok(Message::Close(_))))
        ) {
            closed += 1;
        }
    }
    assert!(child.0.wait().unwrap().success());
    assert_eq!(
        closed,
        sockets.len(),
        "every upgraded connection must receive Close before runtime exits"
    );
}
