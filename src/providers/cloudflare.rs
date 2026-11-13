use super::common::{self, invalid};
use crate::{Error, Options, Result};
use serde_json::{Map, Value, json};
use std::time::Duration;

pub const API_ROOT: &str = "https://api.cloudflare.com/client/v4";
pub const QUESTION_ID_PATTERN: &str = "^[A-Za-z0-9_.-]{1,100}$";
pub const MAX_IMAGE_BYTES: usize = 4 * 1024 * 1024;
const MAX_TOTAL_BYTES: usize = 8 * 1024 * 1024;
const MAX_BODY_BYTES: usize = 13 * 1024 * 1024;
const REFERENCES: &str = "_decision_image_references";

fn model_name(value: &str) -> Result<&str> {
    let name = value
        .trim()
        .strip_prefix("@cf/cloudflare/")
        .unwrap_or(value.trim());
    if !matches!(name, "clef" | "clef-flash") {
        return Err(Error::input(
            "Cloudflare supports clef or clef-flash (short names or @cf/cloudflare/ IDs)",
        ));
    }
    Ok(name)
}

pub fn prepare(payload: &mut Value) -> Result<()> {
    payload["model"] = model_name(common::model(payload, "clef"))?.into();
    common::validate_limits(payload, "Cloudflare Clef", 64, 10)?;
    if common::questions(payload)?
        .keys()
        .any(|name| !common::id_valid(name, 100, true))
    {
        return Err(Error::input(
            "Cloudflare question IDs must be 1 to 100 letters, digits, underscores, dots, or hyphens",
        ));
    }
    body(payload)?;
    Ok(())
}

pub fn endpoint(options: &Options, model: &str) -> Result<String> {
    let mut root = options.base.trim_end_matches('/').to_owned();
    let account = &options.account_id;
    if !account.is_empty()
        && (account.len() != 32 || !account.bytes().all(|b| b.is_ascii_hexdigit()))
    {
        return Err(Error::input(
            "Cloudflare account ID must contain 32 hexadecimal characters",
        ));
    }
    if root == API_ROOT {
        if account.is_empty() {
            return Err(Error::input(
                "Set CLOUDFLARE_ACCOUNT_ID, DECISION_ACCOUNT_ID, or --account-id for Cloudflare, or supply a Workers AI run-root --base-url",
            ));
        }
        root.push_str(&format!("/accounts/{account}/ai/run"));
    }
    Ok(format!("{root}/@cf/cloudflare/{}", model_name(model)?))
}

pub fn build(payload: &Value) -> Result<Value> {
    if payload
        .get("assets")
        .and_then(Value::as_object)
        .is_some_and(|a| a.len() > 4)
    {
        return Err(Error::input("Cloudflare Clef supports at most 4 images"));
    }
    let images = common::images(payload)?;
    if images.iter().any(|i| i.length > MAX_IMAGE_BYTES) {
        return Err(Error::input(
            "Cloudflare Clef images must be at most 4 MiB each",
        ));
    }
    if images.iter().map(|i| i.length).sum::<usize>() > MAX_TOTAL_BYTES {
        return Err(Error::input(
            "Cloudflare Clef images together must be at most 8 MiB",
        ));
    }
    let mut result = json!({"model":model_name(common::model(payload,"clef"))?,"state":payload["state"],"questions":payload["questions"]});
    if !images.is_empty() {
        let mut state = if payload["state"].is_object() {
            payload["state"].clone()
        } else {
            json!({"context":payload["state"]})
        };
        if state.get(REFERENCES).is_some() {
            return Err(Error::input(
                "Cloudflare image requests reserve state._decision_image_references",
            ));
        }
        state[REFERENCES] = Value::Array(
            images
                .iter()
                .enumerate()
                .map(|(i, img)| json!({"reference":img.reference,"image_position_1_based":i+1}))
                .collect(),
        );
        result["state"] = state;
        result["images"] = Value::Array(
            images
                .iter()
                .map(|img| format!("data:image/{};base64,{}", img.format, img.base64).into())
                .collect(),
        );
    }
    Ok(result)
}

pub fn body(payload: &Value) -> Result<Vec<u8>> {
    let body = serde_json::to_vec(&build(payload)?)
        .map_err(|_| Error::input("Invalid Cloudflare request"))?;
    if body.len() > MAX_BODY_BYTES {
        return Err(Error::input("Cloudflare Clef request body exceeds 13 MiB"));
    }
    Ok(body)
}

pub fn normalize(raw: &Value, questions: &Value) -> Result<Value> {
    let fail = || invalid("cloudflare");
    if raw["success"] != true
        || raw
            .get("errors")
            .is_some_and(|v| !v.as_array().is_some_and(Vec::is_empty))
    {
        return Err(fail());
    }
    let result = &raw["result"];
    let qs = questions.as_object().ok_or_else(fail)?;
    let answers = result["answers"]
        .as_object()
        .filter(|a| a.len() == qs.len() && a.keys().all(|k| qs.contains_key(k)))
        .ok_or_else(fail)?;
    let mut normalized = Map::new();
    for (name, q) in qs {
        let a = &answers[name];
        if a["type"] != q["type"] {
            return Err(fail());
        }
        let out = match q["type"].as_str() {
            Some("noul") => json!({"type":"noul","noul":a["noul"]}),
            Some("choice") => {
                json!({"type":"choice","choice":a["choice"],"confidence":a["confidence"],"probabilities":a["probabilities"]})
            }
            Some("score") => {
                json!({"type":"score","score":a["score"],"confidence":a["confidence"],"probabilities":a["probabilities"],"legend":a["legend"]})
            }
            _ => return Err(fail()),
        };
        normalized.insert(name.clone(), out);
    }
    common::normalized(
        "cloudflare",
        json!({"model":result["model"],"answers":normalized,"usage":common::usage(result.get("usage"),"input_tokens","output_tokens","cloudflare")?}),
        questions,
    )
}

pub async fn evaluate(payload: &Value, options: &Options, key: &str) -> Result<Value> {
    let response = crate::provider::client()?
        .post(endpoint(options, common::model(payload, "clef"))?)
        .timeout(Duration::from_secs_f64(options.timeout))
        .bearer_auth(key)
        .header("Content-Type", "application/json")
        .body(body(payload)?)
        .send()
        .await
        .map_err(|_| Error::execution("Cloudflare request failed; no answers returned"))?;
    normalize(
        &common::response("cloudflare", response).await?,
        &payload["questions"],
    )
}
