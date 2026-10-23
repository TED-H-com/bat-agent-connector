use super::{model::*, wire, *};
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Three attempts per claim/receipt; exhaustion retains all evidence.
pub const POST_SLOTS: u8 = 3;
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum PostTarget {
    Claim,
    Receipt(u8),
}
impl PostTarget {
    pub(super) fn number(self) -> Result<u8> {
        match self {
            Self::Claim => Ok(0),
            Self::Receipt(i) if i < 2 => Ok(i + 1),
            _ => Err(Error::Invalid),
        }
    }
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Fence {
    at_ms: u64,
    body: String,
}
#[derive(Serialize, Deserialize)]
#[serde(
    tag = "source",
    content = "response",
    rename_all = "snake_case",
    deny_unknown_fields
)]
enum Acknowledgement {
    Direct(Value),
    Readback(Value),
}
impl Record {
    pub fn request(&self, target: PostTarget) -> Result<Vec<u8>> {
        target.number()?;
        match target {
            PostTarget::Claim => self
                .value(Part::Claim)
                .and_then(|v| v["body"].as_str().map(|s| s.as_bytes().to_vec()))
                .ok_or(Error::Invalid),
            PostTarget::Receipt(i) => self
                .part(Part::Receipt(i))
                .map(Vec::from)
                .ok_or(Error::Invalid),
        }
    }
    fn slots(&self, target: PostTarget) -> u8 {
        (0..POST_SLOTS)
            .take_while(|i| self.part(Part::Post(target, *i)).is_some())
            .count() as u8
    }
    pub(super) fn possibly_sent(&self, target: PostTarget) -> bool {
        let count = self.slots(target);
        count > 0 && self.part(Part::NotSent(target, count - 1)).is_none()
    }
    pub(super) fn post_next(&self, target: PostTarget, first: Next, retry: Next) -> Next {
        let count = self.slots(target);
        if count == 0 {
            first
        } else if self.possibly_sent(target) {
            Next::Readback
        } else if count == POST_SLOTS {
            Next::RetryExhausted
        } else {
            retry
        }
    }
    pub(super) fn validate_post(
        &self,
        part: Part,
        target: PostTarget,
        slot: u8,
        value: &Value,
    ) -> Result<()> {
        ensure(self.intent.format_version() == 2)?;
        target.number()?;
        ensure(self.part(Part::Envelope).is_none() || target != PostTarget::Claim)?;
        if let PostTarget::Receipt(i) = target {
            ensure(self.part(Part::Acknowledgement(i)).is_none())?;
        }
        match part {
            Part::Post(..) => {
                ensure(
                    slot == self.slots(target)
                        && (slot == 0 || self.part(Part::NotSent(target, slot - 1)).is_some()),
                )?;
                let fence: Fence = typed(value.clone())?;
                let started = self.value(Part::Claim).ok_or(Error::Invalid)?["requested_at_ms"]
                    .as_u64()
                    .ok_or(Error::Invalid)?;
                ensure(fence.at_ms >= started && fence.body.as_bytes() == self.request(target)?)?;
                if target == PostTarget::Claim {
                    ensure(fence.at_ms - started < bound("challenge_ttl_s") as u64 * 1000)?;
                }
            }
            Part::NotSent(..) => {
                // Trusted native caller records this ONLY for failure before entering Bridge's sender.
                ensure(
                    self.part(Part::Post(target, slot)).is_some()
                        && *value == serde_json::json!({"before_sender":true}),
                )?;
            }
            _ => return Err(Error::Invalid),
        }
        Ok(())
    }
    pub(super) fn validate_ack(&self, index: u8, body: &[u8], value: &Value) -> Result<()> {
        match typed::<Acknowledgement>(value.clone())? {
            Acknowledgement::Direct(reply) => {
                ensure(self.possibly_sent(PostTarget::Receipt(index)))?;
                wire::acknowledgement(body, &reply)
            }
            Acknowledgement::Readback(reply) => {
                wire::envelope(&self.intent, &reply)?;
                ensure(reply["read_only"] == true)?;
                let original = self.value(Part::Envelope).ok_or(Error::Invalid)?;
                for key in ["epoch", "effect_id", "challenge", "expires_at"] {
                    ensure(reply["native_handoff"][key] == original["native_handoff"][key])?;
                }
                ensure(
                    reply["native_handoff"]["receipt"]
                        == wire::evidence(parse(body, bound("receipt_bytes"))?)?,
                )
            }
        }
    }
}
