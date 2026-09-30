use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};

use super::Error;

pub(super) const SOURCE: &str = "demand-postgres.public.jeju_supply_demand";

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct Snapshot {
    pub schema_version: u8,
    pub observed_at: DateTime<Utc>,
    pub source: String,
    pub source_timezone: String,
    pub quality_flags: Vec<String>,
    #[serde(deserialize_with = "Option::deserialize")]
    pub demand_mw: Option<f64>,
    #[serde(deserialize_with = "Option::deserialize")]
    pub supply_capacity_mw: Option<f64>,
    #[serde(deserialize_with = "Option::deserialize")]
    pub wind_mw: Option<f64>,
    #[serde(deserialize_with = "Option::deserialize")]
    pub solar_mw: Option<f64>,
    #[serde(deserialize_with = "Option::deserialize")]
    pub renewable_total_mw: Option<f64>,
}

impl Snapshot {
    pub(super) fn validate(&self) -> Result<(), Error> {
        if self.schema_version != 1 || self.source != SOURCE || self.source_timezone != "Asia/Seoul"
        {
            return Err(Error::InvalidResponse);
        }
        Ok(())
    }

    pub(super) fn refresh_delay(&mut self, now: DateTime<Utc>) {
        self.quality_flags
            .retain(|f| f != "source_delayed" && f != "source_in_future");
        if now - self.observed_at >= Duration::minutes(15) {
            self.quality_flags.push("source_delayed".into());
        } else if self.observed_at > now {
            self.quality_flags.push("source_in_future".into());
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(super) enum MessageType {
    Snapshot,
    Status,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(super) struct Envelope {
    #[serde(rename = "type")]
    pub kind: MessageType,
    pub schema_version: u8,
    pub observed_at: Option<DateTime<Utc>>,
    pub sent_at: DateTime<Utc>,
    pub state_version: u64,
    pub source: String,
    pub quality_flags: Vec<String>,
    pub data: Option<Snapshot>,
}

impl Envelope {
    pub(super) fn unavailable() -> Self {
        Self {
            kind: MessageType::Status,
            schema_version: 1,
            observed_at: None,
            sent_at: Utc::now(),
            state_version: 0,
            source: SOURCE.into(),
            quality_flags: vec!["bridge_disconnected".into()],
            data: None,
        }
    }

    pub(super) fn validate(&self) -> Result<(), Error> {
        if self.schema_version != 1
            || self.source != SOURCE
            || self.observed_at != self.data.as_ref().map(|s| s.observed_at)
        {
            return Err(Error::InvalidResponse);
        }
        if let Some(data) = &self.data {
            data.validate()?;
        }
        match self.kind {
            MessageType::Snapshot
                if self.data.is_none()
                    || self
                        .quality_flags
                        .iter()
                        .any(|f| f == "source_unavailable" || f == "bridge_disconnected") =>
            {
                Err(Error::InvalidResponse)
            }
            MessageType::Snapshot | MessageType::Status => Ok(()),
        }
    }

    pub(super) fn refresh(&mut self) {
        self.sent_at = Utc::now();
        self.quality_flags
            .retain(|f| f != "source_delayed" && f != "source_in_future");
        if let Some(data) = &mut self.data {
            data.refresh_delay(self.sent_at);
            for flag in &data.quality_flags {
                if !self.quality_flags.contains(flag) {
                    self.quality_flags.push(flag.clone());
                }
            }
        }
    }
}

pub(super) fn parse_time(value: &str) -> Result<DateTime<Utc>, Error> {
    DateTime::parse_from_rfc3339(value)
        .map(|t| t.to_utc())
        .map_err(|_| Error::InvalidQuery)
}

pub(super) fn validate_assets(bytes: &[u8]) -> Result<(), Error> {
    // Source-specific properties are intentionally opaque; preserve them byte for byte.
    let value: serde_json::Value =
        serde_json::from_slice(bytes).map_err(|_| Error::InvalidResponse)?;
    let valid = value["type"] == "FeatureCollection"
        && value["schema_version"] == 1
        && value["source"] == "energy-hub-db.public"
        && value["generated_at"]
            .as_str()
            .is_some_and(|s| parse_time(s).is_ok());
    if !valid {
        return Err(Error::InvalidResponse);
    }
    let features = value["features"].as_array().ok_or(Error::InvalidResponse)?;
    let mut ids = std::collections::HashSet::new();
    for feature in features {
        let id = feature["id"].as_str().ok_or(Error::InvalidResponse)?;
        let props = &feature["properties"];
        let kind = props["facility_kind"]
            .as_str()
            .ok_or(Error::InvalidResponse)?;
        let source_id = &props["source_id"];
        let source_id = match source_id {
            serde_json::Value::String(s) => s.clone(),
            serde_json::Value::Number(n) => n.to_string(),
            _ => return Err(Error::InvalidResponse),
        };
        if feature["type"] != "Feature"
            || !props.is_object()
            || !matches!(
                kind,
                "power_line" | "substation" | "power_plant" | "pv_facility"
            )
            || id != format!("hub:{kind}:{source_id}")
            || !ids.insert(id)
            || props["coordinate_system"] != "EPSG:4326"
            || !geometry(&feature["geometry"])
        {
            return Err(Error::InvalidResponse);
        }
    }
    Ok(())
}

fn geometry(value: &serde_json::Value) -> bool {
    let coords = &value["coordinates"];
    match value["type"].as_str() {
        Some("Point") => position(coords),
        Some("MultiPoint") => items(coords, 1, position),
        Some("LineString") => line(coords),
        Some("MultiLineString") => items(coords, 1, line),
        Some("Polygon") => polygon(coords),
        Some("MultiPolygon") => items(coords, 1, polygon),
        _ => false,
    }
}

fn items(value: &serde_json::Value, min: usize, valid: fn(&serde_json::Value) -> bool) -> bool {
    value
        .as_array()
        .is_some_and(|items| items.len() >= min && items.iter().all(valid))
}

fn position(value: &serde_json::Value) -> bool {
    value.as_array().is_some_and(|p| {
        (2..=3).contains(&p.len())
            && p.iter().all(|v| v.as_f64().is_some_and(f64::is_finite))
            && p[0].as_f64().is_some_and(|x| (-180.0..=180.0).contains(&x))
            && p[1].as_f64().is_some_and(|y| (-90.0..=90.0).contains(&y))
    })
}

fn line(value: &serde_json::Value) -> bool {
    items(value, 2, position)
}

fn polygon(value: &serde_json::Value) -> bool {
    items(value, 1, ring)
}

fn ring(value: &serde_json::Value) -> bool {
    if !items(value, 4, position) {
        return false;
    }
    let Some(points) = value.as_array() else {
        return false;
    };
    match (
        points.first().and_then(serde_json::Value::as_array),
        points.last().and_then(serde_json::Value::as_array),
    ) {
        (Some(a), Some(b)) => {
            a.len() == b.len()
                && a.iter().zip(b).all(|(a, b)| {
                    a.as_f64()
                        .zip(b.as_f64())
                        .is_some_and(|(a, b)| a.total_cmp(&b).is_eq())
                })
        }
        _ => false,
    }
}
