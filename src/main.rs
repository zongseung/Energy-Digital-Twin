mod bridge;
mod config;
mod health;
mod simulation;
mod weather;

use std::{process::ExitCode, time::Duration};

use config::Settings;

#[tokio::main]
async fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "jeju_twin=info".into()),
        )
        .init();
    match run().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            tracing::error!("{message}");
            ExitCode::FAILURE
        }
    }
}

async fn run() -> Result<(), String> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.as_slice() == ["--help"] {
        println!(
            "jeju-twin [serve|check-config|healthcheck|simulate <file.json>]\n\
            BIND_ADDR=127.0.0.1:8090 REDIS_URL=redis://127.0.0.1:6380/0\n\
            BRIDGE_BASE_URL is optional until the bridge is ready.\n\
            /health/live: process liveness; /api/v1/health: dependency readiness.\n\
            simulate: offline regional/ESS scenario from explicit JSON input."
        );
        return Ok(());
    }
    let command = match args.as_slice() {
        [command, path] if command == "simulate" => return simulation::cli::run(path),
        [] => "serve",
        [command] if matches!(command.as_str(), "serve" | "check-config" | "healthcheck") => {
            command
        }
        _ => {
            return Err(
                "usage: jeju-twin [serve|check-config|healthcheck|simulate <file.json>|--help]"
                    .into(),
            );
        }
    };
    let settings = Settings::from_env().map_err(|error| error.to_string())?;
    if command == "check-config" {
        println!("configuration valid");
        return Ok(());
    }
    if command == "healthcheck" {
        let response = reqwest::Client::builder()
            .timeout(Duration::from_secs(3))
            .no_proxy()
            .build()
            .map_err(|_| "cannot initialize healthcheck")?
            .get(format!("http://{}/health/live", settings.bind_addr))
            .send()
            .await
            .map_err(|_| "liveness check failed")?;
        return if response.status().is_success() {
            Ok(())
        } else {
            Err("liveness check failed".into())
        };
    }
    let probe = health::Probe::new(&settings).map_err(|_| "cannot initialize HTTP client")?;
    let bridge = bridge::Bridge::new(&settings, probe.clone())
        .map_err(|_| "cannot initialize bridge client")?;
    let weather =
        weather::Weather::new(&settings).map_err(|_| "cannot initialize weather client")?;
    let listener = tokio::net::TcpListener::bind(settings.bind_addr)
        .await
        .map_err(|_| "cannot bind BIND_ADDR")?;
    tracing::info!(address = %settings.bind_addr, "API listening");
    let mut tasks = tokio::task::JoinSet::new();
    tasks.spawn(bridge.clone().run());
    tasks.spawn(weather.clone().run());
    let routes = health::router(probe)
        .merge(bridge::router(bridge.clone()))
        .merge(simulation::http::router(bridge.clone()))
        .merge(weather::router(weather.clone()));
    let stopping_bridge = bridge.clone();
    let stopping_weather = weather.clone();
    let result = axum::serve(listener, routes)
        .with_graceful_shutdown(async move {
            shutdown().await;
            stopping_bridge.stop();
            stopping_weather.stop();
        })
        .await
        .map_err(|_| "HTTP server failed".into());
    tasks.shutdown().await;
    bridge.drain_subscribers().await;
    weather.drain_subscribers().await;
    result
}

async fn shutdown() {
    #[cfg(unix)]
    {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut terminate) => {
                tokio::select! {
                    _ = tokio::signal::ctrl_c() => {},
                    _ = terminate.recv() => {},
                }
            }
            Err(_) => {
                let _ = tokio::signal::ctrl_c().await;
            }
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}
