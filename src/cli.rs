use crate::{Error, Options, Result, json, validation};
use clap::{Arg, ArgAction, ArgMatches, Command};
use serde_json::{Value, json};
use std::{
    ffi::OsString,
    path::{Path, PathBuf},
};

fn value(name: &'static str, help: &'static str) -> Arg {
    Arg::new(name).long(name).help(help)
}
fn flag(name: &'static str, help: &'static str) -> Arg {
    value(name, help).action(ArgAction::SetTrue)
}
fn scope(command: Command) -> Command {
    command
        .arg(value(
            "plugin-home",
            "Personal provider installation directory",
        ))
        .arg(value(
            "project-config",
            "Explicit project provider registry file",
        ))
}
fn connection(command: Command) -> Command {
    scope(command)
        .arg(value(
            "provider",
            "Built-in typesafe, openai, cloudflare, gemini, or installed provider",
        ))
        .arg(value("model", "Model override (default: DECISION_MODEL)"))
        .arg(value(
            "base-url",
            "Provider API base URL (default: DECISION_BASE_URL or provider environment)",
        ))
        .arg(value(
            "api-key-env",
            "Environment variable holding the provider key",
        ))
        .arg(value(
            "account-id",
            "Cloudflare account ID (default: DECISION_ACCOUNT_ID or CLOUDFLARE_ACCOUNT_ID)",
        ))
        .arg(value("timeout", "Finite positive timeout in seconds").default_value("30"))
        .arg(
            value(
                "disable-provider",
                "Disable a provider without loading it; repeat",
            )
            .action(ArgAction::Append),
        )
}
pub fn command() -> Command {
    let mut cli = Command::new("decision")
        .version(env!("CARGO_PKG_VERSION"))
        .about("Ask typed questions through TypeSafe, OpenAI/Gemini Decisions, or Cloudflare Clef.")
        .arg_required_else_help(true)
        .subcommand_required(true)
        .arg(flag("json", "Print evaluation or request preview as JSON").global(true));
    for name in ["noul", "choice", "score", "box", "ask", "run", "mcp"] {
        let mut sub = connection(Command::new(name));
        if ["noul", "choice", "score", "box"].contains(&name) {
            sub = sub.arg(Arg::new("question").required(true));
        }
        if name == "run" {
            sub = sub.arg(
                Arg::new("request-file")
                    .required(true)
                    .help("JSON request file; - reads stdin"),
            );
        }
        if name != "run" && name != "mcp" {
            sub = sub
                .arg(value("state", "Literal text context").conflicts_with("state-file"))
                .arg(value("state-file", "JSON state file; - reads stdin"));
        }
        if name == "ask" {
            sub = sub.arg(
                value(
                    "questions-file",
                    "JSON object of named questions; - reads stdin",
                )
                .required(true),
            );
        }
        if name == "choice" {
            sub = sub.arg(
                value("option", "Unique NAME=description; repeat")
                    .required(true)
                    .action(ArgAction::Append),
            );
        }
        if name == "score" {
            sub = sub.arg(
                value("level", "Ordered score level; repeat")
                    .required(true)
                    .action(ArgAction::Append),
            );
        }
        if name == "mcp" {
            sub = sub.arg(flag(
                "management",
                "Expose provider install/list/remove without inference",
            ));
        } else {
            sub = sub
                .arg(flag(
                    "dry-run",
                    "Preview the validated request without contacting a provider",
                ))
                .arg(
                    value("image", "Attach PNG/JPEG as [NAME=]PATH; repeat")
                        .action(ArgAction::Append),
                );
        }
        cli = cli.subcommand(sub);
    }
    cli.subcommand(
        Command::new("skill")
            .about("Print or export the companion Agent Skill; no API access")
            .arg(value(
                "output",
                "Write SKILL.md and references into this directory",
            ))
            .arg(flag("check", "Verify an exported skill matches this tool")),
    )
    .subcommand(
        Command::new("plugin")
            .about("Export a Codex plugin and local marketplace; no API access")
            .arg(value("output", "Generated marketplace directory").required(true))
            .arg(
                value("integration", "Integration mode")
                    .value_parser(["cli", "mcp"])
                    .default_value("mcp"),
            )
            .arg(
                value(
                    "mcp-command",
                    "MCP executable name or absolute path, without arguments",
                )
                .default_value("decision"),
            )
            .arg(
                value(
                    "env-var",
                    "Additional environment variable name to forward; repeat",
                )
                .action(ArgAction::Append),
            )
            .arg(flag("check", "Verify generated plugin contents")),
    )
    .subcommand(
        Command::new("providers")
            .about("Manage trusted executable providers")
            .subcommand_required(true)
            .subcommand(scope(
                Command::new("install")
                    .arg(Arg::new("source").required(true))
                    .arg(value("sha256", "Required archive SHA-256")),
            ))
            .subcommand(scope(Command::new("list")))
            .subcommand(scope(
                Command::new("remove").arg(Arg::new("name").required(true)),
            )),
    )
}
pub fn help_text() -> String {
    command().render_long_help().to_string()
}
pub fn parse(
    args: impl IntoIterator<Item = OsString>,
) -> std::result::Result<ArgMatches, clap::Error> {
    command().try_get_matches_from(std::iter::once(OsString::from("decision")).chain(args))
}
pub fn get<'a>(m: &'a ArgMatches, name: &str) -> Option<&'a str> {
    m.try_get_one::<String>(name)
        .ok()
        .flatten()
        .map(String::as_str)
}
pub fn many(m: &ArgMatches, name: &str) -> Vec<String> {
    m.try_get_many::<String>(name)
        .ok()
        .flatten()
        .map(|v| v.cloned().collect())
        .unwrap_or_default()
}
pub fn enabled(m: &ArgMatches, name: &str) -> bool {
    m.try_get_one::<bool>(name)
        .ok()
        .flatten()
        .copied()
        .unwrap_or(false)
}
pub fn options(m: &ArgMatches) -> Result<Options> {
    let mut o = Options::default();
    if let Some(v) = get(m, "provider") {
        o.provider = v.into();
    }
    if let Some(v) = get(m, "model") {
        o.model = v.into();
    }
    if let Some(v) = get(m, "base-url") {
        o.base = v.into();
    }
    if let Some(v) = get(m, "api-key-env") {
        o.key_env = v.into();
    }
    if let Some(v) = get(m, "account-id") {
        o.account_id = v.into();
    }
    if let Some(v) = get(m, "plugin-home") {
        o.home = expand_home(v)?;
    }
    o.project = get(m, "project-config").map(expand_home).transpose()?;
    o.timeout = get(m, "timeout")
        .unwrap_or("30")
        .parse()
        .map_err(|_| Error::input("Timeout must be finite and positive"))?;
    o.disabled = many(m, "disable-provider");
    o.dry = enabled(m, "dry-run");
    o.management = enabled(m, "management");
    if !o.timeout.is_finite() || o.timeout <= 0.0 || o.timeout > i64::MAX as f64 / 1e9 {
        return Err(Error::input("Timeout must be finite and positive"));
    }
    if !o.key_env.is_empty() && !crate::store::env_name(&o.key_env) {
        return Err(Error::input("Invalid credential environment-variable name"));
    }
    o.home = std::path::absolute(o.home).map_err(|_| Error::Input)?;
    o.project = o
        .project
        .map(std::path::absolute)
        .transpose()
        .map_err(|_| Error::Input)?;
    Ok(o)
}
pub fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}
pub fn expand_home(s: &str) -> Result<PathBuf> {
    if s == "~" {
        return Ok(home_dir());
    }
    if let Some(tail) = s.strip_prefix("~/").or_else(|| s.strip_prefix("~\\")) {
        return Ok(home_dir().join(tail));
    }
    Ok(PathBuf::from(s))
}
fn read(path: &str) -> Result<Value> {
    let b = if path == "-" {
        validation::read_bounded(std::io::stdin().lock(), json::FRAME_LIMIT)?
    } else {
        validation::read_bounded(
            std::fs::File::open(expand_home(path)?).map_err(|_| {
                Error::input("Cannot read JSON file; check the selected path and permissions")
            })?,
            json::FRAME_LIMIT,
        )?
    };
    json::decode(&b)
}
pub fn make_request(name: &str, m: &ArgMatches, o: &Options) -> Result<(Value, PathBuf)> {
    let directory = if name == "run" && get(m, "request-file") != Some("-") {
        std::path::absolute(expand_home(get(m, "request-file").unwrap())?)
            .map_err(|_| Error::Input)?
            .parent()
            .unwrap()
            .to_path_buf()
    } else {
        std::env::current_dir().map_err(|_| Error::Input)?
    };
    let mut payload = if name == "run" {
        read(get(m, "request-file").unwrap())?
    } else {
        if name == "ask"
            && get(m, "state-file") == Some("-")
            && get(m, "questions-file") == Some("-")
        {
            return Err(Error::input("Only one input can read stdin"));
        }
        let state = match get(m, "state-file") {
            Some(path) => read(path)?,
            None => get(m, "state").map(Value::from).unwrap_or(Value::Null),
        };
        let questions = if name == "ask" {
            read(get(m, "questions-file").unwrap())?
        } else {
            let question = get(m, "question").unwrap();
            if question.trim().is_empty() {
                return Err(Error::input("Question cannot be empty"));
            }
            let mut q = json!({"type":name,"instructions":question});
            if name == "choice" {
                let mut options = serde_json::Map::new();
                for option in many(m, "option") {
                    let (key, description) = option.split_once('=').ok_or_else(|| {
                        Error::input("Each option needs a unique nonempty NAME=description")
                    })?;
                    if key.trim().is_empty() || options.contains_key(key) {
                        return Err(Error::input(
                            "Each option needs a unique nonempty NAME=description",
                        ));
                    }
                    options.insert(
                        key.into(),
                        if description.is_empty() {
                            Value::Null
                        } else {
                            description.into()
                        },
                    );
                }
                q["criteria"] = options.into();
            }
            if name == "score" {
                let levels = many(m, "level");
                if levels.iter().any(|s| s.trim().is_empty()) {
                    return Err(Error::input("Score levels cannot be empty"));
                }
                q["criteria"] = json!(levels);
            }
            json!({"answer":q})
        };
        json!({"state":state,"questions":questions})
    };
    if !payload.is_object() {
        return Err(Error::input("Request must be an object"));
    }
    if !o.model.is_empty() {
        payload["model"] = o.model.clone().into();
    }
    let images = many(m, "image");
    if name != "run" && payload["state"].is_null() && images.is_empty() {
        return Err(Error::input("Supply --state, --state-file, or --image"));
    }
    attach_images(&mut payload, &images, &directory)?;
    Ok((payload, directory))
}
pub fn attach_images(payload: &mut Value, images: &[String], directory: &Path) -> Result<()> {
    let mut assets = match payload.get("assets") {
        Some(v) => v
            .as_object()
            .ok_or_else(|| Error::input("assets must be an object"))?
            .clone(),
        None => serde_json::Map::new(),
    };
    for asset in assets.values_mut() {
        if let Some(path) = asset.get("path").and_then(Value::as_str) {
            asset["path"] = directory
                .join(expand_home(path)?)
                .to_string_lossy()
                .into_owned()
                .into();
        }
    }
    if !images.is_empty() {
        let mut state = payload["state"].as_object().cloned().unwrap_or_else(|| {
            if payload["state"].is_null() {
                serde_json::Map::new()
            } else {
                json!({"context":payload["state"]})
                    .as_object()
                    .unwrap()
                    .clone()
            }
        });
        let mut named = match state.get("images") {
            Some(v) => v
                .as_object()
                .ok_or_else(|| {
                    Error::input("state.images must be an object when attaching CLI images")
                })?
                .clone(),
            None => serde_json::Map::new(),
        };
        for (i, image) in images.iter().enumerate() {
            let default_name = if i == 0 {
                "image".into()
            } else {
                format!("image_{}", i + 1)
            };
            let (name, path) = image.split_once('=').unwrap_or((&default_name, image));
            if !name.as_bytes().first().is_some_and(u8::is_ascii_alphabetic)
                || !name
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
                || path.trim().is_empty()
            {
                return Err(Error::input(
                    "Use --image PATH or --image NAME=PATH with a simple alphanumeric name",
                ));
            }
            let reference = format!("<decision-image:{name}>");
            if named.contains_key(name) || assets.contains_key(&reference) {
                return Err(Error::input(
                    "Image names must be unique and cannot replace existing state images",
                ));
            }
            named.insert(name.into(), reference.clone().into());
            let path = std::path::absolute(expand_home(path)?).map_err(|_| Error::Input)?;
            assets.insert(reference, json!({"path":path.to_string_lossy()}));
        }
        state.insert("images".into(), named.into());
        payload["state"] = state.into();
    }
    if !assets.is_empty() || payload.get("assets").is_some() {
        payload["assets"] = assets.into();
    }
    Ok(())
}
