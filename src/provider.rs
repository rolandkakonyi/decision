use crate::{
    Error, Options, Result,
    json::{FRAME_LIMIT, decode},
    providers,
    store::Plugin,
    validation,
};
use base64::{Engine, engine::general_purpose::STANDARD};
use reqwest::{
    Client, Response,
    multipart::{Form, Part},
};
use serde_json::{Value, json};
use std::{env, path::Path, time::Instant};
use url::Url;

pub fn check_url(s: &str) -> Result<Url> {
    let u = Url::parse(s).map_err(|_| Error::Input)?;
    let local = matches!(
        u.host_str(),
        Some("localhost" | "127.0.0.1" | "[::1]" | "::1")
    );
    if u.host_str().is_none()
        || !u.username().is_empty()
        || u.password().is_some()
        || u.query().is_some()
        || u.fragment().is_some()
        || !(u.scheme() == "https" || u.scheme() == "http" && local)
    {
        return Err(Error::Input);
    }
    Ok(u)
}
pub fn client() -> Result<Client> {
    Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .build()
        .map_err(|_| Error::Provider)
}
pub async fn response_bytes(mut response: Response, limit: usize) -> Result<Vec<u8>> {
    let mut b = Vec::new();
    while let Some(c) = response.chunk().await.map_err(|_| Error::Provider)? {
        if b.len() + c.len() > limit {
            return Err(Error::Provider);
        }
        b.extend_from_slice(&c);
    }
    Ok(b)
}
pub fn configure(o: &mut Options, p: Option<&Plugin>) -> Result<()> {
    if o.base.is_empty() {
        o.base = match p {
            Some(p) => p.manifest.default_base_url.clone(),
            None => {
                let info =
                    providers::info(&o.provider).ok_or_else(|| Error::input("Unknown provider"))?;
                env::var(info.base_url_env)
                    .ok()
                    .filter(|s| !s.is_empty())
                    .unwrap_or_else(|| info.default_base_url.into())
            }
        };
    }
    check_url(&o.base)?;
    Ok(())
}
fn credential(o: &Options, p: Option<&Plugin>) -> Result<String> {
    let mut names = vec!["DECISION_API_KEY"];
    if !o.key_env.is_empty() {
        names = vec![&o.key_env];
    } else {
        let info = providers::info(&o.provider);
        let official = match p {
            Some(p) => p.manifest.default_base_url.as_str(),
            None => info.as_ref().ok_or(Error::Input)?.default_base_url,
        };
        let u = check_url(&o.base)?;
        let v = check_url(official)?;
        if u.scheme() == "https"
            && u.port_or_known_default() == Some(443)
            && v.scheme() == "https"
            && v.port_or_known_default() == Some(443)
            && u.host_str() == v.host_str()
        {
            match p {
                Some(p) => names.extend(p.manifest.credential_env_vars.iter().map(String::as_str)),
                None => names.extend(info.as_ref().unwrap().credential_env_vars.iter().copied()),
            }
        }
    }
    names
        .into_iter()
        .find_map(|n| {
            env::var(n)
                .ok()
                .filter(|s| !s.trim().is_empty())
                .map(|s| s.trim().into())
        })
        .ok_or_else(|| Error::input("No provider key: set DECISION_API_KEY or select it with DECISION_API_KEY_ENV / --api-key-env; matching official provider hosts also accept their provider-specific credential variables"))
}
pub async fn evaluate(
    o: &Options,
    p: Option<&Plugin>,
    mut r: Value,
    paths: bool,
    directory: &Path,
) -> Result<Value> {
    let preview_assets = r.get("assets").cloned();
    validation::request(&mut r, paths, directory)?;
    if !o.model.is_empty() && (paths || r.get("model").is_none()) {
        r["model"] = o.model.clone().into();
    } else if r.get("model").is_none()
        && let Some(m) = p
            .and_then(|p| p.manifest.default_model.as_deref())
            .or_else(|| providers::info(&o.provider).and_then(|i| i.default_model))
    {
        r["model"] = m.into();
    }
    if let Some(p) = p {
        let mut prepared = p.call("prepare", Some(&r), None, "").await?;
        validation::request(&mut prepared, false, Path::new("")).map_err(|_| Error::Provider)?;
        if ["state", "questions", "assets"]
            .iter()
            .any(|k| r.get(k) != prepared.get(k))
        {
            return Err(Error::Provider);
        }
        r = prepared;
    } else {
        let state = r["state"].clone();
        let questions = r["questions"].clone();
        providers::prepare(&o.provider, &mut r)?;
        if r["state"] != state || r["questions"] != questions {
            return Err(Error::Provider);
        }
    }
    if o.dry {
        if let Some(assets) = preview_assets {
            r["assets"] = assets;
        }
        return Ok(r);
    }
    let key = credential(o, p)?;
    if p.is_none() {
        providers::validate_connection(&o.provider, &r, o)?;
    }
    let start = Instant::now();
    let raw = match p {
        None if o.provider=="typesafe" => typesafe(o, &r, &key).await?,
        None => providers::evaluate(&o.provider,&r,o,&key).await?,
        Some(p) => {
            p.call(
                "evaluate",
                Some(&r),
                Some(json!({"base_url":o.base,"timeout":o.timeout,"account_id":if o.account_id.is_empty() {Value::Null} else {o.account_id.clone().into()}})),
                &key,
            )
            .await?
        }
    };
    let mut result = validation::result(&raw, &r["questions"])?;
    result["duration_ms"] = (start.elapsed().as_secs_f64() * 1000.0).into();
    Ok(result)
}
async fn typesafe(o: &Options, r: &Value, key: &str) -> Result<Value> {
    let mut body = r.clone();
    body.as_object_mut().unwrap().remove("assets");
    if body.get("model").is_none() {
        body["model"] = env::var("TYPESAFE_DEFAULT_MODEL")
            .ok()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "jev-latest".into())
            .into();
    }
    let mut request = client()?
        .post(format!("{}/v1/systemone", o.base.trim_end_matches('/')))
        .bearer_auth(key)
        .header("Accept", "application/json");
    let assets = r.get("assets").and_then(Value::as_object);
    if let Some(assets) = assets {
        let mut names: Vec<_> = assets.keys().collect();
        names.sort();
        let mut metadata = json!({});
        for (i, name) in names.iter().enumerate() {
            metadata[*name] = json!({"type":"image","part":format!("asset-{i}")});
        }
        body["assets"] = metadata;
        let part = Part::bytes(serde_json::to_vec(&body).map_err(|_| Error::Input)?)
            .mime_str("application/json")
            .map_err(|_| Error::Input)?;
        let mut form = Form::new().part("request", part);
        for (i, name) in names.iter().enumerate() {
            let image = &assets[*name];
            let format = image["format"].as_str().unwrap();
            let raw = STANDARD
                .decode(image["data_base64"].as_str().unwrap())
                .map_err(|_| Error::Input)?;
            let part = Part::bytes(raw)
                .file_name(format!("asset-{i}.{format}"))
                .mime_str(&format!("image/{format}"))
                .map_err(|_| Error::Input)?;
            form = form.part(format!("asset-{i}"), part);
        }
        request = request.multipart(form);
    } else {
        request = request.json(&body);
    }
    if assets.is_some()
        || r["questions"]
            .as_object()
            .unwrap()
            .values()
            .any(|q| q["type"] == "box")
    {
        request = request.header("TypeSafe-Danger-Zone-Images", "true");
    }
    let response = request.send().await.map_err(|_| Error::Provider)?;
    if !response.status().is_success() {
        return Err(Error::Provider);
    }
    decode(&response_bytes(response, FRAME_LIMIT).await?).map_err(|_| Error::Data)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[ignore = "isolated credential environment fixture"]
    fn credential_host_fixture() {
        let scenario = std::env::var("DECISION_CREDENTIAL_TEST_SCENARIO").unwrap();
        let default = if scenario == "http" || scenario == "explicit" {
            "http://localhost"
        } else if scenario == "official" {
            "https://api.example.test"
        } else {
            "https://api.example.test:444"
        };
        let actual = if default.contains("localhost") {
            "https://localhost"
        } else {
            "https://api.example.test"
        };
        let o = Options {
            provider: "fixture".into(),
            base: actual.into(),
            key_env: if scenario == "explicit" {
                "EXPLICIT_FIXTURE_KEY".into()
            } else {
                String::new()
            },
            ..Options::default()
        };
        let plugin = Plugin {
            root: "unused".into(),
            manifest: crate::store::Manifest {
                protocol_version: 1,
                name: "fixture".into(),
                version: "1".into(),
                command: vec!["unused".into()],
                setup: None,
                default_base_url: default.into(),
                default_model: None,
                credential_env_vars: vec!["PROVIDER_FIXTURE_KEY".into()],
                schema_constraints: serde_json::Map::new(),
            },
        };
        let value = credential(&o, Some(&plugin));
        match scenario.as_str() {
            "official" => assert_eq!(value.unwrap(), "implicit-fixture"),
            "generic" => assert_eq!(value.unwrap(), "generic-fixture"),
            "explicit" => assert_eq!(value.unwrap(), "explicit-fixture"),
            _ => assert!(value.is_err()),
        }
    }
    #[test]
    fn implicit_credentials_require_both_default_and_actual_https_443() {
        for scenario in ["port", "http", "official", "generic", "explicit"] {
            let mut child = std::process::Command::new(std::env::current_exe().unwrap());
            child
                .args([
                    "--ignored",
                    "--exact",
                    "provider::tests::credential_host_fixture",
                ])
                .env_clear();
            for name in crate::process::INHERITED_ENV {
                if let Some(value) = std::env::var_os(name) {
                    child.env(name, value);
                }
            }
            child
                .env("DECISION_CREDENTIAL_TEST_SCENARIO", scenario)
                .env("PROVIDER_FIXTURE_KEY", "implicit-fixture");
            if scenario == "generic" {
                child.env("DECISION_API_KEY", "generic-fixture");
            }
            if scenario == "explicit" {
                child.env("EXPLICIT_FIXTURE_KEY", "explicit-fixture");
            }
            let result = child.output().unwrap();
            assert!(
                result.status.success(),
                "credential policy case {scenario} failed"
            );
        }
    }
}

