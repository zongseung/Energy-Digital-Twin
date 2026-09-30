use axum::{
    Json,
    body::Bytes,
    extract::{Query, State, rejection::QueryRejection},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use chrono::{DateTime, Utc};
use futures_util::{
    FutureExt,
    future::{BoxFuture, WeakShared},
};
use serde::Deserialize;

use super::{
    Bridge, Error,
    cache::Entry,
    protocol::{Snapshot, parse_time, validate_assets},
};

const MAX_ASSETS: usize = 16 * 1024 * 1024;
pub(super) type AssetsFlight = WeakShared<BoxFuture<'static, Result<(Bytes, &'static str), Error>>>;

pub(super) async fn observation_at(bridge: &Bridge, at: DateTime<Utc>) -> Result<Snapshot, Error> {
    let mut url = bridge.url("/api/v1/jeju/state")?;
    url.query_pairs_mut().append_pair("at", &at.to_rfc3339());
    parse_snapshot(&fetch(bridge, url, 64 * 1024).await?, Some(at))
}

async fn fetch(bridge: &Bridge, url: reqwest::Url, limit: usize) -> Result<Vec<u8>, Error> {
    let mut response = bridge
        .http
        .get(url)
        .send()
        .await
        .map_err(|_| Error::Unavailable)?;
    match response.status() {
        StatusCode::OK => {}
        StatusCode::NOT_FOUND => return Err(Error::NotFound),
        StatusCode::UNPROCESSABLE_ENTITY => return Err(Error::InvalidQuery),
        _ => return Err(Error::Unavailable),
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| Error::Unavailable)? {
        if bytes.len().saturating_add(chunk.len()) > limit {
            return Err(Error::InvalidResponse);
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct StateQuery {
    at: Option<String>,
}

pub(super) async fn state(
    State(bridge): State<Bridge>,
    query: Result<Query<StateQuery>, QueryRejection>,
) -> Result<Json<Snapshot>, Error> {
    let Query(query) = query.map_err(|_| Error::InvalidQuery)?;
    let at = query.at.as_deref().map(parse_time).transpose()?;
    let mut url = bridge.url("/api/v1/jeju/state")?;
    if let Some(at) = at {
        url.query_pairs_mut().append_pair("at", &at.to_rfc3339());
    }
    let cache = Entry::new(&bridge, &url, if at.is_some() { 300 } else { 30 });
    if let Some(bytes) = cache.get(&bridge).await?
        && bytes.len() <= 64 * 1024
        && let Ok(snapshot) = parse_snapshot(&bytes, at)
    {
        return Ok(Json(snapshot));
    }
    let bytes = fetch(&bridge, url, 64 * 1024).await?;
    let snapshot = parse_snapshot(&bytes, at)?;
    cache.put(&bridge, &bytes).await;
    Ok(Json(snapshot))
}

fn parse_snapshot(bytes: &[u8], at: Option<DateTime<Utc>>) -> Result<Snapshot, Error> {
    let mut snapshot: Snapshot =
        serde_json::from_slice(bytes).map_err(|_| Error::InvalidResponse)?;
    snapshot.validate()?;
    if at.is_some_and(|at| at != snapshot.observed_at) {
        return Err(Error::InvalidResponse);
    }
    if at.is_none() {
        snapshot.refresh_delay(Utc::now());
    }
    Ok(snapshot)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct RangeQuery {
    start: String,
    end: String,
}

pub(super) async fn timeline(
    State(bridge): State<Bridge>,
    query: Result<Query<RangeQuery>, QueryRejection>,
) -> Result<Json<Vec<DateTime<Utc>>>, Error> {
    let Query(query) = query.map_err(|_| Error::InvalidQuery)?;
    let (start, end) = (parse_time(&query.start)?, parse_time(&query.end)?);
    if end <= start || end - start > chrono::Duration::days(7) {
        return Err(Error::InvalidQuery);
    }
    let mut url = bridge.url("/api/v1/jeju/timeline")?;
    url.query_pairs_mut()
        .append_pair("start", &start.to_rfc3339())
        .append_pair("end", &end.to_rfc3339());
    let cache = Entry::new(&bridge, &url, 300);
    if let Some(bytes) = cache.get(&bridge).await?
        && bytes.len() <= 128 * 1024
        && let Ok(times) = parse_timeline(&bytes, (start, end))
    {
        return Ok(Json(times));
    }
    let bytes = fetch(&bridge, url, 128 * 1024).await?;
    let times = parse_timeline(&bytes, (start, end))?;
    cache.put(&bridge, &bytes).await;
    Ok(Json(times))
}

fn parse_timeline(
    bytes: &[u8],
    (start, end): (DateTime<Utc>, DateTime<Utc>),
) -> Result<Vec<DateTime<Utc>>, Error> {
    let times: Vec<DateTime<Utc>> =
        serde_json::from_slice(bytes).map_err(|_| Error::InvalidResponse)?;
    if times.len() > 2016
        || times.iter().any(|t| *t < start || *t >= end)
        || times.windows(2).any(|w| w[0] >= w[1])
    {
        return Err(Error::InvalidResponse);
    }
    Ok(times)
}

pub(super) async fn assets(State(bridge): State<Bridge>) -> Result<Response, Error> {
    if !bridge.probe.bridge_ready().await {
        return Err(Error::Unavailable);
    }
    let request = {
        let mut flight = bridge.assets_flight.lock().await;
        if let Some(request) = flight
            .as_ref()
            .and_then(WeakShared::upgrade)
            .filter(|f| f.peek().is_none())
        {
            request
        } else {
            let request = load_assets(bridge.clone()).boxed().shared();
            *flight = request.downgrade();
            request
        }
    };
    let (bytes, cache) = request.await?;
    Ok(asset_response(bytes, cache))
}

async fn load_assets(bridge: Bridge) -> Result<(Bytes, &'static str), Error> {
    let url = bridge.url("/api/v1/jeju/assets")?;
    let cache = Entry::new(&bridge, &url, 3600);
    if let Some(bytes) = cache.get_after_health_check(&bridge).await
        && bytes.len() <= MAX_ASSETS
        && validate_assets(&bytes).is_ok()
    {
        return Ok((bytes.into(), "hit"));
    }
    let bytes = fetch(&bridge, url, MAX_ASSETS).await?;
    validate_assets(&bytes)?;
    let status = cache.put(&bridge, &bytes).await;
    Ok((bytes.into(), status))
}

fn asset_response(bytes: Bytes, cache: &'static str) -> Response {
    (
        [
            (header::CONTENT_TYPE, "application/geo+json"),
            (header::CACHE_CONTROL, "no-store"),
            (header::HeaderName::from_static("x-cache"), cache),
        ],
        bytes,
    )
        .into_response()
}
