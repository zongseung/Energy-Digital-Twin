use std::{collections::BTreeMap, sync::Arc, time::Duration};

use axum::{
    Json, Router,
    extract::State,
    http::{HeaderValue, header},
    routing::get,
};
use chrono::{DateTime, FixedOffset, NaiveDateTime, SecondsFormat, TimeDelta, TimeZone, Utc};
use serde::{Deserialize, Serialize};
use tokio::sync::{RwLock, Semaphore, watch};

use crate::config::Settings;

mod stream;

const DATA_URL: &str = "https://www.weather.go.kr/w/observation/land/aws-obs-data.do?db=MINDB_01M&stnId=0&sidoCode=5000000000";
const SOURCE_URL: &str = "https://www.weather.go.kr/w/observation/land/aws-obs.do";
const MAX_BYTES: usize = 1024 * 1024;
const POLL_SECONDS: u64 = 60;
const STALE_SECONDS: i64 = 900;
const RECEIVE_SECONDS: i64 = 150;

#[derive(Clone)]
pub(crate) struct Weather {
    client: reqwest::Client,
    url: String,
    cache: Arc<RwLock<Cache>>,
    updates: watch::Sender<u64>,
    stop: watch::Sender<bool>,
    allowed_origins: Arc<[String]>,
    ws_slots: Arc<Semaphore>,
}

#[derive(Default, Clone)]
struct Cache {
    stations: BTreeMap<u16, StationCache>,
    received_at: Option<DateTime<Utc>>,
    poll_attempted: bool,
    last_poll_succeeded: bool,
}

#[derive(Clone)]
struct StationCache {
    station: Station,
    observation: Option<Observation>,
    current: bool,
}

#[derive(Clone)]
struct Observation {
    observed_at: DateTime<Utc>,
    received_at: DateTime<Utc>,
    speed_m_s: f64,
    direction_from_deg: Option<f64>,
    direction_label: Option<&'static str>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Source {
    stn_id: String,
    sido_code: String,
    is_all_stn: bool,
    wind_unit: String,
    items: Vec<serde_json::Value>,
}

struct Batch {
    received_at: DateTime<Utc>,
    stations: BTreeMap<u16, StationCache>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Item {
    aws_stn_id: u16,
    aws_stn_name: String,
    lat: String,
    lon: String,
    tm: Option<serde_json::Value>,
    aws_ws10: Option<serde_json::Value>,
    aws_wd10: Option<serde_json::Value>,
    aws_trobl_knd: Option<serde_json::Value>,
}

#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
enum Status {
    Fresh,
    Stale,
    Unavailable,
}

#[derive(Clone, Serialize)]
struct Station {
    id: u16,
    name: String,
    latitude: f64,
    longitude: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    distance_km: Option<f64>,
}

#[derive(Serialize)]
struct WindResponse {
    status: Status,
    source: &'static str,
    source_url: &'static str,
    station: Station,
    observed_at: Option<String>,
    received_at: Option<String>,
    speed_m_s: Option<f64>,
    direction_from_deg: Option<f64>,
    direction_label: Option<&'static str>,
    directional_resolution_deg: f64,
    average_window_minutes: u8,
    quality_flags: Vec<&'static str>,
    poll_interval_seconds: u64,
    stale_after_seconds: i64,
}

#[derive(Serialize)]
struct StationsResponse {
    source: &'static str,
    source_url: &'static str,
    received_at: Option<String>,
    poll_interval_seconds: u64,
    stale_after_seconds: i64,
    station_count: usize,
    stations: Vec<WindResponse>,
}

impl Weather {
    pub(crate) fn new(settings: &Settings) -> Result<Self, reqwest::Error> {
        Self::with_url_and_origins(DATA_URL.into(), settings.allowed_origins.clone())
    }

    #[cfg(test)]
    fn with_url(url: String) -> Result<Self, reqwest::Error> {
        Self::with_url_and_origins(url, Vec::new())
    }

    fn with_url_and_origins(
        url: String,
        allowed_origins: Vec<String>,
    ) -> Result<Self, reqwest::Error> {
        Ok(Self {
            client: reqwest::Client::builder()
                .timeout(Duration::from_secs(8))
                .redirect(reqwest::redirect::Policy::none())
                .build()?,
            url,
            cache: Arc::new(RwLock::new(Cache::default())),
            updates: watch::channel(0).0,
            stop: watch::channel(false).0,
            allowed_origins: allowed_origins.into(),
            ws_slots: Arc::new(Semaphore::new(64)),
        })
    }

