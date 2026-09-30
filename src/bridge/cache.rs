use std::{
    hash::{Hash, Hasher},
    sync::atomic::Ordering,
    time::Duration,
};

use super::{Bridge, Error, protocol::MessageType};

pub(super) struct Entry {
    key: String,
    ttl: u64,
    epoch: Option<u64>,
}

impl Entry {
    pub(super) fn new(bridge: &Bridge, url: &reqwest::Url, ttl: u64) -> Self {
        if url.path() == "/api/v1/jeju/assets" {
            return Self {
                key: bridge.cache_key.to_string(),
                ttl,
                epoch: None,
            };
        }
        let mut hash = std::collections::hash_map::DefaultHasher::new();
        url.hash(&mut hash);
        let epoch = bridge.cache_epoch.load(Ordering::SeqCst);
        Self {
            key: format!(
                "{}:{}:{epoch}:{:x}",
                bridge.cache_key,
                bridge.cache_session,
                hash.finish()
            ),
            ttl,
            epoch: Some(epoch),
        }
    }

    fn eligible(&self, bridge: &Bridge) -> bool {
        self.epoch.is_none_or(|epoch| {
            epoch == bridge.cache_epoch.load(Ordering::SeqCst)
                && bridge.live.borrow().kind == MessageType::Snapshot
        })
    }

    pub(super) async fn get(&self, bridge: &Bridge) -> Result<Option<Vec<u8>>, Error> {
        if !self.eligible(bridge) {
            return Ok(None);
        }
        if !bridge.probe.bridge_ready().await {
            return Err(Error::Unavailable);
        }
        Ok(self.get_after_health_check(bridge).await)
    }

    pub(super) async fn get_after_health_check(&self, bridge: &Bridge) -> Option<Vec<u8>> {
        if !self.eligible(bridge) {
            return None;
        }
        let result = tokio::time::timeout(Duration::from_secs(1), async {
            let mut conn = bridge.probe.redis_connection().await?;
            redis::cmd("GET")
                .arg(&self.key)
                .query_async::<Option<Vec<u8>>>(&mut conn)
                .await
        })
        .await;
        match result {
            Ok(Ok(bytes)) if self.eligible(bridge) => bytes,
            Ok(Ok(_) | Err(_)) | Err(_) => None,
        }
    }

    pub(super) async fn put(&self, bridge: &Bridge, bytes: &[u8]) -> &'static str {
        if !self.eligible(bridge) {
            return "bypass";
        }
        let result = tokio::time::timeout(Duration::from_secs(1), async {
            let mut conn = bridge.probe.redis_connection().await?;
            redis::cmd("SET")
                .arg(&self.key)
                .arg(bytes)
                .arg("EX")
                .arg(self.ttl)
                .query_async::<String>(&mut conn)
                .await
        })
        .await;
        match result {
            Ok(Ok(reply)) if reply == "OK" => "miss",
            Ok(Ok(_) | Err(_)) | Err(_) => {
                tracing::warn!("cache unavailable; serving bridge response");
                "bypass"
            }
        }
    }
}
