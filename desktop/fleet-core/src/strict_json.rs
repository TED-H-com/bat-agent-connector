//! Reject decoded duplicate keys before serde_json can discard their evidence.
use serde::{
    de::{self, MapAccess, SeqAccess, Visitor},
    Deserialize, Deserializer,
};
use serde_json::{Map, Number, Value};
use std::{collections::HashSet, fmt};

struct Strict(Value, bool);
impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        struct JsonVisitor;
        impl<'de> Visitor<'de> for JsonVisitor {
            type Value = Strict;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("bounded JSON without duplicate object keys")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> std::result::Result<Strict, E> {
                Ok(Strict(v.into(), false))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> std::result::Result<Strict, E> {
                Ok(Strict(v.into(), false))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> std::result::Result<Strict, E> {
                Ok(Strict(v.into(), false))
            }
            fn visit_f64<E: de::Error>(self, v: f64) -> std::result::Result<Strict, E> {
                Number::from_f64(v)
                    .map(|n| Strict(n.into(), false))
                    .ok_or_else(|| E::custom("invalid number"))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> std::result::Result<Strict, E> {
                Ok(Strict(v.into(), false))
            }
            fn visit_unit<E: de::Error>(self) -> std::result::Result<Strict, E> {
                Ok(Strict(Value::Null, false))
            }
            fn visit_seq<A: SeqAccess<'de>>(
                self,
                mut seq: A,
            ) -> std::result::Result<Strict, A::Error> {
                let mut out = Vec::new();
                let mut ambiguous = false;
                while let Some(Strict(value, child_ambiguous)) = seq.next_element()? {
                    ambiguous |= child_ambiguous;
                    out.push(value);
                }
                Ok(Strict(Value::Array(out), ambiguous))
            }
            fn visit_map<A: MapAccess<'de>>(
                self,
                mut map: A,
            ) -> std::result::Result<Strict, A::Error> {
                let mut out = Map::new();
                let mut keys = HashSet::new();
                let mut ambiguous = false;
                while let Some(key) = map.next_key::<String>()? {
                    // PowerShell property lookup is case-insensitive. Escape spellings have
                    // already been decoded by serde. Defer ambiguity until a full valid
                    // parse: truncation/Unicode/number/depth errors always take priority.
                    ambiguous |= !keys.insert(key.to_lowercase());
                    let Strict(value, child_ambiguous) = map.next_value()?;
                    ambiguous |= child_ambiguous;
                    if let Some(mut replaced) = out.insert(key, value) {
                        scrub(&mut replaced);
                    }
                }
                Ok(Strict(Value::Object(out), ambiguous))
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
    let Strict(mut value, ambiguous) =
        serde_json::from_slice::<Strict>(bytes).map_err(|_| "JSON_INVALID")?;
    if ambiguous {
        scrub(&mut value);
        return Err("JSON_AMBIGUOUS");
    }
    Ok(value)
}

/// Also scrub replaced duplicate values and completed ambiguous trees.
pub(crate) fn scrub(value: &mut Value) {
    use zeroize::Zeroize;
    match value {
        Value::String(s) => s.zeroize(),
        Value::Array(values) => values.iter_mut().for_each(scrub),
        Value::Object(values) => {
            for (mut key, mut value) in std::mem::take(values) {
                key.zeroize();
                scrub(&mut value);
            }
        }
        _ => (),
    }
}