    pub(crate) fn stop(&self) {
        self.stop.send_replace(true);
    }

    pub(crate) async fn drain_subscribers(&self) {
        self.stop();
        if !matches!(
            tokio::time::timeout(
                Duration::from_secs(11),
                Arc::clone(&self.ws_slots).acquire_many_owned(64)
            )
            .await,
            Ok(Ok(_))
        ) {
            tracing::warn!("weather subscriber shutdown deadline exceeded");
        }
    }

    pub(crate) async fn run(self) {
        let mut interval = tokio::time::interval(Duration::from_secs(POLL_SECONDS));
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let mut stop = self.stop.subscribe();
        if *stop.borrow_and_update() {
            return;
        }
        loop {
            tokio::select! {
                _ = interval.tick() => self.poll().await, // The first tick polls immediately.
                _ = stop.changed() => break,
            }
        }
    }

    async fn poll(&self) {
        let result = tokio::time::timeout(Duration::from_secs(8), self.fetch()).await;
        let mut cache = self.cache.write().await;
        cache.poll_attempted = true;
        if let Ok(Some(batch)) = result {
            for station in cache.stations.values_mut() {
                station.current = false;
            }
            for (id, mut incoming) in batch.stations {
                if incoming.observation.is_none()
                    && let Some(previous) = cache.stations.get(&id)
                {
                    incoming.observation.clone_from(&previous.observation);
                }
                cache.stations.insert(id, incoming);
            }
            cache.received_at = Some(batch.received_at);
            cache.last_poll_succeeded = true;
        } else {
            cache.last_poll_succeeded = false;
            tracing::warn!("wind source unavailable or invalid");
        }
        drop(cache);
        self.updates
            .send_modify(|version| *version = version.wrapping_add(1));
    }

    async fn fetch(&self) -> Option<Batch> {
        let mut response = self.client.get(&self.url).send().await.ok()?;
        if !response.status().is_success()
            || response
                .content_length()
                .is_some_and(|length| length > MAX_BYTES as u64)
        {
            return None;
        }
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await.ok()? {
            if body.len().saturating_add(chunk.len()) > MAX_BYTES {
                return None;
            }
            body.extend_from_slice(&chunk);
        }
        parse(&body, Utc::now())
    }

    async fn response(&self, now: DateTime<Utc>) -> WindResponse {
        let cache = self.cache.read().await;
        let mut station = cache
            .stations
            .get(&185)
            .cloned()
            .unwrap_or_else(|| StationCache {
                station: Station {
                    id: 185,
                    name: "고산".into(),
                    latitude: 33.29382,
                    longitude: 126.16283,
                    distance_km: None,
                },
                observation: None,
                current: false,
            });
        // Distance from scene origin (33.34303, 126.17217), not each turbine.
        station.station.distance_km = Some(5.54);
        Self::station_response(&cache, &station, now)
    }

    async fn stations_response(&self, now: DateTime<Utc>) -> StationsResponse {
        let cache = self.cache.read().await;
        StationsResponse {
            source: "기상청 날씨누리",
            source_url: SOURCE_URL,
            received_at: cache
                .received_at
                .map(|time| time.to_rfc3339_opts(SecondsFormat::Secs, true)),
            poll_interval_seconds: POLL_SECONDS,
            stale_after_seconds: STALE_SECONDS,
            station_count: cache.stations.len(),
            stations: cache
                .stations
                .values()
                .map(|station| Self::station_response(&cache, station, now))
                .collect(),
        }
    }

