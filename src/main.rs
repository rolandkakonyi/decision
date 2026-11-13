mod cli;
mod json;
mod mcp;
mod output;
mod plugin;
mod process;
mod provider;
mod providers;
mod skill;
mod store;
mod validation;

use std::{env, fmt, path::PathBuf, time::Duration};
#[derive(Debug, Clone, PartialEq)]
pub enum Error {
    Input,
    Data,
    Provider,
    Disabled,
    Interrupted,
    InputMessage(String),
    ExecutionMessage(String),
}
impl Error {
    pub fn input(message: impl Into<String>) -> Self {
        Self::InputMessage(message.into())
    }
    pub fn execution(message: impl Into<String>) -> Self {
        Self::ExecutionMessage(message.into())
    }
    pub fn exit_code(&self) -> i32 {
        if matches!(self, Self::Interrupted) {
            return 130;
        }
        if matches!(self, Self::Input | Self::Disabled | Self::InputMessage(_)) {
            2
        } else {
            1
        }
    }
}
impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str(match self {
            Self::Interrupted => "Interrupted",
            Self::Input => "Invalid request or JSON input",
            Self::Data => "Provider returned invalid decision data; no answers returned",
            Self::Provider => "Provider operation failed; no answers returned",
            Self::Disabled => "Selected provider is disabled for this invocation; choose another --provider or remove its --disable-provider flag",
            Self::InputMessage(s) | Self::ExecutionMessage(s) => s,
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
    account_id: String,
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
            account_id: env::var("DECISION_ACCOUNT_ID")
                .ok()
                .filter(|v| !v.is_empty())
                .unwrap_or_else(|| get("CLOUDFLARE_ACCOUNT_ID")),
            home: cli::home_dir().join(".local/share/decision"),
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
async fn timed<T>(o: &Options, f: impl Future<Output = Result<T>>) -> Result<T> {
    tokio::time::timeout(o.duration(), f)
        .await
        .map_err(|_| Error::Provider)?
}
async fn run(args: Vec<std::ffi::OsString>) -> Result<()> {
    let matches = match cli::parse(args) {
        Ok(m) => m,
        Err(e) => {
            let code = e.exit_code();
            let _ = e.print();
            std::process::exit(code);
        }
    };
    let (name, m) = matches.subcommand().unwrap();
    if name == "skill" {
        let output = cli::get(m, "output").map(cli::expand_home).transpose()?;
        return skill::export(output.as_deref(), cli::enabled(m, "check"));
    }
    if name == "plugin" {
        return plugin::export(
            &cli::expand_home(cli::get(m, "output").unwrap())?,
            cli::get(m, "integration").unwrap(),
            cli::get(m, "mcp-command").unwrap(),
            &cli::many(m, "env-var"),
            cli::enabled(m, "check"),
        );
    }
    if name == "providers" {
        let (name, m) = m.subcommand().unwrap();
        let o = cli::options(m)?;
        let result = match name {
            "install" => {
                timed(
                    &o,
                    store::install(
                        &o,
                        cli::get(m, "source").unwrap(),
                        cli::get(m, "sha256").unwrap_or(""),
                    ),
                )
                .await?
            }
            "list" => store::list(&o)?,
            "remove" => store::remove(&o, cli::get(m, "name").unwrap())?,
            _ => unreachable!(),
        };
        println!("{result}");
        return Ok(());
    }
    let mut o = cli::options(m)?;
    if name == "mcp" && o.management {
        return mcp::serve(o, None).await;
    }
    let p = timed(&o, store::resolve(&o)).await?;
    provider::configure(&mut o, p.as_ref())?;
    if name == "mcp" {
        return mcp::serve(o, p).await;
    }
    let (r, directory) = cli::make_request(name, m, &o)?;
    let result = timed(&o, provider::evaluate(&o, p.as_ref(), r, true, &directory)).await?;
    if cli::enabled(&matches, "json") {
        println!("{result}");
    } else if o.dry {
        println!("{}", output::preview(&result));
    } else {
        println!("{}", output::result(&result));
    }
    Ok(())
}
async fn termination() {
    #[cfg(unix)]
    {
        if let Ok(mut signal) =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        {
            signal.recv().await;
        } else {
            std::future::pending::<()>().await;
        }
    }
    #[cfg(not(unix))]
    std::future::pending::<()>().await;
}
#[tokio::main(flavor = "current_thread")]
async fn main() {
    let result = tokio::select! {r=run(env::args_os().skip(1).collect())=>r,_=tokio::signal::ctrl_c()=>Err(Error::Interrupted),_=termination()=>Err(Error::Provider)};
    process::drain().await;
    if let Err(e) = result {
        eprintln!("decision: {e}");
        std::process::exit(e.exit_code());
    }
}
#[cfg(test)]
mod tests;
