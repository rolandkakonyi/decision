use super::*;
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};

fn questions() -> Value {
    json!({"present":{"type":"noul","instructions":"Is a dog visible?"},"breed":{"type":"choice","instructions":"Which breed?","criteria":{"french":"French bulldog","english":"English bulldog"}},"visible":{"type":"score","instructions":"How visible?","criteria":["Hidden","Visible"]}})
}
fn payload() -> Value {
    json!({"state":"dog","questions":questions()})
}
fn openai_response() -> Value {
    json!({"model":"gpt-6-luna","usage":{"input_tokens":10,"output_tokens":0},"answers":[
        {"name":"present","type":"predicate","probability":0.9},
        {"name":"breed","type":"choice","choice":"french","confidence":0.8,"probabilities":[{"value":"french","probability":0.9},{"value":"english","probability":0.1}]},
        {"name":"visible","type":"score","score":0.9,"confidence":0.8,"probabilities":[{"value":0,"label":"0","probability":0.1},{"value":1,"label":"1","probability":0.9}]}
    ]})
}
fn common_answers() -> Value {
    json!({"present":{"type":"noul","noul":0.9},"breed":{"type":"choice","choice":"french","confidence":0.9,"probabilities":{"french":0.9,"english":0.1}},"visible":{"type":"score","score":0.9,"confidence":0.9,"legend":{"0":"Hidden","1":"Visible"},"probabilities":{"0":0.1,"1":0.9}}})
}
fn cloudflare_response() -> Value {
    json!({"success":true,"errors":[],"result":{"model":"clef","answers":common_answers(),"usage":{"input_tokens":10,"output_tokens":0}}})
}
fn gemini_response() -> Value {
    let mut answers = common_answers();
    answers["present"]["confidence"] = json!(0.9);
    json!({"answers":answers,"usage":{"total_input_tokens":10,"total_output_tokens":0,"total_tokens":10}})
}
fn add_image(r: &mut Value, name: &str, length: usize) {
    let mut raw = b"\x89PNG\r\n\x1a\n".to_vec();
    raw.resize(length.max(8), b'x');
    if r.get("assets").is_none() {
        r["assets"] = json!({});
    }
    r["assets"][name] = json!({"format":"png","data_base64":STANDARD.encode(raw)});
}

#[test]
fn characterized_wire_and_schemas_match_python() {
    let cases: Value = serde_json::from_str(include_str!("fixtures/wire.json")).unwrap();
    for case in cases.as_array().unwrap() {
        let actual = match case["provider"].as_str().unwrap() {
            "openai" => openai::build(&case["request"]),
            "cloudflare" => cloudflare::build(&case["request"]),
            "gemini" => gemini::build(&case["request"]),
            _ => unreachable!(),
        }
        .unwrap();
        assert_eq!(
            actual, case["expected"],
            "provider={}, state={}",
            case["provider"], case["request"]["state"]
        );
    }
    let schemas: Value = serde_json::from_str(include_str!("fixtures/constraints.json")).unwrap();
    for name in ["openai", "cloudflare", "gemini"] {
        assert_eq!(schema_constraints(name), schemas[name]);
    }
    assert_eq!(schema_constraints("typesafe"), json!({}));
    assert_eq!(info("openai").unwrap().default_model, Some("gpt-6-luna"));
    assert_eq!(
        info("gemini").unwrap().credential_env_vars,
        &["GEMINI_API_KEY", "GOOGLE_API_KEY"]
    );
    assert!(info("not-a-builtin").is_none());
}

