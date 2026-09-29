use chrono::Utc;
use futures_util::TryStreamExt;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::{
    Row,
    postgres::{PgConnectOptions, PgPoolOptions},
};
use std::{collections::HashSet, env, path::Path, process::ExitCode, str::FromStr, time::Duration};
use tokio::{
    fs,
    io::{AsyncReadExt, AsyncWriteExt, BufWriter},
    process::Command,
};

const ROOT: &str = "/mnt/iscsi/energy-digital-twin/geography/jeju";
const BBOX: [f64; 4] = [126.0, 33.0, 127.0, 33.7];
const API: &str = "https://api.vworld.kr/req/data";
const TABLES: [&str; 7] = [
    "admin_boundary",
    "road",
    "landcover",
    "power_line",
    "substation",
    "power_plant",
    "pv_facility",
];
type Result<T> = std::result::Result<T, &'static str>;

fn cells() -> Vec<[f64; 4]> {
    (0..34)
        .flat_map(|x| {
            (0..24).map(move |y| {
                [
                    126.0 + f64::from(x * 3) / 100.0,
                    33.0 + f64::from(y * 3) / 100.0,
                    (126.0 + f64::from((x + 1) * 3) / 100.0).min(127.0),
                    (33.0 + f64::from((y + 1) * 3) / 100.0).min(33.7),
                ]
            })
        })
        .collect()
}

fn number(value: &Value) -> Result<u64> {
    value
        .as_u64()
        .or_else(|| value.as_str()?.parse().ok())
        .ok_or("vworld_invalid_count")
}

fn page(payload: &Value, expected: u64) -> Result<(&[Value], u64, u64)> {
    let r = &payload["response"];
    let total = number(&r["record"]["total"])?;
    let count = number(&r["record"]["current"])?;
    let pages = number(&r["page"]["total"])?;
    if number(&r["page"]["current"])? != expected || pages == 0 || expected > pages {
        return Err("vworld_invalid_page");
    }
    if r["status"] == "NOT_FOUND" && total == 0 && count == 0 && pages == 1 {
        if r["result"]["featureCollection"]["features"]
            .as_array()
            .is_some_and(|features| !features.is_empty())
        {
            return Err("vworld_incomplete_page");
        }
        return Ok((&[], 0, 1));
    }
    if r["status"] != "OK" {
        return Err("vworld_request_rejected");
    }
    let features = r["result"]["featureCollection"]["features"]
        .as_array()
        .ok_or("vworld_missing_features")?;
    if features.len() as u64 != count || count > total || (total > 0 && count == 0) {
        return Err("vworld_incomplete_page");
    }
    for f in features {
        if f["type"] != "Feature"
            || f["id"].as_str().is_none_or(|id| id.is_empty())
            || f["geometry"].is_null()
            || !f["properties"].is_object()
        {
            return Err("vworld_invalid_feature");
        }
    }
    Ok((features, total, pages))
}

fn setting(file: &str, name: &str) -> Result<String> {
    if let Ok(value) = env::var(name) {
        return Ok(value);
    }
    // Only selected fields enter settings; no keys or URLs are formatted in errors.
    dotenvy::from_path_iter(file)
        .map_err(|_| "configuration_file_unavailable")?
        .filter_map(std::result::Result::ok)
        .find(|(key, _)| key == name)
        .map(|(_, value)| value)
        .ok_or("required_configuration_missing")
}