    fn station_response(cache: &Cache, station: &StationCache, now: DateTime<Utc>) -> WindResponse {
        let observation = station.observation.as_ref();
        let status = match observation {
            None => Status::Unavailable,
            Some(value)
                if cache.last_poll_succeeded
                    && station.current
                    && (value.observed_at
                        ..=value.observed_at + TimeDelta::seconds(STALE_SECONDS))
                        .contains(&now)
                    && (value.received_at
                        ..=value.received_at + TimeDelta::seconds(RECEIVE_SECONDS))
                        .contains(&now) =>
            {
                Status::Fresh
            }
            Some(_) => Status::Stale,
        };
        let mut quality_flags = vec!["qc_not_provided"];
        if cache.poll_attempted && !cache.last_poll_succeeded {
            quality_flags.push("source_fetch_failed");
        }
        if cache.last_poll_succeeded && !station.current {
            quality_flags.push("station_data_missing");
        }
        if observation.is_some_and(|value| {
            now > value.observed_at + TimeDelta::seconds(STALE_SECONDS)
                || now > value.received_at + TimeDelta::seconds(RECEIVE_SECONDS)
        }) {
            quality_flags.push("source_delayed");
        }
        WindResponse {
            status,
            source: "기상청 날씨누리",
            source_url: SOURCE_URL,
            station: station.station.clone(),
            observed_at: observation
                .map(|value| value.observed_at.to_rfc3339_opts(SecondsFormat::Secs, true)),
            received_at: observation
                .map(|value| value.received_at.to_rfc3339_opts(SecondsFormat::Secs, true)),
            speed_m_s: observation.map(|value| value.speed_m_s),
            direction_from_deg: observation.and_then(|value| value.direction_from_deg),
            direction_label: observation.and_then(|value| value.direction_label),
            directional_resolution_deg: 22.5,
            average_window_minutes: 10,
            quality_flags,
            poll_interval_seconds: POLL_SECONDS,
            stale_after_seconds: STALE_SECONDS,
        }
    }
}

fn parse(body: &[u8], received_at: DateTime<Utc>) -> Option<Batch> {
    let source: Source = serde_json::from_slice(body).ok()?;
    if source.stn_id != "0"
        || source.sido_code != "5000000000"
        || !source.is_all_stn
        || source.wind_unit != "m/s"
    {
        return None;
    }
    let mut stations: BTreeMap<u16, StationCache> = BTreeMap::new();
    for raw in source.items {
        let Ok(item) = serde_json::from_value::<Item>(raw) else {
            continue;
        };
        let Some(station) = parse_station(&item) else {
            continue;
        };
        let observation = parse_observation(&item, received_at);
        match stations.entry(station.id) {
            std::collections::btree_map::Entry::Vacant(entry) => {
                entry.insert(StationCache {
                    station,
                    current: observation.is_some(),
                    observation,
                });
            }
            std::collections::btree_map::Entry::Occupied(mut entry) => {
                let old = entry.get_mut();
                if old.station.name == station.name
                    && (old.station.latitude - station.latitude).abs() < 0.000_001
                    && (old.station.longitude - station.longitude).abs() < 0.000_001
                    && let Some(observation) = observation
                    && old
                        .observation
                        .as_ref()
                        .is_none_or(|value| observation.observed_at > value.observed_at)
                {
                    old.observation = Some(observation);
                    old.current = true;
                }
            }
        }
    }
    if stations.is_empty() {
        return None;
    }
    Some(Batch {
        received_at,
        stations,
    })
}

fn parse_station(item: &Item) -> Option<Station> {
    let latitude = item.lat.parse::<f64>().ok()?;
    let longitude = item.lon.parse::<f64>().ok()?;
    if item.aws_stn_id == 0
        || item.aws_stn_name.trim().is_empty()
        || !latitude.is_finite()
        || !longitude.is_finite()
        || !(32.0..=34.5).contains(&latitude)
        || !(125.0..=127.5).contains(&longitude)
        || (item.aws_stn_id == 185
            && (item.aws_stn_name != "고산"
                || (latitude - 33.29382).abs() > 0.01
                || (longitude - 126.16283).abs() > 0.01))
    {
        return None;
    }
    Some(Station {
        id: item.aws_stn_id,
        name: item.aws_stn_name.clone(),
        latitude,
        longitude,
        distance_km: None,
    })
}

fn parse_observation(item: &Item, received_at: DateTime<Utc>) -> Option<Observation> {
    if !item.aws_trobl_knd.as_ref()?.as_str()?.is_empty() {
        return None;
    }
    let kst = FixedOffset::east_opt(9 * 3600)?;
    let local = NaiveDateTime::parse_from_str(item.tm.as_ref()?.as_str()?, "%Y%m%d%H%M").ok()?;
    let observed_at = kst
        .from_local_datetime(&local)
        .single()?
        .with_timezone(&Utc);
    if observed_at > received_at + TimeDelta::seconds(60) {
        return None;
    }
    let speed_m_s = item.aws_ws10.as_ref()?.as_str()?.parse::<f64>().ok()?;
    if !speed_m_s.is_finite() || speed_m_s < 0.0 {
        return None;
    }
    let (direction_from_deg, direction_label) = if speed_m_s == 0.0 {
        (None, None)
    } else {
        let label = item.aws_wd10.as_ref()?.as_str()?;
        let &(label, degrees) = DIRECTIONS.iter().find(|(name, _)| *name == label)?;
        (Some(degrees), Some(label))
    };
    Some(Observation {
        observed_at,
        received_at,
        speed_m_s,
        direction_from_deg,
        direction_label,
    })
}

const DIRECTIONS: [(&str, f64); 16] = [
    ("북", 0.0),
    ("북북동", 22.5),
    ("북동", 45.0),
    ("동북동", 67.5),
    ("동", 90.0),
    ("동남동", 112.5),
    ("남동", 135.0),
    ("남남동", 157.5),
    ("남", 180.0),
    ("남남서", 202.5),
    ("남서", 225.0),
    ("서남서", 247.5),
    ("서", 270.0),
    ("서북서", 292.5),
    ("북서", 315.0),
    ("북북서", 337.5),
];

async fn wind(State(weather): State<Weather>) -> impl axum::response::IntoResponse {
    (
        [(header::CACHE_CONTROL, HeaderValue::from_static("no-store"))],
        Json(weather.response(Utc::now()).await),
    )
}

async fn stations(State(weather): State<Weather>) -> impl axum::response::IntoResponse {
    (
        [(header::CACHE_CONTROL, HeaderValue::from_static("no-store"))],
        Json(weather.stations_response(Utc::now()).await),
    )
}

pub(crate) fn router(weather: Weather) -> Router {
    Router::new()
        .route("/api/v1/jeju/wind", get(wind))
        .route("/api/v1/jeju/wind/stations", get(stations))
        .route("/api/v1/jeju/wind/ws", get(stream::upgrade))
        .with_state(weather)
}

#[cfg(test)]
#[allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::needless_pass_by_value,
    reason = "test assertions and compact fixtures"
)]
mod tests {
    use std::sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    };

    use axum::http::StatusCode;
    use futures_util::StreamExt;
    use serde_json::{Value, json};
    use tokio_tungstenite::{
        connect_async,
        tungstenite::{Message as ClientMessage, client::IntoClientRequest},
    };

    use super::*;

    fn at(time: &str) -> DateTime<Utc> {
        DateTime::parse_from_rfc3339(time)
            .unwrap()
            .with_timezone(&Utc)
    }

    fn source(items: Value) -> Value {
        json!({
            "stnId": "0", "sidoCode": "5000000000", "isAllStn": true,
            "windUnit": "m/s", "items": items
        })
    }

    fn item(id: u16, name: &str, tm: &str, speed: &str, direction: &str) -> Value {
        let (lat, lon) = if id == 185 {
            ("33.29382", "126.16283")
        } else {
            ("33.31822", "126.23050")
        };
        json!({
            "awsStnId": id, "awsStnName": name, "lat": lat, "lon": lon,
            "tm": tm, "awsWs10": speed, "awsWd10": direction, "awsTroblKnd": ""
        })
    }

    fn parsed(value: &Value, now: DateTime<Utc>) -> Option<Batch> {
        parse(&serde_json::to_vec(value).unwrap(), now)
    }

    fn observation(batch: &Batch, id: u16) -> &Observation {
        batch.stations[&id].observation.as_ref().unwrap()
    }

    #[test]
    fn parses_real_jeju_batch_shape_with_43_stations() {
        let batch = parse(
            include_str!("weather_fixture.json").as_bytes(),
            at("2026-09-29T18:03:00Z"),
        )
        .unwrap();
        assert_eq!(batch.stations.len(), 43);
        assert_eq!(batch.stations[&185].station.name, "고산");
        assert_eq!(observation(&batch, 185).direction_from_deg, Some(45.0));
        assert!(
            batch
                .stations
                .values()
                .all(|value| value.observation.is_some())
        );
    }

    #[test]
    fn selects_newest_usable_item_time_and_calm_has_no_direction() {
        let now = at("2026-09-29T17:31:00Z");
        let value = source(json!([
            item(185, "고산", "202609300228", "3.5", "북동"),
            item(185, "고산", "202609300230", "-99", "북"),
            item(185, "고산", "202609300229", "0", "북")
        ]));
        let batch = parsed(&value, now).unwrap();
        let reading = observation(&batch, 185);
        assert_eq!(reading.observed_at, at("2026-09-29T17:29:00Z"));
        assert!(reading.speed_m_s.abs() < f64::EPSILON);
        assert_eq!(reading.direction_from_deg, None);
        assert_eq!(reading.direction_label, None);
    }

    #[test]
    fn rejects_invalid_root_and_metadata_but_keeps_other_stations() {
        let now = at("2026-09-29T17:31:00Z");
        let valid = source(json!([
            item(185, "고산", "202609300230", "3.5", "북동"),
            item(990, "낙천", "202609300230", "2", "남")
        ]));
        for (pointer, replacement) in [
            ("/stnId", json!("185")),
            ("/sidoCode", json!("1100000000")),
            ("/isAllStn", json!(false)),
            ("/windUnit", json!("km/h")),
        ] {
            let mut invalid = valid.clone();
            *invalid.pointer_mut(pointer).unwrap() = replacement;
            assert!(parsed(&invalid, now).is_none(), "accepted {pointer}");
        }
        for (pointer, replacement) in [
            ("/items/1/awsStnName", json!("")),
            ("/items/1/lat", json!("NaN")),
            ("/items/1/lon", json!("Infinity")),
            ("/items/1/lat", json!("40")),
            ("/items/1/awsStnId", json!(0)),
            ("/items/1/awsStnId", json!("bad")),
        ] {
            let mut invalid = valid.clone();
            *invalid.pointer_mut(pointer).unwrap() = replacement;
            let batch = parsed(&invalid, now).unwrap();
            assert_eq!(batch.stations.len(), 1, "kept {pointer}");
            assert!(batch.stations.contains_key(&185));
        }
    }

    #[test]
    fn invalid_wind_keeps_station_without_claiming_observation() {
        let now = at("2026-09-29T17:31:00Z");
        let valid = source(json!([
            item(185, "고산", "202609300230", "3.5", "북동"),
            item(990, "낙천", "202609300230", "2", "남")
        ]));
        for (pointer, replacement) in [
            ("/items/1/awsTroblKnd", json!("장애")),
            ("/items/1/awsTroblKnd", Value::Null),
            ("/items/1/awsWs10", json!(".")),
            ("/items/1/awsWs10", json!("-1")),
            ("/items/1/awsWs10", json!("NaN")),
            ("/items/1/awsWs10", json!(3.5)),
            ("/items/1/awsWd10", json!("NNE")),
            ("/items/1/awsWd10", json!(45)),
            ("/items/1/tm", json!("202609300233")),
            ("/items/1/tm", json!(202_609_300_230_u64)),
            ("/items/1/tm", Value::Null),
        ] {
            let mut invalid = valid.clone();
            *invalid.pointer_mut(pointer).unwrap() = replacement;
            let batch = parsed(&invalid, now).unwrap();
            assert_eq!(batch.stations.len(), 2);
            assert!(batch.stations[&185].observation.is_some());
            assert!(
                batch.stations[&990].observation.is_none(),
                "accepted {pointer}"
            );
        }
    }

    #[tokio::test]
    async fn partial_rows_and_batch_failure_retain_old_values_as_stale() {
        let body = Arc::new(RwLock::new(String::new()));
        let upstream = Router::new().route(
            "/",
            get({
                let body = Arc::clone(&body);
                move || {
                    let body = Arc::clone(&body);
                    async move { body.read().await.clone() }
                }
            }),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let source_url = format!("http://{}/", listener.local_addr().unwrap());
        let server = tokio::spawn(async move { axum::serve(listener, upstream).await });
        let weather = Weather::with_url(source_url).unwrap();
        let tm = (Utc::now() + TimeDelta::hours(9))
            .format("%Y%m%d%H%M")
            .to_string();
        *body.write().await = source(json!([
            item(185, "고산", &tm, "3", "북동"),
            item(990, "낙천", &tm, "2", "남")
        ]))
        .to_string();
        weather.poll().await;
        let fresh = weather.stations_response(Utc::now()).await;
        assert_eq!(fresh.station_count, 2);
        assert!(
            fresh
                .stations
                .iter()
                .all(|value| matches!(value.status, Status::Fresh))
        );
        *body.write().await = source(json!([item(185, "고산", &tm, ".", "북동")])).to_string();
        weather.poll().await;
        let partial = weather.stations_response(Utc::now()).await;
        assert_eq!(partial.station_count, 2);
        assert!(
            partial
                .stations
                .iter()
                .all(|value| matches!(value.status, Status::Stale))
        );
        assert_eq!(partial.stations[0].speed_m_s, Some(3.0));
        assert_eq!(partial.stations[1].speed_m_s, Some(2.0));
        assert!(
            partial.stations[0]
                .quality_flags
                .contains(&"station_data_missing")
        );
        *body.write().await = "{}".into();
        weather.poll().await;
        let failed = weather.stations_response(Utc::now()).await;
        assert_eq!(failed.station_count, 2);
        assert!(
            failed
                .stations
                .iter()
                .all(|value| matches!(value.status, Status::Stale))
        );
        assert!(
            failed.stations[0]
                .quality_flags
                .contains(&"source_fetch_failed")
        );
        server.abort();
    }

    #[tokio::test]
    async fn both_routes_read_one_batch_cache_without_upstream_requests() {
        let calls = Arc::new(AtomicUsize::new(0));
        let upstream = Router::new().route(
            "/",
            get({
                let calls = Arc::clone(&calls);
                move || {
                    let calls = Arc::clone(&calls);
                    async move {
                        calls.fetch_add(1, Ordering::SeqCst);
                        let tm = (Utc::now() + TimeDelta::hours(9))
                            .format("%Y%m%d%H%M")
                            .to_string();
                        source(json!([
                            item(185, "고산", &tm, "4", "북동"),
                            item(990, "낙천", &tm, "2", "남")
                        ]))
                        .to_string()
                    }
                }
            }),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let source_url = format!("http://{}/", listener.local_addr().unwrap());
        let upstream_task = tokio::spawn(async move { axum::serve(listener, upstream).await });
        let weather = Weather::with_url(source_url).unwrap();
        weather.poll().await;
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let api_url = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move { axum::serve(listener, router(weather)).await });
        let client = reqwest::Client::new();
        for _ in 0..3 {
            let old = client
                .get(format!("{api_url}/api/v1/jeju/wind"))
                .send()
                .await
                .unwrap();
            assert_eq!(old.status(), StatusCode::OK);
            assert_eq!(old.headers()[header::CACHE_CONTROL], "no-store");
            let old: Value = old.json().await.unwrap();
            assert_eq!(old["status"], "fresh");
            assert_eq!(old["station"]["distance_km"], 5.54);
            let batch = client
                .get(format!("{api_url}/api/v1/jeju/wind/stations"))
                .send()
                .await
                .unwrap();
            assert_eq!(batch.status(), StatusCode::OK);
            assert_eq!(batch.headers()[header::CACHE_CONTROL], "no-store");
            let batch: Value = batch.json().await.unwrap();
            assert_eq!(batch["station_count"], 2);
            assert_eq!(batch["stations"][0]["station"]["id"], 185);
            assert!(batch["stations"][0]["station"].get("distance_km").is_none());
        }
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        upstream_task.abort();
        server.abort();
    }

    #[tokio::test]
    #[allow(
        clippy::too_many_lines,
        reason = "one end-to-end socket lifecycle scenario"
    )]
    async fn websocket_fans_out_latest_cache_and_closes_on_shutdown() {
        let calls = Arc::new(AtomicUsize::new(0));
        let body = Arc::new(RwLock::new(String::new()));
        let tm = (Utc::now() + TimeDelta::hours(9))
            .format("%Y%m%d%H%M")
            .to_string();
        let mut items = vec![item(185, "고산", &tm, "3", "북동")];
        items.extend((200..242).map(|id| item(id, "제주", &tm, "2", "남")));
        *body.write().await = source(json!(items)).to_string();
        let upstream = Router::new().route(
            "/",
            get({
                let body = Arc::clone(&body);
                let calls = Arc::clone(&calls);
                move || {
                    let body = Arc::clone(&body);
                    let calls = Arc::clone(&calls);
                    async move {
                        calls.fetch_add(1, Ordering::SeqCst);
                        body.read().await.clone()
                    }
                }
            }),
        );
        let source_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let source_url = format!("http://{}/", source_listener.local_addr().unwrap());
        let source_task = tokio::spawn(async move { axum::serve(source_listener, upstream).await });
        let weather =
            Weather::with_url_and_origins(source_url, vec!["https://allowed.example".into()])
                .unwrap();
        weather.poll().await;
        let api_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let api = format!("http://{}", api_listener.local_addr().unwrap());
        let ws_url = api.replacen("http", "ws", 1) + "/api/v1/jeju/wind/ws";
        let api_weather = weather.clone();
        let api_task =
            tokio::spawn(async move { axum::serve(api_listener, router(api_weather)).await });

        let mut denied = ws_url.clone().into_client_request().unwrap();
        denied
            .headers_mut()
            .insert("origin", "https://denied.example".parse().unwrap());
        let rejected = connect_async(denied).await.unwrap_err();
        assert_http_error(rejected, StatusCode::FORBIDDEN);

        let (mut first, _) = connect_async(&ws_url).await.unwrap();
        let mut allowed = ws_url.clone().into_client_request().unwrap();
        allowed
            .headers_mut()
            .insert("origin", "https://allowed.example".parse().unwrap());
        let (mut second, _) = connect_async(allowed).await.unwrap();
        let initial_first = next_ws(&mut first).await;
        let initial_second = next_ws(&mut second).await;
        assert_eq!(initial_first, initial_second);
        assert_eq!(initial_first["station_count"], 43);
        assert_eq!(initial_first["stations"][0]["status"], "fresh");
        assert_eq!(calls.load(Ordering::SeqCst), 1);

        *body.write().await = source(json!([item(185, "고산", &tm, "7", "남")])).to_string();
        weather.poll().await;
        for socket in [&mut first, &mut second] {
            let update = next_ws(socket).await;
            assert_eq!(update["stations"][0]["speed_m_s"], 7.0);
            assert_eq!(update["stations"][0]["status"], "fresh");
        }
        *body.write().await = "{}".into();
        weather.poll().await;
        for socket in [&mut first, &mut second] {
            let failed = next_ws(socket).await;
            assert_eq!(failed["stations"][0]["status"], "stale");
            assert_eq!(failed["stations"][0]["speed_m_s"], 7.0);
            assert_eq!(failed["station_count"], 43);
            assert!(
                failed["stations"][0]["quality_flags"]
                    .as_array()
                    .unwrap()
                    .contains(&json!("source_fetch_failed"))
            );
        }
        let (mut reconnected, _) = connect_async(&ws_url).await.unwrap();
        assert_eq!(
            next_ws(&mut reconnected).await["stations"][0]["status"],
            "stale"
        );
        assert_eq!(calls.load(Ordering::SeqCst), 3);

        let held = weather.ws_slots.acquire_many(61).await.unwrap();
        let rejected = connect_async(&ws_url).await.unwrap_err();
        assert_http_error(rejected, StatusCode::TOO_MANY_REQUESTS);
        drop(held);
        first.close(None).await.unwrap();
        tokio::time::timeout(Duration::from_secs(2), async {
            while weather.ws_slots.available_permits() < 62 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .unwrap();
        let (mut resumed, _) = connect_async(&ws_url).await.unwrap();
        assert_eq!(next_ws(&mut resumed).await["station_count"], 43);
        weather.stop();
        assert!(matches!(
            tokio::time::timeout(Duration::from_secs(3), second.next())
                .await
                .unwrap(),
            Some(Ok(ClientMessage::Close(_)))
        ));
        tokio::time::timeout(Duration::from_secs(3), weather.drain_subscribers())
            .await
            .unwrap();
        let rejected = connect_async(&ws_url).await.unwrap_err();
        assert_http_error(rejected, StatusCode::SERVICE_UNAVAILABLE);
        api_task.abort();
        source_task.abort();
    }

    async fn next_ws(
        socket: &mut tokio_tungstenite::WebSocketStream<
            tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
        >,
    ) -> Value {
        tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                match socket.next().await.unwrap().unwrap() {
                    ClientMessage::Text(text) => return serde_json::from_str(&text).unwrap(),
                    ClientMessage::Ping(_) | ClientMessage::Pong(_) => {}
                    frame => panic!("unexpected frame: {frame:?}"),
                }
            }
        })
        .await
        .unwrap()
    }

    fn assert_http_error(error: tokio_tungstenite::tungstenite::Error, status: StatusCode) {
        match error {
            tokio_tungstenite::tungstenite::Error::Http(response) => {
                assert_eq!(response.status(), status);
            }
            other => panic!("unexpected handshake error: {other}"),
        }
    }
}