#[cfg(test)]
mod http_tests {
    use super::*;
    use std::io::{Read, Write};
    #[tokio::test]
    async fn typesafe_http_omitted_usage_normalizes_to_null_counts() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut input = Vec::new();
            let mut chunk = [0u8; 4096];
            loop {
                let n = stream.read(&mut chunk).unwrap();
                assert!(n > 0);
                input.extend_from_slice(&chunk[..n]);
                if let Some(end) = input.windows(4).position(|w| w == b"\r\n\r\n") {
                    let headers = String::from_utf8_lossy(&input[..end]).to_ascii_lowercase();
                    let length: usize = headers
                        .lines()
                        .find_map(|l| l.strip_prefix("content-length:"))
                        .unwrap()
                        .trim()
                        .parse()
                        .unwrap();
                    if input.len() >= end + 4 + length {
                        break;
                    }
                }
            }
            let body = r#"{"model":"fixture","answers":{"q":{"type":"noul","noul":0.9}}}"#;
            write!(stream,"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len()).unwrap();
        });
        let o = Options {
            base: format!("http://{address}"),
            ..Options::default()
        };
        let r = json!({"state":"x","questions":{"q":{"type":"noul","instructions":"True?"}}});
        let raw = typesafe(&o, &r, "fixture-key").await.unwrap();
        server.join().unwrap();
        let normalized = validation::result(&raw, &r["questions"]).unwrap();
        assert_eq!(
            normalized["usage"],
            json!({"input_tokens":null,"output_tokens":null})
        );
    }
}