#[test]
fn python_json_text_preserves_arbitrary_values_and_escaping() {
    let v =
        crate::json::decode(br#"{"a":1e-5,"b":1e16,"c":1.0,"d":9007199254740993,"e":[true,null]}"#)
            .unwrap();
    assert_eq!(
        common::python_json(&v, false),
        r#"{"a": 1e-05, "b": 1e+16, "c": 1.0, "d": 9007199254740993, "e": [true, null]}"#
    );
    assert_eq!(
        common::python_json(&json!("é😀\u{7f}"), true),
        r#""\u00e9\ud83d\ude00\u007f""#
    );
}

#[test]
fn provider_limits_fail_before_network() {
    for (name, max) in [("openai", 400), ("cloudflare", 64), ("gemini", 1000)] {
        let mut r = payload();
        r["questions"] = Value::Object(
            (0..max)
                .map(|i| (i.to_string(), json!({"type":"noul","instructions":"Q"})))
                .collect(),
        );
        prepare(name, &mut r).unwrap();
        r["questions"]["extra"] = json!({"type":"noul","instructions":"Q"});
        assert!(prepare(name, &mut r).is_err());
        for q in [
            json!({"type":"box"}),
            json!({"type":"choice","criteria":{"only":null}}),
            json!({"type":"score","criteria":["one"]}),
        ] {
            let mut r = json!({"state":"x","questions":{"q":q}});
            assert!(prepare(name, &mut r).is_err());
        }
        let max_score = if name == "gemini" { 255 } else { 10 };
        let mut r = json!({"state":"x","questions":{"q":{"type":"score","criteria":vec!["level";max_score]}}});
        prepare(name, &mut r).unwrap();
        r["questions"]["q"]["criteria"]
            .as_array_mut()
            .unwrap()
            .push(json!("extra"));
        assert!(prepare(name, &mut r).is_err());
    }
}

#[test]
fn gemini_unicode_options_and_ids_match_python_rules() {
    let mut r = payload();
    r["questions"]["breed"]["criteria"] = json!({"é".repeat(128):null,"two words":"allowed"});
    prepare("gemini", &mut r).unwrap();
    for key in [
        "a".repeat(129),
        " edge".into(),
        "edge ".into(),
        "a\tb".into(),
        "a\0b".into(),
        "a\u{a0}b".into(),
        "a\u{85}b".into(),
        "a\u{1c}b".into(),
    ] {
        let mut r = payload();
        r["questions"]["breed"]["criteria"] = json!({key:"","valid":""});
        assert!(prepare("gemini", &mut r).is_err());
    }
    for id in ["has.dot", "has space", "newline\n", "é"] {
        let mut r = json!({"state":"x","questions":{id:{"type":"noul","instructions":"Q"}}});
        assert!(prepare("gemini", &mut r).is_err());
    }
    let mut r = payload();
    r["questions"]["visible"]["criteria"] = json!([" ", "two"]);
    assert!(prepare("gemini", &mut r).is_err());
}

#[test]
fn cloudflare_model_account_images_and_body_budget() {
    let mut r = payload();
    r["model"] = json!(" @cf/cloudflare/clef-flash ");
    prepare("cloudflare", &mut r).unwrap();
    assert_eq!(r["model"], "clef-flash");
    let mut o = Options {
        base: cloudflare::API_ROOT.into(),
        account_id: String::new(),
        ..Options::default()
    };
    assert!(validate_connection("cloudflare", &r, &o).is_err());
    o.account_id = "c".repeat(32);
    assert_eq!(
        cloudflare::endpoint(&o, "clef-flash").unwrap(),
        format!(
            "{}/accounts/{}/ai/run/@cf/cloudflare/clef-flash",
            cloudflare::API_ROOT,
            "c".repeat(32)
        )
    );
    o.base = "http://127.0.0.1:1234/run/".into();
    o.account_id = String::new();
    assert_eq!(
        cloudflare::endpoint(&o, "clef").unwrap(),
        "http://127.0.0.1:1234/run/@cf/cloudflare/clef"
    );
    o.account_id = "bad/account".into();
    assert!(cloudflare::endpoint(&o, "clef").is_err());
    for name in ["other", "clef-omni", "../../other"] {
        r["model"] = name.into();
        assert!(prepare("cloudflare", &mut r).is_err());
    }
    let mut r = payload();
    r["state"] = json!({"_decision_image_references":"user context"});
    add_image(&mut r, "photo", 8);
    assert!(prepare("cloudflare", &mut r).is_err());
    for (count, length) in [(5, 8), (1, (4 << 20) + 1), (3, 3 << 20)] {
        let mut r = payload();
        for i in 0..count {
            add_image(&mut r, &i.to_string(), length)
        }
        assert!(prepare("cloudflare", &mut r).is_err());
    }
    let mut r = payload();
    add_image(&mut r, "one", 4 << 20);
    add_image(&mut r, "two", 4 << 20);
    prepare("cloudflare", &mut r).unwrap();
    let mut r = payload();
    r["state"] = "x".repeat(13 << 20).into();
    assert!(prepare("cloudflare", &mut r).is_err());
}

#[test]
fn openai_normalizes_order_and_rejects_atomic_failures() {
    let mut raw = openai_response();
    raw["answers"].as_array_mut().unwrap().reverse();
    let result = openai::normalize(&raw, &questions()).unwrap();
    assert_eq!(
        result["answers"]
            .as_object()
            .unwrap()
            .keys()
            .collect::<Vec<_>>(),
        questions().as_object().unwrap().keys().collect::<Vec<_>>()
    );
    assert_eq!(
        result["usage"],
        json!({"input_tokens":10,"output_tokens":0})
    );
    for mode in 0..13 {
        let mut raw = openai_response();
        match mode {
            0 => {
                raw["answers"].as_array_mut().unwrap().pop();
            }
            1 => raw["answers"][1] = raw["answers"][0].clone(),
            2 => raw["answers"][0]["name"] = json!("unknown"),
            3 => raw["answers"][0]["type"] = json!("refusal"),
            4 => raw["answers"][0]["probability"] = json!(true),
            5 => raw["answers"][1]["choice"] = json!(true),
            6 => {
                raw["answers"][1]["probabilities"][1] =
                    raw["answers"][1]["probabilities"][0].clone()
            }
            7 => raw["answers"][2]["probabilities"][0]["label"] = json!("wrong"),
            8 => raw["answers"][2]["probabilities"][0]["value"] = json!(0.0),
            9 => raw["usage"]["input_tokens"] = json!(-1),
            10 => raw["usage"]["input_tokens"] = json!(5.0),
            11 => raw["answers"][1]["probabilities"][0]["probability"] = json!(0.1),
            12 => raw["answers"][0]["type"] = json!("choice"),
            _ => unreachable!(),
        };
        assert!(
            openai::normalize(&raw, &questions()).is_err(),
            "mode {mode}"
        );
    }
}

#[test]
fn cloudflare_envelope_and_gemini_legend_confidence_are_strict() {
    let cf = cloudflare::normalize(&cloudflare_response(), &questions()).unwrap();
    assert_eq!(cf["answers"], common_answers());
    let ge = gemini::normalize(&gemini_response(), &questions(), "custom-model").unwrap();
    assert_eq!(ge["model"], "custom-model");
    assert_eq!(ge["answers"], common_answers());
    for mode in 0..12 {
        for provider in ["cloudflare", "gemini"] {
            let mut raw = if provider == "cloudflare" {
                cloudflare_response()
            } else {
                gemini_response()
            };
            let root = if provider == "cloudflare" {
                &mut raw["result"]
            } else {
                &mut raw
            };
            match mode {
                0 => {
                    root["answers"].as_object_mut().unwrap().remove("present");
                }
                1 => root["answers"]["extra"] = json!({"type":"noul","noul":0.5}),
                2 => root["answers"]["present"]["type"] = json!("refusal"),
                3 => root["answers"]["present"]["noul"] = json!("0.9"),
                4 => root["answers"]["present"]["noul"] = json!(true),
                5 => root["answers"]["present"]["noul"] = json!(2),
                6 => root["answers"]["breed"]["choice"] = json!("unknown"),
                7 => {
                    root["answers"]["breed"]["probabilities"]
                        .as_object_mut()
                        .unwrap()
                        .remove("english");
                }
                8 => root["answers"]["visible"]["score"] = json!(-1),
                9 => {
                    root["answers"]["visible"]["legend"]
                        .as_object_mut()
                        .unwrap()
                        .remove("1");
                }
                10 => root["usage"] = json!([]),
                11 => {
                    let key = if provider == "cloudflare" {
                        "input_tokens"
                    } else {
                        "total_input_tokens"
                    };
                    root["usage"][key] = json!(false);
                }
                _ => unreachable!(),
            };
            let result = if provider == "cloudflare" {
                cloudflare::normalize(&raw, &questions())
            } else {
                gemini::normalize(&raw, &questions(), "gemini-decision-1")
            };
            assert!(result.is_err(), "{provider} mode {mode}");
        }
    }
    for flag in [json!(false), json!(1), Value::Null] {
        let mut raw = cloudflare_response();
        raw["success"] = flag;
        assert!(cloudflare::normalize(&raw, &questions()).is_err());
    }
    let mut raw = cloudflare_response();
    raw["errors"] = json!([{"message":"private context"}]);
    assert!(cloudflare::normalize(&raw, &questions()).is_err());
    let mut raw = gemini_response();
    raw["answers"]["present"]["confidence"] = json!(-1);
    assert!(gemini::normalize(&raw, &questions(), "gemini-decision-1").is_err());
    let mut raw = gemini_response();
    raw["answers"]["visible"]["legend"]["1"] = json!("Wrong level");
    assert!(gemini::normalize(&raw, &questions(), "gemini-decision-1").is_err());
    let mut raw = gemini_response();
    raw["error"] = Value::Null;
    assert!(gemini::normalize(&raw, &questions(), "gemini-decision-1").is_err());
}

#[test]
fn missing_usage_remains_null_and_large_counts_are_exact() {
    for mode in 0..3 {
        let usage = match mode {
            0 => None,
            1 => Some(Value::Null),
            _ => Some(json!({})),
        };
        let mut a = openai_response();
        let mut c = cloudflare_response();
        let mut g = gemini_response();
        for raw in [&mut a, &mut c["result"], &mut g] {
            raw.as_object_mut().unwrap().remove("usage");
            if let Some(u) = &usage {
                raw["usage"] = u.clone();
            }
        }
        for result in [
            openai::normalize(&a, &questions()),
            cloudflare::normalize(&c, &questions()),
            gemini::normalize(&g, &questions(), "gemini-decision-1"),
        ] {
            assert_eq!(
                result.unwrap()["usage"],
                json!({"input_tokens":null,"output_tokens":null})
            );
        }
    }
    let large = crate::json::decode(b"184467440737095516170").unwrap();
    let mut raw = openai_response();
    raw["usage"]["input_tokens"] = large.clone();
    assert_eq!(
        openai::normalize(&raw, &questions()).unwrap()["usage"]["input_tokens"],
        large
    );
}

#[test]
fn structured_and_scalar_states_follow_provider_wire_types() {
    let cases: Value = serde_json::from_str(include_str!("fixtures/states.json")).unwrap();
    for case in cases.as_array().unwrap() {
        let r = json!({"state":case["state"],"questions":questions()});
        assert_eq!(
            openai::build(&r).unwrap()["input"][0]["content"][0],
            case["openai"]
        );
        assert_eq!(gemini::build(&r).unwrap()["input"][0], case["gemini"]);
        assert_eq!(cloudflare::build(&r).unwrap()["state"], case["state"]);
    }
}

struct Exchange {
    route: String,
    headers: String,
    body: Value,
}
fn http_fixture(
    status: u16,
    body: Vec<u8>,
) -> (
    String,
    std::sync::mpsc::Receiver<Exchange>,
    std::thread::JoinHandle<()>,
) {
    use std::io::{Read, Write};
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let (send, receive) = std::sync::mpsc::channel();
    let thread = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(std::time::Duration::from_secs(3)))
            .unwrap();
        let mut bytes = Vec::new();
        let mut buffer = [0; 4096];
        let split = loop {
            let n = stream.read(&mut buffer).unwrap();
            assert!(n > 0);
            bytes.extend_from_slice(&buffer[..n]);
            if let Some(i) = bytes.windows(4).position(|s| s == b"\r\n\r\n") {
                break i + 4;
            }
        };
        let headers = String::from_utf8(bytes[..split].to_vec()).unwrap();
        let length = headers
            .lines()
            .find_map(|line| {
                line.split_once(':')
                    .filter(|(k, _)| k.eq_ignore_ascii_case("content-length"))
                    .map(|(_, v)| v.trim().parse::<usize>().unwrap())
            })
            .unwrap();
        while bytes.len() - split < length {
            let n = stream.read(&mut buffer).unwrap();
            assert!(n > 0);
            bytes.extend_from_slice(&buffer[..n]);
        }
        let route = headers.lines().next().unwrap().to_owned();
        let request = crate::json::decode(&bytes[split..split + length]).unwrap();
        send.send(Exchange {
            route,
            headers: headers.to_ascii_lowercase(),
            body: request,
        })
        .unwrap();
        write!(stream,"HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\nContent-Length: {}\r\nLocation: /must-not-follow\r\nConnection: close\r\n\r\n",body.len()).unwrap();
        stream.write_all(&body).unwrap();
    });
    (format!("http://{address}/custom"), receive, thread)
}

