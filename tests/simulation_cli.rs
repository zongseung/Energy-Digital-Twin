#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

use serde_json::Value;
use std::{
    io::Write,
    process::{Command, Output},
    sync::atomic::{AtomicUsize, Ordering},
};

fn run_input(bytes: &[u8]) -> Output {
    static NEXT_FILE: AtomicUsize = AtomicUsize::new(0);
    let path = std::env::temp_dir().join(format!(
        "jeju-simulation-{}-{}.json",
        std::process::id(),
        NEXT_FILE.fetch_add(1, Ordering::Relaxed)
    ));
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
        .unwrap();
    file.write_all(bytes).unwrap();
    drop(file);
    let result = Command::new(env!("CARGO_BIN_EXE_jeju-twin"))
        .args(["simulate"])
        .arg(&path)
        .env("BIND_ADDR", "invalid-unused-for-offline")
        .output();
    std::fs::remove_file(path).unwrap();
    result.unwrap()
}

#[test]
fn offline_cli_returns_scenario_json_and_energy_result() {
    let output = run_input(include_bytes!("../examples/scenario.json"));
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["status"], "complete");
    assert_eq!(result["data_kind"], "scenario");
    assert!((result["final_scenario_mwh"].as_f64().unwrap() - 4.65).abs() < 1e-10);
}

#[test]
fn cli_reports_missing_data_without_inventing_energy() {
    let mut input: Value =
        serde_json::from_slice(include_bytes!("../examples/scenario.json")).unwrap();
    input["snapshots"][0]["demand_mw"] = Value::Null;
    let output = run_input(&serde_json::to_vec(&input).unwrap());
    assert!(output.status.success());
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["status"], "incomplete");
    assert_eq!(
        result["missing_intervals"],
        serde_json::json!(["2026-01-01T00:00:00Z"])
    );
    assert!(result["final_scenario_mwh"].is_null());
}

#[test]
fn invalid_input_exits_unsuccessfully_without_result_json() {
    for bytes in [b"{}".to_vec(), vec![b' '; 262_145]] {
        let output = run_input(&bytes);
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
        assert!(!output.stderr.is_empty());
    }
}