async fn digest(path: &Path) -> Result<String> {
    let mut file = fs::File::open(path)
        .await
        .map_err(|_| "dataset_read_failed")?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let n = file
            .read(&mut buffer)
            .await
            .map_err(|_| "dataset_read_failed")?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

async fn json_file(path: &Path, data: &Value) -> Result<()> {
    let part = path.with_extension("json.part");
    let bytes = serde_json::to_vec_pretty(data).map_err(|_| "metadata_encoding_failed")?;
    let mut file = fs::File::create(&part)
        .await
        .map_err(|_| "metadata_write_failed")?;
    file.write_all(&bytes)
        .await
        .map_err(|_| "metadata_write_failed")?;
    file.sync_all().await.map_err(|_| "metadata_sync_failed")?;
    drop(file);
    fs::rename(part, path)
        .await
        .map_err(|_| "metadata_publish_failed")
}

async fn existing(root: &Path, name: &str) -> Result<bool> {
    let path = root.join(name);
    let meta = root.join(format!("{name}.metadata.json"));
    if !fs::try_exists(&path)
        .await
        .map_err(|_| "dataset_stat_failed")?
    {
        return Ok(false);
    }
    let bytes = match fs::read(meta).await {
        Ok(bytes) => bytes,
        // A crash between data and metadata publication leaves an incomplete file.
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(_) => return Err("dataset_metadata_read_failed"),
    };
    let value: Value = serde_json::from_slice(&bytes).map_err(|_| "dataset_metadata_invalid")?;
    if value["complete"] != true
        || value["bbox"] != json!(BBOX)
        || value["schema_version"] != 1
        || value["sha256"] != digest(&path).await?
    {
        return Err("existing_dataset_verification_failed");
    }
    println!("reuse {name}");
    Ok(true)
}

async fn finish(name: &str, mut metadata: Value) -> Result<()> {
    let root = Path::new(ROOT);
    let part = root.join(format!("{name}.part"));
    metadata["schema_version"] = json!(1);
    metadata["bbox"] = json!(BBOX);
    metadata["crs"] = json!("EPSG:4326");
    metadata["complete"] = json!(true);
    metadata["collected_at"] = json!(Utc::now());
    metadata["file"] = json!(name);
    metadata["bytes"] = json!(
        fs::metadata(&part)
            .await
            .map_err(|_| "dataset_stat_failed")?
            .len()
    );
    metadata["sha256"] = json!(digest(&part).await?);
    // Publish metadata last. A data file alone never establishes completion.
    fs::rename(&part, root.join(name))
        .await
        .map_err(|_| "dataset_publish_failed")?;
    json_file(&root.join(format!("{name}.metadata.json")), &metadata).await?;
    println!(
        "complete {name}: {} features, {} bytes",
        metadata["feature_count"], metadata["bytes"]
    );
    Ok(())
}

async fn feature(writer: &mut BufWriter<fs::File>, value: &Value) -> Result<()> {
    let mut bytes = serde_json::to_vec(value).map_err(|_| "feature_encoding_failed")?;
    bytes.push(b'\n');
    writer
        .write_all(&bytes)
        .await
        .map_err(|_| "dataset_write_failed")
}

async fn flush(mut writer: BufWriter<fs::File>) -> Result<()> {
    writer.flush().await.map_err(|_| "dataset_write_failed")?;
    writer
        .get_ref()
        .sync_all()
        .await
        .map_err(|_| "dataset_sync_failed")
}

async fn export_db() -> Result<()> {
    let url = setting("bridge/.env", "HUB_DATABASE_URL")?;
    let options = PgConnectOptions::from_str(&url)
        .map_err(|_| "invalid_hub_configuration")?
        .host("127.0.0.1")
        .port(5437);
    let pool = PgPoolOptions::new()
        .max_connections(2)
        .acquire_timeout(Duration::from_secs(5))
        .after_connect(|connection, _| {
            Box::pin(async move {
                sqlx::query("SET default_transaction_read_only = on")
                    .execute(&mut *connection)
                    .await?;
                sqlx::query("SET statement_timeout = '300s'")
                    .execute(&mut *connection)
                    .await?;
                Ok(())
            })
        })
        .connect_lazy_with(options);
    for table in TABLES {
        let name = format!("{table}.geojsonl");
        if existing(Path::new(ROOT), &name).await? {
            continue;
        }
        let mut connection = pool.acquire().await.map_err(|_| "hub_unavailable")?;
        connection.close_on_drop();
        // Bounding-box candidates retain full geometry. Expensive clipping belongs
        // to GPU-side scene preparation, not the source collection transaction.
        let filter = if table == "power_line" {
            "(p.sido = '제주특별자치도' OR p.geom && ST_MakeEnvelope(126,33,127,33.7,4326))"
        } else {
            "p.geom && ST_MakeEnvelope(126,33,127,33.7,4326)"
        };
        let sql = format!(
            "SELECT jsonb_build_object('type','Feature','id','hub:{table}:' || p.id,'geometry',ST_AsGeoJSON(p.geom)::jsonb,'properties',(to_jsonb(p)-'geom') || jsonb_build_object('source_table','public.{table}','source_id',p.id,'coordinate_system','EPSG:4326')) AS feature FROM public.{table} p WHERE p.geom IS NOT NULL AND {filter} ORDER BY p.id"
        );
        let mut rows = sqlx::query(&sql).fetch(&mut *connection);
        let mut writer = BufWriter::new(
            fs::File::create(Path::new(ROOT).join(format!("{name}.part")))
                .await
                .map_err(|_| "dataset_write_failed")?,
        );
        let mut count = 0u64;
        while let Some(row) = tokio::time::timeout(Duration::from_secs(30), rows.try_next())
            .await
            .map_err(|_| "hub_read_timeout")?
            .map_err(|_| "hub_query_failed")?
        {
            let sqlx::types::Json(value): sqlx::types::Json<Value> =
                row.try_get("feature").map_err(|_| "hub_decode_failed")?;
            feature(&mut writer, &value).await?;
            count += 1;
        }
        flush(writer).await?;
        finish(&name, json!({"feature_count":count,"source":format!("energy-hub-db.public.{table}"),"format":"GeoJSONL",
            "selection":"bbox_overlap_full_geometry","source_dates":"preserved_in_properties","license":"see_source_dataset",
            "quality_flags": if table == "admin_boundary" {vec!["historical_boundary_codes_preserved"]} else {vec![]}
        })).await?;
    }
    let _ = tokio::time::timeout(Duration::from_secs(3), pool.close()).await;
    Ok(())
}

async fn request(
    client: &reqwest::Client,
    key: &str,
    layer: &str,
    bbox: [f64; 4],
    index: u64,
) -> Result<Value> {
    let area = format!(
        "BOX({:.6},{:.6},{:.6},{:.6})",
        bbox[0], bbox[1], bbox[2], bbox[3]
    );
    for attempt in 0..3 {
        tokio::time::sleep(Duration::from_millis(300 * (1 << attempt))).await;
        let response = client
            .get(API)
            .query(&[
                ("service", "data"),
                ("version", "2.0"),
                ("request", "GetFeature"),
                ("data", layer),
                ("key", key),
                ("format", "json"),
                ("crs", "EPSG:4326"),
                ("geometry", "true"),
                ("attribute", "true"),
                ("size", "1000"),
                ("page", &index.to_string()),
                ("geomFilter", &area),
            ])
            .send()
            .await;
        if let Ok(mut response) = response
            && response.status().is_success()
        {
            let mut bytes = Vec::new();
            loop {
                let chunk = response.chunk().await.map_err(|_| "vworld_read_failed")?;
                let Some(chunk) = chunk else {
                    break;
                };
                if bytes.len() + chunk.len() > 64 * 1024 * 1024 {
                    return Err("vworld_response_too_large");
                }
                bytes.extend_from_slice(&chunk);
            }
            return serde_json::from_slice(&bytes).map_err(|_| "vworld_invalid_json");
        }
    }
    // reqwest errors include the query URL and must never reach stderr.
    Err("vworld_transport_failed")
}

async fn checked_page<'a>(
    cache: &Path,
    cell: usize,
    payload: &'a Value,
    index: u64,
    expected: &mut Option<(u64, u64)>,
    raw_count: &mut u64,
    cell_ids: &mut HashSet<String>,
) -> Result<(&'a [Value], u64)> {
    let result = (|| {
        let (features, total, pages) = page(payload, index)?;
        if expected.is_some_and(|old| old != (total, pages)) {
            return Err("vworld_total_changed_during_pagination");
        }
        *expected = Some((total, pages));
        *raw_count += features.len() as u64;
        for value in features {
            let id = value["id"].as_str().ok_or("vworld_missing_id")?;
            if !cell_ids.insert(id.to_owned()) {
                return Err("vworld_repeated_pagination_id");
            }
        }
        if index == pages && *raw_count != total {
            return Err("vworld_incomplete_cell");
        }
        if pages > 2000 {
            return Err("vworld_page_limit");
        }
        Ok((features, pages))
    })();
    if result.is_err() {
        // A moving provider snapshot must be refetched for this cell on restart.
        let prefix = format!("cell-{cell:03}-page-");
        let mut entries = fs::read_dir(cache).await.map_err(|_| "cache_read_failed")?;
        while let Some(entry) = entries
            .next_entry()
            .await
            .map_err(|_| "cache_read_failed")?
        {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name.starts_with(&prefix)
                && (name.ends_with(".json") || name.ends_with(".json.part"))
            {
                fs::remove_file(entry.path())
                    .await
                    .map_err(|_| "cache_invalidation_failed")?;
            }
        }
    }
    result
}

