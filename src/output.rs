use serde_json::Value;
use unicode_general_category::{GeneralCategory as Category, get_general_category};

fn text(v: &Value) -> String {
    match v.as_str() {
        Some(s) => s
            .chars()
            .map(|c| {
                if c != ' '
                    && matches!(
                        get_general_category(c),
                        Category::Control
                            | Category::Format
                            | Category::Unassigned
                            | Category::PrivateUse
                            | Category::Surrogate
                            | Category::LineSeparator
                            | Category::ParagraphSeparator
                            | Category::SpaceSeparator
                    )
                {
                    format!("\\u{:04x}", c as u32)
                } else {
                    c.to_string()
                }
            })
            .collect(),
        None => json_text(v),
    }
}
fn json_text(v: &Value) -> String {
    match v {
        Value::Array(a) => format!(
            "[{}]",
            a.iter().map(json_text).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(m) => format!(
            "{{{}}}",
            m.iter()
                .map(|(k, v)| format!("{}: {}", serde_json::to_string(k).unwrap(), json_text(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        _ => v.to_string(),
    }
}
fn general(value: &Value) -> String {
    let number = value.as_f64().unwrap();
    let scientific = format!("{number:.5e}");
    let (mantissa, exponent) = scientific.split_once('e').unwrap();
    let exponent: i32 = exponent.parse().unwrap();
    let trim = |s: String| {
        if s.contains('.') {
            s.trim_end_matches('0').trim_end_matches('.').to_string()
        } else {
            s
        }
    };
    if !(-4..6).contains(&exponent) {
        format!("{}e{exponent:+03}", trim(mantissa.into()))
    } else {
        trim(format!(
            "{number:.precision$}",
            precision = (5 - exponent).max(0) as usize
        ))
    }
}
pub fn result(r: &Value) -> String {
    let mut lines = vec![format!("Model: {}", text(&r["model"]))];
    for (name, a) in r["answers"].as_object().unwrap() {
        lines.extend([
            String::new(),
            format!("{}:", text(&Value::String(name.clone()))),
        ]);
        match a["type"].as_str().unwrap() {
            "noul" => lines.push(format!(
                "  Yes probability: {:.1}%",
                a["noul"].as_f64().unwrap() * 100.0
            )),
            "choice" => lines.push(format!("  Choice: {}", text(&a["choice"]))),
            "score" => lines.push(format!("  Score: {}", general(&a["score"]))),
            "box" => lines.push(format!(
                "  Box: {}",
                ["left", "top", "right", "bottom"]
                    .iter()
                    .map(|e| format!("{e}={}", general(&a["box"][e])))
                    .collect::<Vec<_>>()
                    .join(", ")
            )),
            _ => (),
        }
        if let Some(c) = a["confidence"].as_f64() {
            lines.push(format!("  Confidence: {:.1}%", c * 100.0));
        }
        if let Some(ps) = a["probabilities"].as_object() {
            if !ps.is_empty() {
                lines.push("  Probabilities:".into());
            }
            for (key, p) in ps {
                let label = a["legend"]
                    .get(key)
                    .cloned()
                    .unwrap_or_else(|| key.clone().into());
                lines.push(format!(
                    "    {}: {:.1}%",
                    text(&label),
                    p.as_f64().unwrap() * 100.0
                ));
            }
        }
    }
    let usage = ["input_tokens", "output_tokens"]
        .iter()
        .zip(["input", "output"])
        .map(|(key, label)| {
            if r["usage"][key].is_null() {
                format!("{label} tokens unavailable")
            } else {
                format!("{} {label} tokens", r["usage"][key])
            }
        })
        .collect::<Vec<_>>()
        .join(", ");
    lines.extend([
        String::new(),
        format!("Usage: {usage}"),
        format!("Duration: {:.3} ms", r["duration_ms"].as_f64().unwrap()),
    ]);
    lines.join("\n")
}
pub fn preview(r: &Value) -> String {
    let mut lines = vec![
        "Request preview (not sent)".into(),
        format!(
            "Model: {}",
            text(
                r.get("model")
                    .unwrap_or(&Value::String("provider default".into()))
            )
        ),
        String::new(),
        "State:".into(),
        serde_json::to_string_pretty(&r["state"]).unwrap(),
        String::new(),
        "Questions:".into(),
    ];
    for (name, q) in r["questions"].as_object().unwrap() {
        lines.push(format!(
            "  {} [{}]: {}",
            text(&name.clone().into()),
            q["type"].as_str().unwrap(),
            text(&q["instructions"])
        ));
        if let Some(c) = q.get("criteria") {
            lines.push(format!("    Criteria: {}", text(c)));
        }
    }
    if let Some(assets) = r["assets"].as_object() {
        lines.extend([String::new(), "Images:".into()]);
        for (name, a) in assets {
            let (source, size) = if let Some(path) = a["path"].as_str() {
                (path, std::fs::metadata(path).map(|m| m.len()).unwrap_or(0))
            } else {
                let s = a["data_base64"].as_str().unwrap();
                (
                    "base64 input",
                    (s.len() / 4 * 3 - s.chars().rev().take_while(|c| *c == '=').count()) as u64,
                )
            };
            lines.push(format!(
                "  {}: {} ({size} bytes)",
                text(&name.clone().into()),
                text(&source.into())
            ));
        }
    }
    lines.join("\n")
}
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn general_matches_six_significant_digits() {
        for (n, expected) in [
            (0.12345678, "0.123457"),
            (1.0, "1"),
            (0.000001, "1e-06"),
            (999999.9, "1e+06"),
            (0.0, "0"),
        ] {
            assert_eq!(general(&json!(n)), expected);
        }
    }
    #[test]
    fn escapes_terminal_controls() {
        assert_eq!(text(&json!("a\n\u{1b}[2J")), "a\\u000a\\u001b[2J");
        assert_eq!(text(&json!("Café \u{00a0}\u{e000}")), "Café \\u00a0\\ue000");
    }
}
