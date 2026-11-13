mod json;
mod mcp;
mod process;
mod provider;
mod store;
mod validation;

use serde_json::Value;
use std::{
    env, fmt,
    path::{Path, PathBuf},
    time::Duration,
};

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Error {
    Input,
    Data,
    Provider,
    Disabled,
}
impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str(match self {
            Self::Input => "Invalid request or JSON input",
            Self::Data => "Provider returned invalid decision data; no answers returned",
            Self::Provider => "Provider operation failed; no answers returned",
            Self::Disabled => "Selected provider is disabled",
        })
    }
}
type Result<T> = std::result::Result<T, Error>;
#[derive(Clone, Debug)]
pub struct Options {
    provider: String,
    model: String,
    base: String,
    key_env: String,
    home: PathBuf,
    project: Option<PathBuf>,
    timeout: f64,
    disabled: Vec<String>,
    dry: bool,
    management: bool,
}
impl Default for Options {
    fn default() -> Self {
        let get = |n| env::var(n).unwrap_or_default();
        Self {
            provider: env::var("DECISION_PROVIDER")
                .ok()
                .filter(|v| !v.is_empty())
                .unwrap_or_else(|| "typesafe".into()),
            model: get("DECISION_MODEL"),
            base: get("DECISION_BASE_URL"),
            key_env: get("DECISION_API_KEY_ENV"),
            home: PathBuf::from(get("HOME")).join(".local/share/decision-prototype-rust"),
            project: None,
            timeout: 30.0,
            disabled: vec![],
            dry: false,
            management: false,
        }
    }
}
impl Options {
    fn duration(&self) -> Duration {
        Duration::from_secs_f64(self.timeout)
    }
}
fn parse(args: &[String]) -> Result<(Options, Vec<String>, String)> {
    let mut o = Options::default();
    let mut pos = Vec::new();
    let mut checksum = String::new();
    let mut args = args.iter();
    while let Some(arg) = args.next() {
        if !arg.starts_with("--") {
            pos.push(arg.clone());
            continue;
        }
        let (name, val) = arg
            .split_once('=')
            .map_or((arg.as_str(), None), |(a, b)| (a, Some(b)));
        match name {
            "--json" => continue,
            "--dry-run" => {
                o.dry = true;
                continue;
            }
            "--management" => {
                o.management = true;
                continue;
            }
            _ => (),
        }
        let val = val
            .or_else(|| args.next().map(String::as_str))
            .ok_or(Error::Input)?;
        match name {
            "--provider" => o.provider = val.into(),
            "--model" => o.model = val.into(),
            "--base-url" => o.base = val.into(),
            "--api-key-env" => o.key_env = val.into(),
            "--timeout" => o.timeout = val.parse().map_err(|_| Error::Input)?,
            "--disable-provider" => o.disabled.push(val.into()),
            "--plugin-home" => o.home = val.into(),
            "--project-config" => o.project = Some(val.into()),
            "--sha256" => checksum = val.into(),
            _ => return Err(Error::Input),
        }
    }
    if !o.timeout.is_finite()
        || o.timeout <= 0.0
        || o.timeout > i64::MAX as f64 / 1e9
        || !o.key_env.is_empty() && !store::env_name(&o.key_env)
    {
        return Err(Error::Input);
    }
    o.home = std::path::absolute(o.home).map_err(|_| Error::Input)?;
    o.project = o
        .project
        .map(std::path::absolute)
        .transpose()
        .map_err(|_| Error::Input)?;
    Ok((o, pos, checksum))
}
async fn timed<T>(o: &Options, f: impl Future<Output = Result<T>>) -> Result<T> {
    tokio::time::timeout(o.duration(), f)
        .await
        .map_err(|_| Error::Provider)?
}
fn print(v: Value) {
    println!("{}", v);
}
async fn run(args: Vec<String>) -> Result<()> {
    if args == ["--version"] {
        println!("decision prototype 1");
        return Ok(());
    }
    if args.iter().any(|s| s == "--help" || s == "-h") {
        println!(
            "decision prototype 1\nUsage: decision run REQUEST.json --json [provider options]\n       decision mcp [provider options] [--management]\n       decision providers install SOURCE [--sha256 HEX] [--plugin-home DIRECTORY] [--project-config FILE]\n       decision providers list|remove [NAME] [--plugin-home DIRECTORY] [--project-config FILE]\nProvider options: --provider NAME --model MODEL --base-url URL --api-key-env NAME --timeout SECONDS --disable-provider NAME --plugin-home DIRECTORY --project-config FILE\nRun options: --dry-run --json"
        );
        return Ok(());
    }
    let command = args.first().ok_or(Error::Input)?;
    let (mut o, pos, checksum) = parse(&args[1..])?;
    if command == "providers" {
        let v = match pos.as_slice() {
            [operation, source] if operation == "install" => {
                timed(&o, store::install(&o, source, &checksum)).await?
            }
            [operation] if operation == "list" => store::list(&o)?,
            [operation, name] if operation == "remove" => store::remove(&o, name)?,
            _ => return Err(Error::Input),
        };
        print(v);
        return Ok(());
    }
    if command == "mcp" && pos.is_empty() && o.management {
        return mcp::serve(o, None).await;
    }
    if !(command == "mcp" && pos.is_empty() || command == "run" && pos.len() == 1 && !o.management)
    {
        return Err(Error::Input);
    }
    let p = timed(&o, store::resolve(&o)).await?;
    provider::configure(&mut o, p.as_ref())?;
    if command == "mcp" {
        return mcp::serve(o, p).await;
    }
    let (b, directory) = if pos[0] == "-" {
        (
            validation::read_bounded(std::io::stdin().lock(), json::FRAME_LIMIT)?,
            env::current_dir().map_err(|_| Error::Input)?,
        )
    } else {
        let path = std::path::absolute(&pos[0]).map_err(|_| Error::Input)?;
        (
            validation::read_bounded(
                std::fs::File::open(&path).map_err(|_| Error::Input)?,
                json::FRAME_LIMIT,
            )?,
            path.parent().unwrap_or(Path::new(".")).to_path_buf(),
        )
    };
    let r = json::decode(&b)?;
    print(timed(&o, provider::evaluate(&o, p.as_ref(), r, true, &directory)).await?);
    Ok(())
}
#[tokio::main(flavor = "current_thread")]
async fn main() {
    let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        .expect("signal handler");
    let result = tokio::select! {
        r = run(env::args().skip(1).collect()) => r,
        _ = tokio::signal::ctrl_c() => Err(Error::Provider),
        _ = terminate.recv() => Err(Error::Provider),
    };
    process::drain().await;
    if let Err(e) = result {
        eprintln!("decision: {e}");
        std::process::exit(if matches!(e, Error::Input | Error::Disabled) {
            2
        } else {
            1
        });
    }
}

#[cfg(test)]
mod tests;