async fn export_layer(
    client: &reqwest::Client,
    key: &str,
    name: &str,
    layer: &str,
    areas: Vec<[f64; 4]>,
) -> Result<()> {
    if existing(Path::new(ROOT), name).await? {
        return Ok(());
    }
    let cache = Path::new(ROOT).join("vworld_pages").join(layer);
    fs::create_dir_all(&cache)
        .await
        .map_err(|_| "cache_directory_failed")?;
    let mut writer = BufWriter::new(
        fs::File::create(Path::new(ROOT).join(format!("{name}.part")))
            .await
            .map_err(|_| "dataset_write_failed")?,
    );
    let mut ids = HashSet::new();
    let mut count = 0u64;
    for (cell, bbox) in areas.iter().enumerate() {
        let mut index = 1u64;
        let mut expected_total = None;
        let mut raw_count = 0u64;
        let mut cell_ids = HashSet::new();
        loop {
            let path = cache.join(format!("cell-{cell:03}-page-{index:04}.json"));
            let cached = fs::try_exists(&path)
                .await
                .map_err(|_| "cache_stat_failed")?;
            let payload: Value = if cached {
                serde_json::from_slice(&fs::read(&path).await.map_err(|_| "cache_read_failed")?)
                    .map_err(|_| "cache_invalid_json")?
            } else {
                let payload = request(client, key, layer, *bbox, index).await?;
                // Retain only data and pagination, never request URLs or keys.
                let sanitized = json!({"response":{
                    "status":payload["response"]["status"],"record":payload["response"]["record"],
                    "page":payload["response"]["page"],"result":payload["response"]["result"]
                }});
                sanitized
            };
            let (features, pages) = checked_page(
                &cache,
                cell,
                &payload,
                index,
                &mut expected_total,
                &mut raw_count,
                &mut cell_ids,
            )
            .await?;
            if !cached {
                json_file(&path, &payload).await?;
            }
            for value in features {
                let id = value["id"].as_str().ok_or("vworld_missing_id")?;
                if ids.insert(id.to_owned()) {
                    feature(&mut writer, value).await?;
                    count += 1;
                }
            }
            if index == pages {
                break;
            }
            index += 1;
            if index > 2000 {
                return Err("vworld_page_limit");
            }
        }
        if cell % 25 == 0 {
            println!(
                "{layer}: {}/{} cells, {count} unique features",
                cell + 1,
                areas.len()
            );
        }
    }
    flush(writer).await?;
    finish(name, json!({"feature_count":count,"source":API,"source_layer":layer,"format":"GeoJSONL",
        "grid_cell_count":areas.len(),"selection":"complete_bbox_grid_full_geometry","deduplication":"provider_feature_id",
        "source_date":null,"license":"VWorld provider terms; source attribution required",
        "quality_flags":if layer == "LT_C_SPBD" {vec!["building_height_not_provided","floor_count_is_not_height"]} else {vec![]}
    })).await
}

