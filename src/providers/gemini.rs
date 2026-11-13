use super::common::{self, description, invalid};
use crate::{Error, Options, Result};
use serde_json::{Map, Value, json};
use std::time::Duration;

pub const DEFAULT_MODEL: &str = "gemini-decision-1";
pub const QUESTION_ID_PATTERN: &str = "^[A-Za-z0-9_-]{1,64}$";

pub fn validate(payload: &Value) -> Result<()> {
    common::validate_limits(payload, "Gemini Decisions", 1000, 255)?;
    for (name, q) in common::questions(payload)? {
        if !common::id_valid(name, 64, false) {
            return Err(Error::input(
                "Gemini question IDs must be 1 to 64 ASCII letters, digits, underscores, or hyphens",
            ));
        }
        if q["type"] == "choice" {
            for key in q["criteria"].as_object().unwrap().keys() {
                if key.is_empty()
                    || key.chars().count() > 128
                    || key.len() > 512
                    || key != key.trim()
                    || key
                        .chars()
                        .any(|c| c.is_control() || c.is_whitespace() && c != ' ')
                {
                    return Err(Error::input(
                        "Gemini option IDs require 1 to 128 characters without edge spaces, control characters, or non-ASCII spaces",
                    ));
                }
            }
        }
        if q["type"] == "score"
            && q["criteria"]
                .as_array()
                .unwrap()
                .iter()
                .any(|v| description(v).trim().is_empty())
        {
            return Err(Error::input("Gemini score levels cannot be empty"));
        }
    }
    Ok(())
}

pub fn build(payload: &Value) -> Result<Value> {
    let mut content = vec![json!({"type":"text","text":description(&payload["state"])})];
    for image in common::images(payload)? {
        content.push(json!({"type":"text","text":format!("Image reference (JSON string): {}",common::python_json(&json!(image.reference),true))}));
        content.push(json!({"type":"image","data":image.base64,"mime_type":format!("image/{}",image.format)}));
    }
    let mut questions = Map::new();
    for (name, q) in common::questions(payload)? {
        let mut converted = json!({"type":q["type"],"instructions":q["instructions"]});
        let criteria = &q["criteria"];
        match q["type"].as_str() {
            Some("noul") if !criteria.is_null() => {
                let mapped: Map<String, Value> = criteria
                    .as_object()
                    .ok_or_else(|| Error::input("Noul criteria must be an object"))?
                    .iter()
                    .map(|(key, v)| {
                        (
                            format!("{key}_description"),
                            if v.is_null() {
                                "".into()
                            } else {
                                description(v).into()
                            },
                        )
                    })
                    .collect();
                converted["criteria"] = Value::Object(mapped);
            }
            Some("choice") => {
                let mapped: Map<String, Value> = criteria
                    .as_object()
                    .ok_or_else(|| Error::input("Choice criteria must be an object"))?
                    .iter()
                    .map(|(key, v)| {
                        (
                            key.clone(),
                            if v.is_null() {
                                "".into()
                            } else {
                                description(v).into()
                            },
                        )
                    })
                    .collect();
                converted["criteria"] = Value::Object(mapped);
            }
            Some("score") => {
                converted["criteria"] = Value::Array(
                    criteria
                        .as_array()
                        .ok_or_else(|| Error::input("Score criteria must be a list"))?
                        .iter()
                        .map(|v| description(v).into())
                        .collect(),
                )
            }
            _ => (),
        }
        questions.insert(name.clone(), converted);
    }
    Ok(json!({"model":common::model(payload,DEFAULT_MODEL),"input":content,"questions":questions}))
}

pub fn normalize(raw: &Value, questions: &Value, model: &str) -> Result<Value> {
    let fail = || invalid("gemini");
    if raw.get("error").is_some() {
        return Err(fail());
    }
    let qs = questions.as_object().ok_or_else(fail)?;
    let answers = raw["answers"]
        .as_object()
        .filter(|a| a.len() == qs.len() && a.keys().all(|k| qs.contains_key(k)))
        .ok_or_else(fail)?;
    let mut normalized = Map::new();
    for (name, q) in qs {
        let a = &answers[name];
        if a["type"] != q["type"] {
            return Err(fail());
        }
        common::number(&a["confidence"], 1.0, "gemini")?;
        let out = match q["type"].as_str() {
            Some("noul") => json!({"type":"noul","noul":a["noul"]}),
            Some("choice") => {
                json!({"type":"choice","choice":a["choice"],"confidence":a["confidence"],"probabilities":a["probabilities"]})
            }
            Some("score") => {
                let expected: Map<String, Value> = q["criteria"]
                    .as_array()
                    .ok_or_else(fail)?
                    .iter()
                    .enumerate()
                    .map(|(i, v)| (i.to_string(), description(v).into()))
                    .collect();
                if a["legend"] != Value::Object(expected) {
                    return Err(fail());
                }
                json!({"type":"score","score":a["score"],"confidence":a["confidence"],"probabilities":a["probabilities"],"legend":a["legend"]})
            }
            _ => return Err(fail()),
        };
        normalized.insert(name.clone(), out);
    }
    common::normalized(
        "gemini",
        json!({"model":model,"answers":normalized,"usage":common::usage(raw.get("usage"),"total_input_tokens","total_output_tokens","gemini")?}),
        questions,
    )
}

pub async fn evaluate(payload: &Value, options: &Options, key: &str) -> Result<Value> {
    let response = crate::provider::client()?
        .post(format!("{}/decisions", options.base.trim_end_matches('/')))
        .timeout(Duration::from_secs_f64(options.timeout))
        .header("x-goog-api-key", key)
        .json(&build(payload)?)
        .send()
        .await
        .map_err(|_| Error::execution("Gemini request failed; no answers returned"))?;
    normalize(
        &common::response("gemini", response).await?,
        &payload["questions"],
        common::model(payload, DEFAULT_MODEL),
    )
}
