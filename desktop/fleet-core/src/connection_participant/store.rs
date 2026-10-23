use super::{model::*, wire, *};
use crate::{connection_publish::AbandonDecision, connection_setup::Artifact, digest};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Part {
    Intent,
    Claim,
    Envelope,
    Attempt(u8),
    Receipt(u8),
    Acknowledgement(u8),
}
impl Part {
    fn order(self) -> Result<u8> {
        match self {
            Self::Intent => Ok(0),
            Self::Claim => Ok(1),
            Self::Envelope => Ok(2),
            Self::Attempt(i) if i < 5 => Ok(3 + i),
            Self::Receipt(i) if i < 2 => Ok(8 + 2 * i),
            Self::Acknowledgement(i) if i < 2 => Ok(9 + 2 * i),
            _ => Err(Error::Invalid),
        }
    }
    pub fn filename(self) -> Result<String> {
        Ok(format!("{:02}.json", self.order()?))
    }
    pub fn from_filename(name: &str) -> Result<Self> {
        (0..12)
            .map(|n| match n {
                0 => Self::Intent,
                1 => Self::Claim,
                2 => Self::Envelope,
                3..=7 => Self::Attempt(n - 3),
                8 | 10 => Self::Receipt((n - 8) / 2),
                _ => Self::Acknowledgement((n - 9) / 2),
            })
            .find(|p| p.filename().as_deref() == Ok(name))
            .ok_or(Error::Invalid)
    }
    fn limit(self) -> usize {
        match self {
            Self::Intent => Intent::limit(),
            Self::Envelope => {
                bound("native_plan_bytes") + bound("handoff_bytes") + bound("receipt_bytes")
            }
            Self::Receipt(_) | Self::Acknowledgement(_) => bound("receipt_bytes") + 256,
            _ => bound("handoff_bytes"),
        }
    }
}
/// Native protected storage must implement this contract; there is no default disk fallback.
/// Reads are bounded before allocation, no-follow, regular-file only; only genuine
/// absence returns None. Enumerate at most twelve parts; reject extra/unknown residue.
/// Create-only stages, syncs, publishes without replacement, and reads back under
/// native provenance/ancestor guards. Production must also serialize this operation.
pub trait Backend {
    fn available(&mut self) -> Result<()>;
    fn entries(&mut self, namespace: &Namespace) -> Result<Vec<Part>>;
    fn read(&mut self, namespace: &Namespace, part: Part, bound: usize) -> Result<Option<Vec<u8>>>;
    fn create_only(&mut self, namespace: &Namespace, part: Part, bytes: &[u8]) -> Result<()>;
}
pub struct Unavailable;
impl Backend for Unavailable {
    fn available(&mut self) -> Result<()> {
        Err(Error::Unavailable)
    }
    fn entries(&mut self, _: &Namespace) -> Result<Vec<Part>> {
        Err(Error::Unavailable)
    }
    fn read(&mut self, _: &Namespace, _: Part, _: usize) -> Result<Option<Vec<u8>>> {
        Err(Error::Unavailable)
    }
    fn create_only(&mut self, _: &Namespace, _: Part, _: &[u8]) -> Result<()> {
        Err(Error::Unavailable)
    }
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StoredPart {
    intent_digest: String,
    payload: String,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Effect {
    artifact: Artifact,
}
#[derive(Serialize, Deserialize)]
#[serde(untagged, deny_unknown_fields)]
enum Attempt {
    Effect {
        effect: Effect,
    },
    Abandon {
        abandonment: AbandonDecision,
        journal_digest: String,
    },
}
#[derive(Clone, PartialEq, Eq)]
pub enum Next {
    Claim,
    ReplayClaim,
    Readback,
    Executor { read_only: bool },
    Redeliver { index: u8, body: Vec<u8> },
    Done,
}
impl std::fmt::Debug for Next {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Redeliver { index, .. } => f
                .debug_tuple("Redeliver [private body]")
                .field(index)
                .finish(),
            Self::Executor { read_only } => f.debug_tuple("Executor").field(read_only).finish(),
            Self::Claim => f.write_str("Claim"),
            Self::ReplayClaim => f.write_str("ReplayClaim"),
            Self::Readback => f.write_str("Readback"),
            Self::Done => f.write_str("Done"),
        }
    }
}
/// Validated stored parts, never a business-operation authority or execution permit.
pub struct Record {
    intent: Intent,
    parts: BTreeMap<Part, Vec<u8>>,
    abandonment: Option<AbandonDecision>,
}
impl Record {
    pub fn intent(&self) -> &Intent {
        &self.intent
    }
    /// Private operation material for native recovery, never a frontend export.
    pub fn part(&self, part: Part) -> Option<&[u8]> {
        self.parts.get(&part).map(Vec::as_slice)
    }
    fn value(&self, part: Part) -> Option<Value> {
        self.parts
            .get(&part)
            .map(|b| parse(b, part.limit()).expect("validated part"))
    }
    pub fn abandonment(&self) -> Option<&AbandonDecision> {
        self.abandonment.as_ref()
    }
    fn add(&mut self, part: Part, bytes: &[u8]) -> Result<()> {
        part.order()?;
        ensure(bytes.len() <= part.limit())?;
        if let Some(saved) = self.parts.get(&part) {
            return if saved == bytes {
                Ok(())
            } else {
                Err(Error::Conflict)
            };
        }
        let value = parse(bytes, part.limit())?;
        let envelope = self.value(Part::Envelope);
        match part {
            Part::Intent => return Err(Error::Conflict),
            Part::Claim => ensure(
                value.as_object().is_some_and(|v| v.len() == 1)
                    && value["requested_at_ms"].as_u64().is_some_and(|v| {
                        v > 0
                            && v.checked_add(bound("challenge_ttl_s") as u64 * 1000)
                                .is_some()
                    }),
            )?,
            Part::Envelope => {
                ensure(self.parts.contains_key(&Part::Claim))?;
                wire::envelope(&self.intent, &value)?;
            }
            Part::Attempt(i) => {
                ensure(
                    envelope.is_some()
                        && (0..i).all(|n| self.parts.contains_key(&Part::Attempt(n))),
                )?;
                let attempt: Attempt = typed(value)?;
                match attempt {
                    Attempt::Effect { effect } => {
                        ensure(
                            !self.parts.contains_key(&Part::Receipt(0))
                                && self.abandonment.is_none(),
                        )?;
                        ensure(envelope.as_ref().is_some_and(|e| {
                            e["read_only"] == false && e["native_handoff"]["receipt"].is_null()
                        }))?;
                        for n in 0..i {
                            let old: Attempt =
                                typed(self.value(Part::Attempt(n)).ok_or(Error::Invalid)?)?;
                            ensure(
                                !matches!(old, Attempt::Effect { effect: e } if e.artifact == effect.artifact),
                            )?;
                        }
                    }
                    Attempt::Abandon {
                        abandonment,
                        journal_digest,
                    } => {
                        ensure(
                            self.abandonment.is_none()
                                && !self.parts.contains_key(&Part::Receipt(1))
                                && hash(&journal_digest)
                                && text(&abandonment.reference, 1024)
                                && abandonment.reference.len() <= 1024
                                && abandonment.recorded_at_unix_ms > 0,
                        )?;
                        if let Some(first) = self.value(Part::Receipt(0)) {
                            ensure(
                                first["outcome"] == "uncertain"
                                    && self.parts.contains_key(&Part::Acknowledgement(0)),
                            )?;
                        }
                        self.abandonment = Some(abandonment);
                    }
                }
            }
            Part::Receipt(i) => {
                let n = &envelope.as_ref().ok_or(Error::Invalid)?["native_handoff"];
                ensure(bytes.len() <= bound("receipt_bytes"))?;
                wire::receipt(&self.intent, n, &value)?;
                if i == 1 {
                    ensure(
                        self.value(Part::Receipt(0))
                            .is_some_and(|r| r["outcome"] == "uncertain")
                            && self.parts.contains_key(&Part::Acknowledgement(0))
                            && value["outcome"] != "uncertain",
                    )?;
                }
                let effects: Vec<_> = self
                    .parts
                    .keys()
                    .filter_map(|p| {
                        if matches!(p, Part::Attempt(_)) {
                            match typed::<Attempt>(self.value(*p)?) {
                                Ok(Attempt::Effect { effect }) => Some(effect.artifact),
                                _ => None,
                            }
                        } else {
                            None
                        }
                    })
                    .collect();
                if value["outcome"] == "refused" {
                    ensure(effects.is_empty())?;
                }
                if value["outcome"] == "applied" {
                    for output in self.intent.native_plan()["artifacts"]
                        .as_array()
                        .ok_or(Error::Invalid)?
                    {
                        if output["before_digest"] != output["after_digest"] {
                            ensure(effects.iter().any(|a| {
                                output["role"] == crate::connection_contract::artifact_role(*a)
                            }))?;
                        }
                    }
                }
                if value["outcome"] == "abandoned" {
                    ensure(self.abandonment.is_some())?;
                }
            }
            Part::Acknowledgement(i) => wire::acknowledgement(
                self.parts.get(&Part::Receipt(i)).ok_or(Error::Invalid)?,
                &value,
            )?,
        }
        self.parts.insert(part, bytes.to_vec());
        Ok(())
    }
    pub fn recover(&self, now_ms: u64) -> Result<Next> {
        ensure(now_ms > 0)?;
        for i in [1, 0] {
            if let Some(body) = self.parts.get(&Part::Receipt(i)) {
                if !self.parts.contains_key(&Part::Acknowledgement(i)) {
                    return Ok(Next::Redeliver {
                        index: i,
                        body: body.clone(),
                    });
                }
                return Ok(
                    if self.value(Part::Receipt(i)).ok_or(Error::Invalid)?["outcome"] == "uncertain"
                    {
                        Next::Executor { read_only: true }
                    } else {
                        Next::Done
                    },
                );
            }
        }
        let Some(claim) = self.value(Part::Claim) else {
            return Ok(Next::Claim);
        };
        let requested = claim["requested_at_ms"].as_u64().ok_or(Error::Invalid)?;
        ensure(now_ms >= requested)?;
        let Some(envelope) = self.value(Part::Envelope) else {
            return Ok(
                if now_ms - requested < bound("challenge_ttl_s") as u64 * 1000 {
                    Next::ReplayClaim
                } else {
                    Next::Readback
                },
            );
        };
        Ok(Next::Executor {
            read_only: envelope["read_only"] == true
                || (now_ms as f64 / 1000.0)
                    >= envelope["native_handoff"]["expires_at"]
                        .as_f64()
                        .ok_or(Error::Invalid)?
                || self.parts.keys().any(|p| matches!(p, Part::Attempt(_)))
                || !envelope["native_handoff"]["receipt"].is_null(),
        })
    }
}
pub struct Store<B> {
    backend: B,
}
impl<B: Backend> Store<B> {
    pub fn new(backend: B) -> Self {
        Self { backend }
    }
    pub fn available(&mut self) -> Result<()> {
        self.backend.available()
    }
    /// Reopen only the original stored reference. The caller's loader must prove
    /// protected frozen-history provenance; it must never generate a replacement plan.
    pub fn open(
        &mut self,
        scope: &Scope,
        operation_id: &str,
        history: impl FnOnce(
            &crate::connection_publish::FrozenPlanRef,
        ) -> Result<crate::connection_publish::FrozenHistory>,
    ) -> Result<Option<Record>> {
        self.available()?;
        let namespace = Namespace::for_operation(scope, operation_id)?;
        let parts = self.backend.entries(&namespace)?;
        if parts.is_empty() {
            return Ok(None);
        }
        ensure(parts.len() <= 12 && parts.contains(&Part::Intent))?;
        let bytes = self
            .backend
            .read(&namespace, Part::Intent, Intent::limit())?
            .ok_or(Error::Invalid)?;
        let reference = Intent::reference(&bytes, scope, operation_id)?;
        let frozen = history(&reference)?;
        let intent = Intent::decode(&bytes, scope, &frozen)?;
        ensure(intent.accepted().operation_id == operation_id)?;
        self.load(&intent).map(Some)
    }
    pub fn begin(&mut self, intent: &Intent) -> Result<Record> {
        self.available()?;
        let namespace = intent.namespace();
        let bytes = intent.bytes()?;
        if self.backend.entries(&namespace)?.is_empty() {
            self.backend.create_only(&namespace, Part::Intent, &bytes)?;
        }
        self.load(intent)
    }
    pub fn load(&mut self, intent: &Intent) -> Result<Record> {
        self.available()?;
        let namespace = intent.namespace();
        let mut parts = self.backend.entries(&namespace)?;
        ensure(parts.len() <= 12 && parts.contains(&Part::Intent))?;
        let stored = self
            .backend
            .read(&namespace, Part::Intent, Intent::limit())?
            .ok_or(Error::Invalid)?;
        let dispatch = parse(&stored, Intent::limit())?;
        validate_variant(&dispatch)?;
        if stored != intent.bytes()? {
            return Err(Error::Conflict);
        }
        parts.sort_by_key(|p| p.order().unwrap_or(u8::MAX));
        ensure(parts.windows(2).all(|p| p[0] != p[1]))?;
        let mut record = Record {
            intent: intent.clone(),
            parts: BTreeMap::new(),
            abandonment: None,
        };
        for part in parts.into_iter().filter(|p| *p != Part::Intent) {
            part.order()?;
            let bytes = self
                .backend
                .read(&namespace, part, 2 * part.limit() + 256)?
                .ok_or(Error::Invalid)?;
            let saved: StoredPart = typed(parse(&bytes, 2 * part.limit() + 256)?)?;
            ensure(saved.intent_digest == digest(&stored))?;
            record.add(part, saved.payload.as_bytes())?;
        }
        Ok(record)
    }
    pub fn append(&mut self, intent: &Intent, part: Part, payload: &[u8]) -> Result<Record> {
        let mut record = self.load(intent)?;
        if record.parts.get(&part).is_some_and(|b| b == payload) {
            return Ok(record);
        }
        record.add(part, payload)?;
        let saved = encode(&StoredPart {
            intent_digest: digest(&intent.bytes()?),
            payload: String::from_utf8(payload.to_vec()).map_err(|_| Error::Invalid)?,
        })?;
        self.backend
            .create_only(&intent.namespace(), part, &saved)?;
        self.load(intent)
    }
}
