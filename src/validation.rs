use crate::{
    Error, Result,
    json::{fields, integer, nonempty},
};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{fs::File, io::Read, path::Path};

pub fn read_bounded(r: impl Read, limit: usize) -> Result<Vec<u8>> {
    let mut b = Vec::new();
    r.take(limit as u64 + 1)
        .read_to_end(&mut b)
        .map_err(|_| Error::Input)?;
    if b.len() > limit {
        return Err(Error::Input);
    }
    Ok(b)
}
fn strings<'a>(v: &'a Value, out: &mut Vec<&'a str>) {
    match v {
        Value::String(s) => out.push(s),
        Value::Object(m) => m.values().for_each(|v| strings(v, out)),
        Value::Array(a) => a.iter().for_each(|v| strings(v, out)),
        _ => (),
    }
}
pub fn request(r: &mut Value, paths: bool, directory: &Path) -> Result<()> {
    if r["state"].is_null()
        || !fields(r, &["state", "questions", "model", "assets"])
        || r.get("model").is_some_and(|v| !nonempty(v))
    {
        return Err(Error::Input);
    }
    let qs = r["questions"]
        .as_object()
        .filter(|m| !m.is_empty())
        .ok_or(Error::Input)?;
    for (name, q) in qs {
        if name.trim().is_empty()
            || !fields(q, &["type", "instructions", "criteria"])
            || !nonempty(&q["instructions"])
        {
            return Err(Error::Input);
        }
        let c = &q["criteria"];
        let valid = match q["type"].as_str() {
            Some("noul") => c.is_null() || fields(c, &["true", "false"]),
            Some("choice") => c
                .as_object()
                .is_some_and(|m| !m.is_empty() && m.keys().all(|s| !s.trim().is_empty())),
            Some("score") => c.as_array().is_some_and(|a| !a.is_empty()),
            Some("box") => true,
            _ => false,
        };
        if !valid {
            return Err(Error::Input);
        }
    }
    let mut refs = Vec::new();
    strings(&r["state"], &mut refs);
    for q in qs.values() {
        strings(&q["instructions"], &mut refs);
        strings(&q["criteria"], &mut refs);
    }
    if let Some(assets) = r.get("assets") {
        let assets = assets
            .as_object()
            .filter(|a| !a.is_empty())
            .ok_or(Error::Input)?;
        let mut normalized = serde_json::Map::new();
        let mut total = 0;
        for (name, m) in assets {
            if name.trim().is_empty()
                || name.len() > 1024
                || !fields(m, &["path", "data_base64", "format", "type"])
                || m.get("type").is_some_and(|v| v != "image")
                || m.get("format")
                    .is_some_and(|v| !["png", "jpg", "jpeg"].iter().any(|s| v == s))
                || !refs.iter().any(|s| s.contains(name))
            {
                return Err(Error::Input);
            }
            let path = m.get("path");
            let data = m.get("data_base64");
            if path.is_some() && !paths {
                return Err(Error::input(
                    "MCP image assets require data_base64; local paths are supported only by the CLI",
                ));
            }
            let raw = match (path, data) {
                (Some(p), None) if paths && nonempty(p) => {
                    let p = p.as_str().unwrap();
                    let file = directory.join(crate::cli::expand_home(p)?);
                    read_bounded(
                        File::open(file).map_err(|_| {
                            Error::input(
                                "Cannot read image file; check the selected path and permissions",
                            )
                        })?,
                        16 << 20,
                    )?
                }
                (None, Some(d)) if nonempty(d) => {
                    let s = d.as_str().unwrap();
                    if s.len() > 4 * (16_usize * 1024 * 1024).div_ceil(3) {
                        return Err(Error::Input);
                    }
                    STANDARD.decode(s).map_err(|_| Error::input("Image data_base64 must be valid standard base64 without a data: prefix"))?
                }
                _ => return Err(Error::Input),
            };
            total += raw.len();
            if raw.len() > 16 << 20 || total > 32 << 20 {
                return Err(Error::Input);
            }
            let format = if raw.starts_with(b"\x89PNG\r\n\x1a\n") {
                "png"
            } else if raw.starts_with(&[255, 216, 255]) {
                "jpeg"
            } else {
                return Err(Error::Input);
            };
            if m.get("format")
                .is_some_and(|v| v.as_str().unwrap().replace("jpg", "jpeg") != format)
            {
                return Err(Error::Input);
            }
            normalized.insert(
                name.clone(),
                json!({"type":"image", "format":format, "data_base64":STANDARD.encode(raw)}),
            );
        }
        r["assets"] = Value::Object(normalized);
    }
    Ok(())
}
fn num(v: &Value, max: f64) -> Result<f64> {
    v.as_f64()
        .filter(|f| f.is_finite() && *f >= 0.0 && *f <= max)
        .ok_or(Error::Data)
}
fn keys_equal(a: &Value, b: &Value) -> bool {
    match (a.as_object(), b.as_object()) {
        (Some(a), Some(b)) => a.len() == b.len() && a.keys().all(|k| b.contains_key(k)),
        _ => false,
    }
}
pub fn result(v: &Value, qs: &Value) -> Result<Value> {
    if !nonempty(&v["model"]) || !keys_equal(&v["answers"], qs) {
        return Err(Error::Data);
    }
    let mut answers = serde_json::Map::new();
    for (id, q) in qs.as_object().ok_or(Error::Data)? {
        let a = &v["answers"][id];
        if !a.is_object() || a["type"] != q["type"] {
            return Err(Error::Data);
        }
        let mut out = json!({"type":a["type"]});
        match q["type"].as_str().ok_or(Error::Data)? {
            "noul" => {
                num(&a["noul"], 1.0)?;
                out["noul"] = a["noul"].clone();
            }
            kind @ ("choice" | "score") => {
                num(&a["confidence"], 1.0)?;
                out["confidence"] = a["confidence"].clone();
                let expected = if kind == "score" {
                    Value::Object(
                        q["criteria"]
                            .as_array()
                            .ok_or(Error::Data)?
                            .iter()
                            .enumerate()
                            .map(|(i, _)| (i.to_string(), Value::Null))
                            .collect(),
                    )
                } else {
                    q["criteria"].clone()
                };
                if !keys_equal(&a["probabilities"], &expected) {
                    return Err(Error::Data);
                }
                let (mut sum, mut comp) = (0.0, 0.0);
                for p in a["probabilities"].as_object().unwrap().values() {
                    let y = num(p, 1.0)? - comp;
                    let t = sum + y;
                    comp = (t - sum) - y;
                    sum = t;
                }
                if (sum - 1.0).abs() > 0.02 {
                    return Err(Error::Data);
                }
                out["probabilities"] = a["probabilities"].clone();
                if kind == "choice" {
                    let choice = a["choice"].as_str().ok_or(Error::Data)?;
                    if expected.get(choice).is_none() {
                        return Err(Error::Data);
                    }
                    out["choice"] = a["choice"].clone();
                } else {
                    num(
                        &a["score"],
                        (expected.as_object().unwrap().len() - 1) as f64,
                    )?;
                    if !keys_equal(&a["legend"], &expected)
                        || !a["legend"]
                            .as_object()
                            .unwrap()
                            .values()
                            .all(|v| v.is_string() || v.is_object() || v.is_array())
                    {
                        return Err(Error::Data);
                    }
                    out["score"] = a["score"].clone();
                    out["legend"] = a["legend"].clone();
                }
            }
            "box" => {
                let b = &a["box"];
                let left = num(&b["left"], 1.0)?;
                let top = num(&b["top"], 1.0)?;
                if left > num(&b["right"], 1.0)? || top > num(&b["bottom"], 1.0)? {
                    return Err(Error::Data);
                }
                let c = a.get("confidence").unwrap_or(&Value::Null);
                if !c.is_null() {
                    num(c, 1.0)?;
                }
                out["confidence"] = c.clone();
                out["box"] = json!({"left":b["left"],"top":b["top"],"right":b["right"],"bottom":b["bottom"]});
            }
            _ => return Err(Error::Data),
        }
        answers.insert(id.clone(), out);
    }
    let u = v["usage"].as_object().ok_or(Error::Data)?;
    let mut usage = json!({});
    for key in ["input_tokens", "output_tokens"] {
        let n = u.get(key).unwrap_or(&Value::Null);
        if !n.is_null() && !integer(n) {
            return Err(Error::Data);
        }
        usage[key] = n.clone();
    }
    Ok(json!({"model":v["model"],"answers":answers,"usage":usage}))
}
