use super::common::{self, description, invalid};
use crate::{Error, Options, Result};
use serde_json::{Map, Value, json};
use std::time::Duration;

pub const DEFAULT_MODEL: &str = "gpt-6-luna";

pub fn validate(payload: &Value) -> Result<()> {
    common::validate_limits(payload, "OpenAI Decisions", 400, 10)
}

pub fn build(payload: &Value) -> Result<Value> {
    let state = &payload["state"];
    let structured = state.is_object()
        || state
            .as_array()
            .is_some_and(|a| a.iter().all(Value::is_object));
    let mut content = vec![if structured {
        json!({"type":"input_json","data":state})
    } else {
        json!({"type":"input_text","text":description(state)})
    }];
    for image in common::images(payload)? {
        content.push(json!({"type":"input_text","text":format!("Image reference (JSON string): {}",common::python_json(&json!(image.reference),true))}));
        content.push(json!({"type":"input_image","image_url":format!("data:image/{};base64,{}", image.format,image.base64)}));
    }
    let mut questions = Vec::new();
    for (name, q) in common::questions(payload)? {
        let kind = q["type"]
            .as_str()
            .ok_or_else(|| Error::input("Unsupported question type"))?;
        let mut instructions = q["instructions"]
            .as_str()
            .ok_or_else(|| Error::input("Each question needs nonempty instructions"))?
            .to_owned();
        if kind == "noul" && !q["criteria"].is_null() {
            instructions.push_str("\nInterpret true and false using these criteria (JSON): ");
            instructions.push_str(&description(&q["criteria"]));
        }
        let mut converted = json!({"name":name,"type":if kind=="noul"{"predicate"}else{kind},"instructions":instructions});
        if kind == "choice" {
            converted["choices"] = Value::Array(
                q["criteria"]
                    .as_object()
                    .ok_or_else(|| Error::input("Choice criteria must be an object"))?
                    .iter()
                    .map(|(key, v)| {
                        let mut choice = json!({"value":key});
                        if !v.is_null() {
                            choice["description"] = description(v).into();
                        }
                        choice
                    })
                    .collect(),
            );
        } else if kind == "score" {
            converted["levels"] = Value::Array(
                q["criteria"]
                    .as_array()
                    .ok_or_else(|| Error::input("Score criteria must be a list"))?
                    .iter()
                    .enumerate()
                    .map(|(i, v)| json!({"label":i.to_string(),"description":description(v)}))
                    .collect(),
            );
        }
        questions.push(converted);
    }
    Ok(
        json!({"model":common::model(payload,DEFAULT_MODEL),"input":[{"role":"user","content":content}],"questions":questions}),
    )
}

fn probabilities(rows: &Value, q: &Value, score: bool) -> Result<Value> {
    let fail = || invalid("openai");
    let expected: Vec<String> = if score {
        (0..q["criteria"].as_array().ok_or_else(fail)?.len())
            .map(|i| i.to_string())
            .collect()
    } else {
        q["criteria"]
            .as_object()
            .ok_or_else(fail)?
            .keys()
            .cloned()
            .collect()
    };
    let rows = rows
        .as_array()
        .filter(|r| r.len() == expected.len())
        .ok_or_else(fail)?;
    let mut result = Map::new();
    for row in rows {
        let key = if score {
            let value = &row["value"];
            if !crate::json::integer(value) {
                return Err(fail());
            }
            let index = value.as_u64().ok_or_else(fail)?;
            let key = index.to_string();
            if row["label"].as_str() != Some(key.as_str()) {
                return Err(fail());
            }
            key
        } else {
            row["value"].as_str().ok_or_else(fail)?.to_owned()
        };
        if !expected.contains(&key) || result.contains_key(&key) {
            return Err(fail());
        }
        common::number(&row["probability"], 1.0, "openai")?;
        result.insert(key, row["probability"].clone());
    }
    Ok(Value::Object(result))
}

pub fn normalize(raw: &Value, questions: &Value) -> Result<Value> {
    let fail = || invalid("openai");
    let qs = questions.as_object().ok_or_else(fail)?;
    let rows = raw["answers"]
        .as_array()
        .filter(|a| a.len() == qs.len())
        .ok_or_else(fail)?;
    let mut answers = Map::new();
    for row in rows {
        let name = row["name"]
            .as_str()
            .filter(|name| qs.contains_key(*name) && !answers.contains_key(*name))
            .ok_or_else(fail)?;
        if row["type"] == "refusal" {
            return Err(Error::execution(
                "OpenAI refused a question; no answers returned",
            ));
        }
        let q = &qs[name];
        let kind = q["type"].as_str().ok_or_else(fail)?;
        if row["type"] != if kind == "noul" { "predicate" } else { kind } {
            return Err(fail());
        }
        let answer = match kind {
            "noul" => {
                common::number(&row["probability"], 1.0, "openai")?;
                json!({"type":"noul","noul":row["probability"]})
            }
            "choice" => {
                json!({"type":"choice","choice":row["choice"],"confidence":row["confidence"],"probabilities":probabilities(&row["probabilities"],q,false)?})
            }
            "score" => {
                let legend: Map<String, Value> = q["criteria"]
                    .as_array()
                    .ok_or_else(fail)?
                    .iter()
                    .enumerate()
                    .map(|(i, v)| (i.to_string(), description(v).into()))
                    .collect();
                json!({"type":"score","score":row["score"],"confidence":row["confidence"],"legend":legend,"probabilities":probabilities(&row["probabilities"],q,true)?})
            }
            _ => return Err(fail()),
        };
        answers.insert(name.to_owned(), answer);
    }
    // The common result validator also restores request order and checks totals,
    // offered choices, numeric ranges, and raw integer token counts.
    common::normalized(
        "openai",
        json!({"model":raw["model"],"answers":answers,"usage":common::usage(raw.get("usage"),"input_tokens","output_tokens","openai")?}),
        questions,
    )
}

pub async fn evaluate(payload: &Value, options: &Options, key: &str) -> Result<Value> {
    let response = crate::provider::client()?
        .post(format!("{}/decisions", options.base.trim_end_matches('/')))
        .timeout(Duration::from_secs_f64(options.timeout))
        .bearer_auth(key)
        .json(&build(payload)?)
        .send()
        .await
        .map_err(|_| Error::execution("OpenAI request failed; no answers returned"))?;
    normalize(
        &common::response("openai", response).await?,
        &payload["questions"],
    )
}