async fn export_api() -> Result<()> {
    let key = setting(".env", "vworld_key")?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(45))
        .connect_timeout(Duration::from_secs(5))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| "https_client_failed")?;
    export_layer(
        &client,
        &key,
        "coastline.geojsonl",
        "LT_L_TOISDEPCNTAH",
        vec![BBOX],
    )
    .await?;
    export_layer(
        &client,
        &key,
        "vworld_admin_boundary.geojsonl",
        "LT_C_ADSIGG_INFO",
        vec![BBOX],
    )
    .await?;
    export_layer(&client, &key, "buildings.geojsonl", "LT_C_SPBD", cells()).await
}

async fn export_dem() -> Result<()> {
    let name = "dem_jeju.tif";
    if existing(Path::new(ROOT), name).await? {
        return Ok(());
    }
    let part = Path::new(ROOT).join(format!("{name}.part"));
    let result = tokio::time::timeout(
        Duration::from_secs(120),
        Command::new("/mnt/nvme/Energy-hub/.venv/bin/python")
            .arg("-c")
            .arg(include_str!("../../scripts/export_dem.py"))
            .arg("/mnt/nvme/Energy-hub/research/data/raw/dem_korea.tif")
            .arg(part)
            .kill_on_drop(true)
            .output(),
    )
    .await
    .map_err(|_| "dem_timeout")?
    .map_err(|_| "dem_tool_unavailable")?;
    if !result.status.success() {
        return Err("dem_extraction_failed");
    }
    let mut metadata: Value =
        serde_json::from_slice(&result.stdout).map_err(|_| "dem_metadata_invalid")?;
    metadata["source"] = json!("/mnt/nvme/Energy-hub/research/data/raw/dem_korea.tif");
    metadata["format"] = json!("GeoTIFF");
    metadata["quality_flags"] = json!([
        "source_vertical_datum_unverified",
        "source_license_unverified"
    ]);
    finish(name, metadata).await
}

