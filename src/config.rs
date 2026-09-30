use std::net::SocketAddr;

use reqwest::Url;
use thiserror::Error;

pub(crate) struct Settings {
    pub(crate) bind_addr: SocketAddr,
    pub(crate) bridge_health_url: Option<Url>,
    pub(crate) redis: redis::Client,
    pub(crate) allowed_origins: Vec<String>,
}

#[derive(Debug, Error)]
#[error("invalid {0}")]
pub(crate) struct ConfigError(&'static str);

impl Settings {
    pub(crate) fn from_env() -> Result<Self, ConfigError> {
        for key in [
            "BIND_ADDR",
            "BRIDGE_BASE_URL",
            "REDIS_URL",
            "ALLOWED_ORIGINS",
        ] {
            if matches!(std::env::var(key), Err(std::env::VarError::NotUnicode(_))) {
                return Err(ConfigError(key));
            }
        }
        Self::parse(|key| std::env::var(key).ok())
    }

    pub(crate) fn parse(get: impl Fn(&str) -> Option<String>) -> Result<Self, ConfigError> {
        let bind_addr: SocketAddr = get("BIND_ADDR")
            .unwrap_or_else(|| "127.0.0.1:8090".into())
            .parse()
            .map_err(|_| ConfigError("BIND_ADDR"))?;
        if !bind_addr.ip().is_loopback() || bind_addr.port() == 0 {
            return Err(ConfigError("BIND_ADDR"));
        }
        let bridge_health_url = get("BRIDGE_BASE_URL")
            .filter(|value| !value.is_empty())
            .map(|value| {
                let mut url = Url::parse(&value).map_err(|_| ConfigError("BRIDGE_BASE_URL"))?;
                if !matches!(url.scheme(), "http" | "https")
                    || url.host_str().is_none()
                    || !url.username().is_empty()
                    || url.password().is_some()
                    || url.query().is_some()
                    || url.fragment().is_some()
                    || url.path() != "/"
                {
                    return Err(ConfigError("BRIDGE_BASE_URL"));
                }
                url.set_path("/api/v1/health");
                Ok(url)
            })
            .transpose()?;
        let redis_url = get("REDIS_URL").unwrap_or_else(|| "redis://127.0.0.1:6380/0".into());
        let redis = redis::Client::open(redis_url).map_err(|_| ConfigError("REDIS_URL"))?;
        Ok(Self {
            bind_addr,
            bridge_health_url,
            redis,
            allowed_origins: get("ALLOWED_ORIGINS")
                .unwrap_or_default()
                .split(',')
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(str::to_owned)
                .collect(),
        })
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod tests {
    use super::*;

    #[test]
    fn defaults_allow_startup_before_bridge_exists() {
        let settings = Settings::parse(|_| None).unwrap();
        assert_eq!(settings.bind_addr.to_string(), "127.0.0.1:8090");
        assert!(settings.bridge_health_url.is_none());
    }

    #[test]
    fn invalid_settings_do_not_expose_values() {
        for (key, value) in [
            ("BRIDGE_BASE_URL", "https://user:secret@example.com"),
            ("BRIDGE_BASE_URL", "file:///secret"),
            ("BRIDGE_BASE_URL", "http://example.com?secret=token"),
            ("BRIDGE_BASE_URL", "http://example.com/api"),
            ("REDIS_URL", "redis://:secret@host:bad"),
            ("BIND_ADDR", "0.0.0.0:8090"),
        ] {
            let result = Settings::parse(|name| (name == key).then(|| value.to_owned()));
            let error = result
                .err()
                .expect("invalid settings must fail")
                .to_string();
            assert!(error.contains(key));
            assert!(!error.contains("secret"));
        }
    }
}
