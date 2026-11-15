use crate::{Error, Result};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Map, Value, json};
use std::fmt::Write;

pub fn invalid(provider: &str) -> Error {
    Error::execution(match provider {
        "openai" => "OpenAI returned invalid decision data; no answers returned",
        "cloudflare" => {
            "Cloudflare returned an unsuccessful or invalid decision response; no answers returned"
        }
        "gemini" => "Gemini returned invalid decision data; no answers returned",
        _ => "Provider returned invalid decision data; no answers returned",
    })
}

pub fn model<'a>(payload: &'a Value, default: &'a str) -> &'a str {
    payload
        .get("model")
        .and_then(Value::as_str)
        .unwrap_or(default)
}

pub fn questions(payload: &Value) -> Result<&Map<String, Value>> {
    payload["questions"]
        .as_object()
        .ok_or_else(|| Error::input("questions must be an object"))
}

pub fn id_valid(name: &str, max: usize, dots: bool) -> bool {
    !name.is_empty()
        && name.len() <= max
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-' || dots && b == b'.')
}

pub fn validate_limits(
    payload: &Value,
    provider: &str,
    limit: usize,
    max_score: usize,
) -> Result<()> {
    let qs = questions(payload)?;
    if qs.len() > limit {
        return Err(Error::input(format!(
            "{provider} supports at most {limit} questions per request"
        )));
    }
    for q in qs.values() {
        match q["type"].as_str() {
            Some("box") => {
                return Err(Error::input(format!(
                    "{provider} does not support Box; use the typesafe provider for Box requests"
                )));
            }
            Some("choice") => {
                if !q["criteria"]
                    .as_object()
                    .is_some_and(|m| (2..=255).contains(&m.len()))
                {
                    return Err(Error::input(format!(
                        "{provider} Choice requires 2 to 255 options"
                    )));
                }
            }
            Some("score") => {
                if !q["criteria"]
                    .as_array()
                    .is_some_and(|a| (2..=max_score).contains(&a.len()))
                {
                    return Err(Error::input(format!(
                        "{provider} Score requires 2 to {max_score} levels"
                    )));
                }
            }
            Some("noul") => (),
            _ => return Err(Error::input("Unsupported question type")),
        }
    }
    Ok(())
}

pub fn constraints(
    max_questions: usize,
    max_score: usize,
    id_pattern: Option<&str>,
    option_length: Option<usize>,
) -> Value {
    let mut qs = json!({"maxProperties":max_questions});
    if let Some(pattern) = id_pattern {
        qs["propertyNames"] = json!({"pattern":pattern});
    }
    let mut choice = json!({"minProperties":2,"maxProperties":255});
    if let Some(length) = option_length {
        choice["propertyNames"] = json!({"maxLength":length});
    }
    qs["additionalProperties"] = json!({"oneOf":[
        {"properties":{"type":{"const":"noul"}},"required":["type"]},
        {"properties":{"type":{"const":"choice"},"criteria":choice},"required":["type"]},
        {"properties":{"type":{"const":"score"},"criteria":{"minItems":2,"maxItems":max_score}},"required":["type"]}
    ]});
    json!({"properties":{"questions":qs}})
}

// Provider descriptions are JSON text, so Python's separators and string escaping
// are part of the existing wire contract, rather than cosmetic serialization.
pub fn python_json(value: &Value, ascii: bool) -> String {
    match value {
        Value::Array(items) => format!(
            "[{}]",
            items
                .iter()
                .map(|v| python_json(v, ascii))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Object(items) => format!(
            "{{{}}}",
            items
                .iter()
                .map(|(k, v)| format!(
                    "{}: {}",
                    python_json(&Value::String(k.clone()), ascii),
                    python_json(v, ascii)
                ))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::String(s) => {
            let encoded = serde_json::to_string(s).expect("JSON strings are serializable");
            if !ascii {
                return encoded;
            }
            let mut text = String::new();
            for ch in encoded.chars() {
                if ch as u32 >= 0x7f {
                    let mut units = [0; 2];
                    for unit in ch.encode_utf16(&mut units) {
                        write!(text, "\\u{unit:04x}").unwrap();
                    }
                } else {
                    text.push(ch);
                }
            }
            text
        }
        Value::Number(n) => {
            let s = n.to_string();
            if !s.contains(['.', 'e', 'E']) {
                return if s == "-0" { "0".into() } else { s };
            }
            let Some(f) = n.as_f64().filter(|f| f.is_finite()) else {
                return s;
            };
            let repr = format!("{f:?}");
            match repr.split_once('e') {
                Some((mantissa, exponent)) => {
                    let exp: i32 = exponent.parse().expect("formatted exponent");
                    format!(
                        "{mantissa}e{}{abs:02}",
                        if exp < 0 { "-" } else { "+" },
                        abs = exp.abs()
                    )
                }
                None => repr,
            }
        }
        _ => value.to_string(),
    }
}

pub fn description(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| python_json(value, false))
}

pub struct Image<'a> {
    pub reference: &'a str,
    pub base64: &'a str,
    pub format: &'static str,
    pub length: usize,
}
pub fn images(payload: &Value) -> Result<Vec<Image<'_>>> {
    let mut images = Vec::new();
    if let Some(assets) = payload.get("assets") {
        for (reference, asset) in assets
            .as_object()
            .ok_or_else(|| Error::input("assets must be an object"))?
        {
            let data = asset["data_base64"]
                .as_str()
                .ok_or_else(|| Error::input("Image assets require data_base64"))?;
            let raw = STANDARD
                .decode(data)
                .map_err(|_| Error::input("Invalid image base64"))?;
            let format = if raw.starts_with(b"\x89PNG\r\n\x1a\n") {
                "png"
            } else if raw.starts_with(&[255, 216, 255]) {
                "jpeg"
            } else {
                return Err(Error::input("Images must be PNG or JPEG files"));
            };
            images.push(Image {
                reference,
                base64: data,
                format,
                length: raw.len(),
            });
        }
    }
    Ok(images)
}

pub fn number(value: &Value, max: f64, provider: &str) -> Result<()> {
    if !value
        .as_f64()
        .is_some_and(|f| f.is_finite() && f >= 0.0 && f <= max)
    {
        return Err(invalid(provider));
    }
    Ok(())
}

pub fn usage(value: Option<&Value>, input: &str, output: &str, provider: &str) -> Result<Value> {
    let value = value.unwrap_or(&Value::Null);
    if !value.is_null() && !value.is_object() {
        return Err(invalid(provider));
    }
    Ok(json!({"input_tokens":value[input],"output_tokens":value[output]}))
}

pub fn normalized(provider: &str, result: Value, questions: &Value) -> Result<Value> {
    crate::validation::result(&result, questions).map_err(|_| invalid(provider))
}

pub async fn response(provider: &str, response: reqwest::Response) -> Result<Value> {
    let name = match provider {
        "openai" => "OpenAI",
        "cloudflare" => "Cloudflare",
        "gemini" => "Gemini",
        _ => "Provider",
    };
    if !response.status().is_success() {
        return Err(Error::execution(format!(
            "{name} request failed (HTTP {}); no answers returned",
            response.status().as_u16()
        )));
    }
    let data = crate::provider::response_bytes(response, crate::json::FRAME_LIMIT)
        .await
        .map_err(|_| invalid(provider))?;
    crate::json::decode(&data)
        .map_err(|_| Error::execution(format!("{name} returned invalid JSON; no answers returned")))
}
