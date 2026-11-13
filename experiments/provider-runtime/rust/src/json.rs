use crate::{Error, Result};
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::Value;
use std::{collections::HashSet, fmt};

pub const FRAME_LIMIT: usize = 64 << 20;

// Scan before Value deserialization: Value alone silently overwrites duplicate keys.
struct Unique;
impl<'de> Deserialize<'de> for Unique {
    fn deserialize<D: Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        d.deserialize_any(UniqueVisitor)
    }
}
struct UniqueVisitor;
impl<'de> Visitor<'de> for UniqueVisitor {
    type Value = Unique;
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("strict JSON")
    }
    fn visit_map<M: MapAccess<'de>>(self, mut m: M) -> std::result::Result<Unique, M::Error> {
        let mut keys = HashSet::new();
        while let Some(k) = m.next_key::<String>()? {
            if !keys.insert(k) {
                return Err(de::Error::custom("duplicate JSON key"));
            }
            m.next_value::<Unique>()?;
        }
        Ok(Unique)
    }
    fn visit_seq<S: SeqAccess<'de>>(self, mut s: S) -> std::result::Result<Unique, S::Error> {
        while s.next_element::<Unique>()?.is_some() {}
        Ok(Unique)
    }
    fn visit_bool<E: de::Error>(self, _: bool) -> std::result::Result<Unique, E> {
        Ok(Unique)
    }
    fn visit_str<E: de::Error>(self, _: &str) -> std::result::Result<Unique, E> {
        Ok(Unique)
    }
    fn visit_i64<E: de::Error>(self, _: i64) -> std::result::Result<Unique, E> {
        Ok(Unique)
    }
    fn visit_u64<E: de::Error>(self, _: u64) -> std::result::Result<Unique, E> {
        Ok(Unique)
    }
    fn visit_f64<E: de::Error>(self, f: f64) -> std::result::Result<Unique, E> {
        if !f.is_finite() {
            return Err(de::Error::custom("nonfinite JSON"));
        }
        Ok(Unique)
    }
    fn visit_unit<E: de::Error>(self) -> std::result::Result<Unique, E> {
        Ok(Unique)
    }
}
pub fn decode(b: &[u8]) -> Result<Value> {
    if b.len() > FRAME_LIMIT {
        return Err(Error::Input);
    }
    let mut d = serde_json::Deserializer::from_slice(b);
    Unique::deserialize(&mut d).map_err(|_| Error::Input)?;
    d.end().map_err(|_| Error::Input)?;
    let v: Value = serde_json::from_slice(b).map_err(|_| Error::Input)?;
    fn finite(v: &Value) -> bool {
        match v {
            Value::Number(n) if n.to_string().contains(['.', 'e', 'E']) => {
                n.as_f64().is_some_and(f64::is_finite)
            }
            Value::Array(a) => a.iter().all(finite),
            Value::Object(m) => m.values().all(finite),
            _ => true,
        }
    }
    if !finite(&v) {
        return Err(Error::Input);
    }
    Ok(v)
}
pub fn fields(v: &Value, allowed: &[&str]) -> bool {
    v.as_object()
        .is_some_and(|m| m.keys().all(|k| allowed.contains(&k.as_str())))
}
pub fn nonempty(v: &Value) -> bool {
    v.as_str().is_some_and(|s| !s.trim().is_empty())
}
pub fn integer(v: &Value) -> bool {
    v.as_number().is_some_and(|n| {
        let s = n.to_string();
        !s.starts_with('-') && !s.contains(['.', 'e', 'E'])
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn strict_numbers_and_keys() {
        for input in [
            r#"{"a":1,"a":2}"#,
            r#"{"a":{"x":0,"\u0078":1}}"#,
            "NaN",
            "1e999",
            "[Infinity]",
            "{} {}",
        ] {
            assert!(decode(input.as_bytes()).is_err(), "{input}");
        }
        let text =
            r#"{"large":9007199254740993,"huge":184467440737095516170,"choices":{"z":1,"a":2}}"#;
        let v = decode(text.as_bytes()).unwrap();
        assert_eq!(serde_json::to_string(&v).unwrap(), text);
        assert!(integer(&v["huge"]));
        assert!(!integer(&decode(b"5.0").unwrap()));
        assert!(!integer(&Value::Bool(true)));
    }
}