#[tokio::main]
async fn main() -> ExitCode {
    let root = Path::new(ROOT);
    if fs::create_dir_all(root).await.is_err() {
        eprintln!("collection_directory_unwritable");
        return ExitCode::FAILURE;
    }
    let (db, api, dem) = tokio::join!(export_db(), export_api(), export_dem());
    let errors: Vec<&str> = [db, api, dem]
        .into_iter()
        .filter_map(std::result::Result::err)
        .collect();
    let mut datasets = Vec::new();
    let mut names: Vec<String> = TABLES
        .iter()
        .map(|table| format!("{table}.geojsonl"))
        .collect();
    names.extend(
        [
            "coastline.geojsonl",
            "vworld_admin_boundary.geojsonl",
            "buildings.geojsonl",
            "dem_jeju.tif",
        ]
        .map(str::to_owned),
    );
    for name in names {
        if let Ok(bytes) = fs::read(root.join(format!("{name}.metadata.json"))).await
            && let Ok(value) = serde_json::from_slice::<Value>(&bytes)
        {
            datasets.push(value);
        }
    }
    let complete = errors.is_empty() && datasets.len() == 11;
    let manifest = json!({"schema_version":1,"complete":complete,"generated_at":Utc::now(),
        "bbox":BBOX,"crs":"EPSG:4326","datasets":datasets,"errors":errors,
        "notes":["GIS records are not electrical topology","Building floors do not establish measured height",
                 "Historical administrative boundary codes and dates are preserved"]});
    if json_file(&root.join("manifest.json"), &manifest)
        .await
        .is_err()
    {
        eprintln!("manifest_write_failed");
        return ExitCode::FAILURE;
    }
    for error in errors {
        eprintln!("{error}");
    }
    println!("collection_complete={complete}");
    if complete {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn restart_rebuilds_data_without_completion_metadata() {
        let root = env::temp_dir().join(format!(
            "jeju-recovery-{}-{}",
            std::process::id(),
            Utc::now().timestamp_nanos_opt().unwrap()
        ));
        fs::create_dir(&root).await.unwrap();
        fs::write(root.join("roads.geojsonl"), b"uncommitted data")
            .await
            .unwrap();
        let result = existing(&root, "roads.geojsonl").await;
        fs::remove_dir_all(root).await.unwrap();
        assert_eq!(result, Ok(false));
    }

    #[tokio::test]
    async fn inconsistent_pagination_discards_only_the_affected_cell() {
        let root = env::temp_dir().join(format!(
            "jeju-pages-{}-{}",
            std::process::id(),
            Utc::now().timestamp_nanos_opt().unwrap()
        ));
        fs::create_dir(&root).await.unwrap();
        let first = root.join("cell-000-page-0001.json");
        let next = root.join("cell-000-page-0002.json");
        let other = root.join("cell-001-page-0001.json");
        let good = json!({"response":{"status":"OK","record":{"total":"2","current":"1"},"page":{"total":"2","current":"1"},"result":{"featureCollection":{"features":[{"id":"building.1","type":"Feature","geometry":{"type":"MultiPolygon","coordinates":[]},"properties":{}}]}}}});
        fs::write(&other, b"{}").await.unwrap();
        for failure in ["total", "duplicate", "incomplete"] {
            fs::write(&first, b"{}").await.unwrap();
            fs::write(&next, b"{}").await.unwrap();
            let mut expected = None;
            let mut count = 0;
            let mut ids = HashSet::new();
            checked_page(&root, 0, &good, 1, &mut expected, &mut count, &mut ids)
                .await
                .unwrap();
            let mut bad = good.clone();
            bad["response"]["page"]["current"] = json!(2);
            if failure == "total" {
                bad["response"]["record"]["total"] = json!(3);
            } else if failure == "incomplete" {
                bad["response"]["record"]["current"] = json!(0);
                bad["response"]["result"]["featureCollection"]["features"] = json!([]);
            }
            assert!(
                checked_page(&root, 0, &bad, 2, &mut expected, &mut count, &mut ids)
                    .await
                    .is_err()
            );
            assert!(
                !fs::try_exists(&first).await.unwrap(),
                "{failure} leaves a poisoned checkpoint"
            );
            assert!(!fs::try_exists(&next).await.unwrap());
            assert!(fs::try_exists(&other).await.unwrap());
        }
        fs::remove_dir_all(root).await.unwrap();
    }

    #[test]
    fn grid_covers_the_region_with_requests_below_ten_square_kilometres() {
        let cells = cells();
        assert_eq!(cells.len(), 816);
        assert_eq!(cells[0], [126.0, 33.0, 126.03, 33.03]);
        assert_eq!(cells.last().unwrap()[2..], [127.0, 33.7]);
        let mut area = 0.0;
        for [west, south, east, north] in cells {
            assert!(east > west && north > south);
            assert!((east - west) * (north - south) * 10000.0 < 10.0);
            area += (east - west) * (north - south);
        }
        assert!((area - 0.7_f64).abs() < 1e-9);
    }

    #[test]
    fn pagination_rejects_errors_and_truncated_pages() {
        let good = json!({"response":{"status":"OK","record":{"total":"1","current":"1"},"page":{"total":"1","current":"1"},"result":{"featureCollection":{"features":[{"id":"building.1","type":"Feature","geometry":{"type":"MultiPolygon","coordinates":[]},"properties":{"gro_flo_co":2}}]}}}});
        let (features, total, pages) = page(&good, 1).unwrap();
        assert_eq!((features.len(), total, pages), (1, 1, 1));
        assert!(features[0]["properties"].get("height").is_none());
        let mut truncated = good.clone();
        truncated["response"]["record"]["current"] = json!(2);
        assert!(page(&truncated, 1).is_err());
        assert!(page(&good, 2).is_err());
        assert!(page(&json!({"response":{"status":"ERROR"}}), 1).is_err());
        let empty = json!({"response":{"status":"NOT_FOUND","record":{"total":"0","current":"0"},"page":{"total":"1","current":"1"}}});
        assert_eq!(page(&empty, 1).unwrap(), (&[][..], 0, 1));
        let mut contradictory = empty;
        contradictory["response"]["result"] = good["response"]["result"].clone();
        assert!(page(&contradictory, 1).is_err());
    }
}
