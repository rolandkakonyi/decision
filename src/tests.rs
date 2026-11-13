use crate::{Error, json::decode, provider::check_url, validation};
use serde_json::{Value, json};
use std::path::Path;

fn request() -> Value {
    json!({"state":{"large":9007199254740993_u64},"questions":{"q":{"type":"noul","instructions":"Is <image> red?"}}})
}
fn result() -> Value {
    json!({"model":"fixture","answers":{"q":{"type":"noul","noul":0.9}},"usage":{"input_tokens":0,"output_tokens":null}})
}
#[test]
fn validates_raw_answers_before_normalization() {
    let q = request()["questions"].clone();
    let good = validation::result(&result(), &q).unwrap();
    assert_eq!(good["usage"]["input_tokens"], 0);
    assert!(good["usage"]["output_tokens"].is_null());
    for bad in [
        json!(true),
        json!(-0.1),
        json!(1.1),
        json!("0.9"),
        Value::Null,
    ] {
        let mut r = result();
        r["answers"]["q"]["noul"] = bad;
        assert_eq!(validation::result(&r, &q), Err(Error::Data));
    }
    for bad in [json!(-1), json!(5.0), json!(true), json!("5")] {
        let mut r = result();
        r["usage"]["input_tokens"] = bad;
        assert!(validation::result(&r, &q).is_err());
    }
    let mut r = result();
    r["answers"]["extra"] = r["answers"]["q"].clone();
    assert!(validation::result(&r, &q).is_err());
}
#[test]
fn omitted_usage_is_unknown_but_malformed_usage_is_invalid() {
    let q = request()["questions"].clone();
    let mut r = result();
    r.as_object_mut().unwrap().remove("usage");
    assert_eq!(
        validation::result(&r, &q).unwrap()["usage"],
        json!({"input_tokens":null,"output_tokens":null})
    );
    for malformed in [
        Value::Null,
        json!([]),
        json!("unknown"),
        json!(false),
        json!(0),
    ] {
        r["usage"] = malformed;
        assert_eq!(validation::result(&r, &q), Err(Error::Data));
    }
}
#[test]
fn probability_keys_and_rounding() {
    let q = json!({"q":{"type":"choice","criteria":{"z":"Z","a":"A"}}});
    let mut r = json!({"model":"fixture","answers":{"q":{"type":"choice","choice":"z","confidence":0.9,"probabilities":{"z":0.8,"a":0.19}}},"usage":{}});
    assert!(validation::result(&r, &q).is_ok());
    r["answers"]["q"]["probabilities"]["a"] = json!(0.1);
    assert!(validation::result(&r, &q).is_err());
    r["answers"]["q"]["probabilities"] = json!({"z":1.0});
    assert!(validation::result(&r, &q).is_err());
}
#[test]
fn request_routing_and_image_boundaries() {
    let mut r = request();
    r["provider"] = json!("evil");
    assert_eq!(
        validation::request(&mut r, false, Path::new("")),
        Err(Error::Input)
    );
    let mut r = request();
    r["assets"] = json!({"<image>":{"path":"/tmp/test.png"}});
    assert!(validation::request(&mut r, false, Path::new("")).is_err());
    let mut r = request();
    r["assets"] = json!({"<image>":{"data_base64":"iVBORw0KGgo=","format":"jpeg"}});
    assert!(validation::request(&mut r, true, Path::new("")).is_err());
    r["assets"]["<image>"]["format"] = json!("png");
    validation::request(&mut r, false, Path::new("")).unwrap();
    assert_eq!(r["assets"]["<image>"]["type"], "image");
    r["assets"]["<image>"]["data_base64"] = json!("iVBORw0KGgo=\n");
    assert!(validation::request(&mut r, false, Path::new("")).is_err());
}
#[test]
fn endpoint_policy() {
    for s in [
        "https://api.example.test/prefix",
        "http://localhost:8080",
        "http://127.0.0.1:1234/custom",
        "http://[::1]:8080",
    ] {
        assert!(check_url(s).is_ok(), "{s}");
    }
    for s in [
        "http://example.test",
        "https://key@example.test",
        "https://example.test?key=secret",
        "https://example.test/#x",
        "file:///tmp/api",
    ] {
        assert!(check_url(s).is_err(), "{s}");
    }
}
#[test]
fn no_float_roundtrip_on_arbitrary_json_state() {
    let text = br#"{"state":{"large":9007199254740993,"fraction":0.12345678901234567890123},"questions":{"q":{"type":"choice","instructions":"Pick","criteria":{"z":"first","a":"second"}}}}"#;
    let mut r = decode(text).unwrap();
    validation::request(&mut r, false, Path::new("")).unwrap();
    assert_eq!(serde_json::to_vec(&r).unwrap(), text);
}
