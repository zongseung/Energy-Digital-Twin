use super::{SimulationInput, simulate};
use std::{
    fs::File,
    io::{Read, Write},
};

pub(crate) fn run(path: &str) -> Result<(), String> {
    let file =
        File::open(path).map_err(|error| format!("cannot open simulation input: {error}"))?;
    let mut bytes = Vec::new();
    file.take(262_145)
        .read_to_end(&mut bytes)
        .map_err(|error| format!("cannot read simulation input: {error}"))?;
    if bytes.len() > 262_144 {
        return Err("simulation input exceeds 256 KiB".into());
    }
    let input: SimulationInput = serde_json::from_slice(&bytes)
        .map_err(|error| format!("invalid simulation JSON: {error}"))?;
    let result = simulate(&input).map_err(|error| error.to_string())?;
    let mut stdout = std::io::stdout().lock();
    serde_json::to_writer_pretty(&mut stdout, &result)
        .map_err(|error| format!("cannot write simulation result: {error}"))?;
    writeln!(stdout).map_err(|error| format!("cannot write simulation result: {error}"))
}
