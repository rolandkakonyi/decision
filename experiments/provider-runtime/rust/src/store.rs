use crate::{
    Error, Options, Result,
    json::{FRAME_LIMIT, decode, fields, nonempty},
    process::execute,
    provider::{check_url, client, response_bytes},
    validation::read_bounded,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::{Cursor, Write},
    path::{Component, Path},
};
use url::Url;

#[derive(Clone, Debug, PartialEq, Deserialize, Serialize)]
pub struct Manifest {
    pub protocol_version: u32,
    pub name: String,
    pub version: String,
    pub command: Vec<String>,
    pub setup: Option<Vec<String>>,
    pub default_base_url: String,
    pub default_model: Option<String>,
    #[serde(default)]
    pub credential_env_vars: Vec<String>,
    #[serde(default)]
    pub schema_constraints: serde_json::Map<String, Value>,
}
#[derive(Clone)]
pub struct Plugin {
    pub manifest: Manifest,
    pub root: std::path::PathBuf,
}
#[derive(Serialize, Deserialize)]
struct Registration {
    path: std::path::PathBuf,
    version: String,
    commit: String,
}
type Registry = BTreeMap<String, Registration>;
pub fn reserved(s: &str) -> bool {
    ["typesafe", "openai", "cloudflare", "gemini"].contains(&s)
}
pub fn safe_name(s: &str) -> bool {
    s.as_bytes().first().is_some_and(u8::is_ascii_lowercase)
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || b == b'-')
}
pub fn env_name(s: &str) -> bool {
    s.as_bytes()
        .first()
        .is_some_and(|b| b.is_ascii_alphabetic() || *b == b'_')
        && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
}
fn valid_argv(a: &[String]) -> bool {
    a.first().is_some_and(|s| !s.trim().is_empty()) && a.iter().all(|s| !s.contains('\0'))
}
fn manifest(root: &Path) -> Result<Manifest> {
    let b = fs::read(root.join("decision-provider.json")).map_err(|_| Error::Input)?;
    let v = decode(&b)?;
    let m: Manifest = serde_json::from_value(v).map_err(|_| Error::Input)?;
    if m.protocol_version != 1
        || !safe_name(&m.name)
        || reserved(&m.name)
        || m.version.trim().is_empty()
        || !valid_argv(&m.command)
        || m.setup.as_ref().is_some_and(|a| !valid_argv(a))
        || check_url(&m.default_base_url).is_err()
        || !m.credential_env_vars.iter().all(|s| env_name(s))
    {
        return Err(Error::Input);
    }
    Ok(m)
}
fn registry_path(o: &Options) -> std::path::PathBuf {
    o.project
        .clone()
        .unwrap_or_else(|| o.home.join("registry.json"))
}
fn read_registry(path: &Path) -> Result<Registry> {
    match fs::read(path) {
        Ok(b) => serde_json::from_value(decode(&b)?).map_err(|_| Error::Input),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Registry::new()),
        Err(_) => Err(Error::Input),
    }
}
fn registrations(o: &Options) -> Result<Registry> {
    let mut r = read_registry(&o.home.join("registry.json"))?;
    if let Some(p) = &o.project {
        r.extend(read_registry(p)?);
    }
    Ok(r)
}
fn write_registry(path: &Path, r: &Registry) -> Result<()> {
    let parent = path.parent().ok_or(Error::Input)?;
    fs::create_dir_all(parent).map_err(|_| Error::Input)?;
    let mut temp = tempfile::NamedTempFile::new_in(parent).map_err(|_| Error::Input)?;
    temp.write_all(&serde_json::to_vec(r).map_err(|_| Error::Input)?)
        .map_err(|_| Error::Input)?;
    temp.persist(path).map_err(|_| Error::Input)?;
    Ok(())
}
fn copy_tree(src: &Path, dst: &Path) -> Result<()> {
    let meta = fs::symlink_metadata(src).map_err(|_| Error::Input)?;
    if meta.is_dir() {
        fs::create_dir_all(dst).map_err(|_| Error::Input)?;
        for e in fs::read_dir(src).map_err(|_| Error::Input)? {
            let e = e.map_err(|_| Error::Input)?;
            copy_tree(&e.path(), &dst.join(e.file_name()))?;
        }
    } else if meta.is_file() {
        let b = read_bounded(fs::File::open(src).map_err(|_| Error::Input)?, 128 << 20)?;
        fs::write(dst, b).map_err(|_| Error::Input)?;
        fs::set_permissions(dst, meta.permissions()).map_err(|_| Error::Input)?;
    } else {
        return Err(Error::Input);
    }
    Ok(())
}
fn verify_tree(root: &Path) -> Result<()> {
    for entry in fs::read_dir(root).map_err(|_| Error::Input)? {
        let entry = entry.map_err(|_| Error::Input)?;
        let t = entry.file_type().map_err(|_| Error::Input)?;
        if t.is_dir() {
            verify_tree(&entry.path())?;
        } else if !t.is_file() {
            return Err(Error::Input);
        }
    }
    Ok(())
}
fn extract(b: &[u8], root: &Path) -> Result<()> {
    let mut archive = tar::Archive::new(flate2::read::GzDecoder::new(Cursor::new(b)));
    let mut total = 0u64;
    for entry in archive.entries().map_err(|_| Error::Input)? {
        let mut entry = entry.map_err(|_| Error::Input)?;
        let path = entry.path().map_err(|_| Error::Input)?;
        if path.is_absolute()
            || path
                .components()
                .any(|c| matches!(c, Component::ParentDir | Component::Prefix(_)))
        {
            return Err(Error::Input);
        }
        let kind = entry.header().entry_type();
        if !kind.is_file() && !kind.is_dir() {
            return Err(Error::Input);
        }
        total = total.checked_add(entry.size()).ok_or(Error::Input)?;
        if total > 256 << 20 {
            return Err(Error::Input);
        }
        if !entry.unpack_in(root).map_err(|_| Error::Input)? {
            return Err(Error::Input);
        }
    }
    Ok(())
}
fn find_package(root: &Path) -> Result<std::path::PathBuf> {
    if root.join("decision-provider.json").is_file() {
        return Ok(root.to_path_buf());
    }
    let entries: Vec<_> = fs::read_dir(root)
        .map_err(|_| Error::Input)?
        .collect::<std::io::Result<_>>()
        .map_err(|_| Error::Input)?;
    if entries.len() == 1
        && entries[0].file_type().map_err(|_| Error::Input)?.is_dir()
        && entries[0].path().join("decision-provider.json").is_file()
    {
        return Ok(entries[0].path());
    }
    Err(Error::Input)
}
pub async fn install(o: &Options, source: &str, checksum: &str) -> Result<Value> {
    let artifacts = o.home.join("artifacts");
    fs::create_dir_all(&artifacts).map_err(|_| Error::Input)?;
    let stage = tempfile::Builder::new()
        .prefix("artifact-")
        .tempdir_in(&artifacts)
        .map_err(|_| Error::Input)?;
    let mut commit = String::new();
    if let Some(source) = source.strip_prefix("git+") {
        let mut u = Url::parse(source).map_err(|_| Error::Input)?;
        let reference = u
            .fragment()
            .filter(|s| !s.is_empty() && !s.starts_with('-'))
            .ok_or(Error::Input)?
            .to_string();
        if !u.username().is_empty()
            || u.password().is_some()
            || !(u.scheme() == "https"
                || u.scheme() == "file"
                    && u.host_str().is_none()
                    && Path::new(u.path()).is_absolute())
        {
            return Err(Error::Input);
        }
        u.set_fragment(None);
        let repo = stage.path().join("repo");
        let args = [
            "git",
            "-c",
            "http.followRedirects=false",
            "clone",
            "--quiet",
            "--no-checkout",
            "--",
            u.as_str(),
            repo.to_str().ok_or(Error::Input)?,
        ]
        .map(String::from);
        execute(stage.path(), &args, &[], 1 << 20).await?;
        execute(
            &repo,
            &["git", "checkout", "--quiet", "--detach", &reference].map(String::from),
            &[],
            1 << 20,
        )
        .await?;
        let b = execute(
            &repo,
            &["git", "rev-parse", "HEAD"].map(String::from),
            &[],
            1024,
        )
        .await?;
        commit = String::from_utf8(b)
            .map_err(|_| Error::Provider)?
            .trim()
            .into();
        fs::remove_dir_all(repo.join(".git")).map_err(|_| Error::Input)?;
    } else if source.starts_with("https://") || source.starts_with("http://") {
        check_url(source)?;
        if checksum.len() != 64 || !checksum.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(Error::Input);
        }
        let response = client()?
            .get(source)
            .send()
            .await
            .map_err(|_| Error::Provider)?;
        if response.status() != 200 {
            return Err(Error::Provider);
        }
        let b = response_bytes(response, 128 << 20).await?;
        let actual: String = Sha256::digest(&b)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        if actual != checksum.to_ascii_lowercase() {
            return Err(Error::Input);
        }
        extract(&b, stage.path())?;
    } else {
        let source = std::path::absolute(source).map_err(|_| Error::Input)?;
        let meta = fs::symlink_metadata(&source).map_err(|_| Error::Input)?;
        if meta.is_symlink() {
            return Err(Error::Input);
        }
        let source = if meta.is_dir() {
            source
        } else if source
            .file_name()
            .is_some_and(|s| s == "decision-provider.json")
        {
            source.parent().unwrap().to_path_buf()
        } else {
            return Err(Error::Input);
        };
        copy_tree(&source, stage.path())?;
    }
    verify_tree(stage.path())?;
    let root = find_package(stage.path())?;
    let m = manifest(&root)?;
    if let Some(argv) = &m.setup {
        execute(&root, argv, &[], 1 << 20).await?;
    }
    if manifest(&root)? != m {
        return Err(Error::Input);
    }
    let mut r = read_registry(&registry_path(o))?;
    r.insert(
        m.name.clone(),
        Registration {
            path: root,
            version: m.version.clone(),
            commit,
        },
    );
    write_registry(&registry_path(o), &r)?;
    let _ = stage.keep();
    Ok(json!({"name":m.name,"version":m.version,"restart_required":true}))
}
pub fn list(o: &Options) -> Result<Value> {
    Ok(Value::Array(
        registrations(o)?
            .into_iter()
            .map(|(name, r)| json!({"name":name,"version":r.version}))
            .collect(),
    ))
}
pub fn remove(o: &Options, name: &str) -> Result<Value> {
    if !safe_name(name) || reserved(name) {
        return Err(Error::Input);
    }
    let mut r = read_registry(&registry_path(o))?;
    if r.remove(name).is_none() {
        return Err(Error::Input);
    }
    write_registry(&registry_path(o), &r)?;
    Ok(json!({"name":name,"removed":true}))
}
impl Plugin {
    pub async fn call(
        &self,
        method: &str,
        request: Option<&Value>,
        settings: Option<Value>,
        key: &str,
    ) -> Result<Value> {
        let mut v = json!({"protocol_version":1,"method":method});
        if let Some(r) = request {
            v["request"] = r.clone();
        }
        if method == "evaluate" {
            v["settings"] = settings.unwrap();
            v["credential"] = key.into();
        }
        let mut input = serde_json::to_vec(&v).map_err(|_| Error::Provider)?;
        input.push(b'\n');
        if input.len() > FRAME_LIMIT {
            return Err(Error::Input);
        }
        let b = execute(&self.root, &self.manifest.command, &input, FRAME_LIMIT).await?;
        let b = b.strip_suffix(b"\n").unwrap_or(&b);
        let b = b.strip_suffix(b"\r").unwrap_or(b);
        if b.contains(&b'\n') || b.contains(&b'\r') {
            return Err(Error::Provider);
        }
        let response = decode(b).map_err(|_| Error::Provider)?;
        if response["protocol_version"].as_u64() != Some(1) {
            return Err(Error::Provider);
        }
        if response["ok"] != true {
            return Err(if response["error"]["code"] == "invalid_request" {
                Error::Input
            } else {
                Error::Provider
            });
        }
        response.get("result").cloned().ok_or(Error::Provider)
    }
}
pub async fn resolve(o: &Options) -> Result<Option<Plugin>> {
    if o.disabled.contains(&o.provider) {
        return Err(Error::Disabled);
    }
    if o.provider == "typesafe" {
        return Ok(None);
    }
    if !safe_name(&o.provider) || reserved(&o.provider) {
        return Err(Error::Input);
    }
    let r = registrations(o)?;
    let reg = r.get(&o.provider).ok_or(Error::Input)?;
    let m = manifest(&reg.path)?;
    if m.name != o.provider || m.version != reg.version {
        return Err(Error::Input);
    }
    let p = Plugin {
        manifest: m,
        root: reg.path.clone(),
    };
    let d = p.call("describe", None, None, "").await?;
    if d["name"] != p.manifest.name || d["version"] != p.manifest.version {
        return Err(Error::Provider);
    }
    Ok(Some(p))
}
pub async fn management(o: &Options, name: &str, a: Value) -> Result<Value> {
    match name {
        "provider_install"
            if fields(&a, &["source", "sha256"])
                && nonempty(&a["source"])
                && a.get("sha256").is_none_or(Value::is_string) =>
        {
            install(
                o,
                a["source"].as_str().unwrap(),
                a["sha256"].as_str().unwrap_or(""),
            )
            .await
        }
        "provider_list" if a.as_object().is_some_and(|a| a.is_empty()) => list(o),
        "provider_remove" if fields(&a, &["name"]) && nonempty(&a["name"]) => {
            remove(o, a["name"].as_str().unwrap())
        }
        _ => Err(Error::Input),
    }
}
