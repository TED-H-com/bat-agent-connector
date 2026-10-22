//! Reject decoded duplicate keys before serde_json can discard their evidence.
use serde::{
    de::{self, MapAccess, SeqAccess, Visitor},
    Deserialize, Deserializer,
};
use serde_json::{Map, Number, Value};
use std::{collections::HashSet, fmt};

struct Strict(Value);
impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        struct JsonVisitor;
        impl<'de> Visitor<'de> for JsonVisitor {
            type Value = Strict;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("bounded JSON without duplicate object keys")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> std::result::Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> std::result::Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> std::result::Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_f64<E: de::Error>(self, v: f64) -> std::result::Result<Strict, E> {
                Number::from_f64(v)
                    .map(|n| Strict(n.into()))
                    .ok_or_else(|| E::custom("invalid number"))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> std::result::Result<Strict, E> {
                Ok(Strict(v.into()))
            }
            fn visit_unit<E: de::Error>(self) -> std::result::Result<Strict, E> {
                Ok(Strict(Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(
                self,
                mut seq: A,
            ) -> std::result::Result<Strict, A::Error> {
                let mut out = Vec::new();
                while let Some(Strict(value)) = seq.next_element()? {
                    out.push(value);
                }
                Ok(Strict(Value::Array(out)))
            }
            fn visit_map<A: MapAccess<'de>>(
                self,
                mut map: A,
            ) -> std::result::Result<Strict, A::Error> {
                let mut out = Map::new();
                let mut keys = HashSet::new();
                while let Some(key) = map.next_key::<String>()? {
                    // PowerShell property lookup is case-insensitive. Escape spellings have
                    // already been decoded by serde. Reject ambiguity at every nesting level.
                    if !keys.insert(key.to_lowercase()) {
                        return Err(de::Error::custom("duplicate object key"));
                    }
                    let Strict(value) = map.next_value()?;
                    out.insert(key, value);
                }
                Ok(Strict(Value::Object(out)))
            }
        }
        deserializer.deserialize_any(JsonVisitor)
    }
}

pub fn parse(bytes: &[u8], bound: usize) -> crate::Result<Value> {
    parse_classified(bytes, bound).map_err(|reason| {
        if reason == "JSON_BOUND" {
            reason
        } else {
            "JSON_INVALID"
        }
    })
}

/// Fixed classification for admission; never expose serde's input-bearing error.
pub(crate) fn parse_classified(bytes: &[u8], bound: usize) -> crate::Result<Value> {
    if bytes.len() > bound {
        return Err("JSON_BOUND");
    }
    serde_json::from_slice::<Strict>(bytes)
        .map(|v| v.0)
        .map_err(|error| {
            if error.is_data() && error.to_string().starts_with("duplicate object key") {
                "JSON_AMBIGUOUS"
            } else {
                "JSON_INVALID"
            }
        })
}