#[tokio::test]
async fn native_http_transports_route_and_authenticate_one_batch() {
    for name in ["openai", "cloudflare", "gemini"] {
        let raw = match name {
            "openai" => openai_response(),
            "cloudflare" => cloudflare_response(),
            _ => gemini_response(),
        };
        let (base, receive, thread) = http_fixture(200, serde_json::to_vec(&raw).unwrap());
        let options = Options {
            base,
            timeout: 2.0,
            account_id: String::new(),
            ..Options::default()
        };
        let mut r = payload();
        prepare(name, &mut r).unwrap();
        let result = evaluate(name, &r, &options, "fixture-only-key")
            .await
            .unwrap();
        assert_eq!(
            result["usage"],
            json!({"input_tokens":10,"output_tokens":0})
        );
        let exchange = receive.recv().unwrap();
        thread.join().unwrap();
        let suffix = if name == "cloudflare" {
            "/@cf/cloudflare/clef"
        } else {
            "/decisions"
        };
        assert_eq!(exchange.route, format!("POST /custom{suffix} HTTP/1.1"));
        let auth = if name == "gemini" {
            "x-goog-api-key: fixture-only-key"
        } else {
            "authorization: bearer fixture-only-key"
        };
        assert!(exchange.headers.contains(auth));
        let expected = match name {
            "openai" => openai::build(&r),
            "cloudflare" => cloudflare::build(&r),
            _ => gemini::build(&r),
        }
        .unwrap();
        assert_eq!(exchange.body, expected);
    }
}

#[tokio::test]
async fn http_errors_redirects_and_duplicate_json_are_safe() {
    for provider in ["openai", "cloudflare", "gemini"] {
        for (status, body) in [
            (503, b"fixture-only-key private-context".to_vec()),
            (307, b"private-context".to_vec()),
            (
                200,
                br#"{"model":"private-context","model":"private-context"}"#.to_vec(),
            ),
            (200, b"fixture-only-key".to_vec()),
        ] {
            let (base, receive, thread) = http_fixture(status, body);
            let options = Options {
                base,
                timeout: 2.0,
                account_id: String::new(),
                ..Options::default()
            };
            let mut r = payload();
            prepare(provider, &mut r).unwrap();
            let error = evaluate(provider, &r, &options, "fixture-only-key")
                .await
                .unwrap_err()
                .to_string();
            receive.recv().unwrap();
            thread.join().unwrap();
            assert!(!error.contains("fixture-only-key"));
            assert!(!error.contains("private-context"));
        }
    }
}
