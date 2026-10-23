//! Native evidence only. No operation authority, renewal, transport or UI export.
use super::*;
use crate::connection_setup::frozen;
pub use frozen::FROZEN_PLAN_LIMIT;

const DECISION_LIMIT: usize = 32768;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AuthorizationPoint {
    BeforeDirectory,
    BeforeArtifact,
    /// Called before staging AND immediately before replacement, under both guards.
    ReplacementGuard,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactState {
    Published,
    Before,
    ThirdBytes,
    Absent,
    Unreadable,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactObservation {
    pub artifact: Artifact,
    pub state: ArtifactState,
    /// Actual bytes only. Absence/unreadability are never hashed as empty bytes.
    pub sha256: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Inspection {
    pub artifacts: Vec<ArtifactObservation>,
    /// All four after digests AND the existing configuration/installation loaders.
    pub configuration_valid: bool,
}

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AbandonDecision {
    /// Opaque caller linkage, never interpreted as authorization by this core.
    pub reference: String,
    /// Caller-recorded UTC time, retained unchanged for deterministic retries.
    pub recorded_at_unix_ms: u64,
}
impl AbandonDecision {
    fn valid(&self) -> bool {
        !self.reference.is_empty() && self.reference.len() <= 1024 && self.recorded_at_unix_ms > 0
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DecisionBoundary {
    BeforeRecord,
    AfterRecord,
}

#[derive(PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Scope {
    pointer: PathBuf,
    startup: PathBuf,
    user: PathBuf,
    owner: OwnerEvidence,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct DecisionRecord {
    version: u32,
    scope: Scope,
    journal_digest: String,
    review_digest: String,
    decision: AbandonDecision,
    observations: Vec<ArtifactObservation>,
}
/// Native storage reference only; source bytes and paths stay in private storage.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FrozenPlanRef {
    pub review_digest: String,
    pub sha256: String,
}

/// Immutable result of the original frozen-plan validator (32 MiB maximum).
/// Decoding establishes consistency, not provenance or permission to execute.
pub struct FrozenHistory {
    pub(crate) reference: FrozenPlanRef,
    pub(crate) plan: PublicationPlan,
}
impl FrozenHistory {
    pub fn decode(reference: &FrozenPlanRef, bytes: &[u8]) -> Result<Self> {
        if !hash_valid(&reference.review_digest)
            || !hash_valid(&reference.sha256)
            || bytes.len() > FROZEN_PLAN_LIMIT
            || digest(bytes) != reference.sha256
        {
            return Err(Error::InvalidPlan);
        }
        Ok(Self {
            reference: reference.clone(),
            plan: frozen::decode(bytes, &reference.review_digest)?,
        })
    }
    pub fn plan(&self) -> &PublicationPlan {
        &self.plan
    }
    pub fn reference(&self) -> &FrozenPlanRef {
        &self.reference
    }
}

/// Read-only validated history. Fields cannot be supplied by a deserializer.
/// Storage provenance and guarded freshness remain the native caller's duty.
pub struct PublicationHistory {
    pub(crate) frozen: FrozenHistory,
    pub(crate) pointer: PathBuf,
    pub(crate) journal_sha256: Option<String>,
    pub(crate) reached: Vec<(Artifact, bool)>,
    pub(crate) complete: bool,
    pub(crate) decision: Option<(String, Vec<ArtifactObservation>)>,
}
impl PublicationHistory {
    pub fn frozen(&self) -> &FrozenHistory {
        &self.frozen
    }
    pub fn journal_sha256(&self) -> Option<&str> {
        self.journal_sha256.as_deref()
    }
    /// None: unreached; Some(false): Intended; Some(true): Published.
    pub fn reached(&self, artifact: Artifact) -> Option<bool> {
        self.reached
            .iter()
            .find(|(a, _)| *a == artifact)
            .map(|(_, published)| *published)
    }
    pub fn complete(&self) -> bool {
        self.complete
    }
    pub fn decision(&self) -> Option<(&str, &[ArtifactObservation])> {
        self.decision
            .as_ref()
            .map(|(hash, observations)| (hash.as_str(), observations.as_slice()))
    }
}

impl Publisher {
    /// Caller holds Launcher then Monitor on this thread. This accessor acquires
    /// no nested guards, writes nothing, and never calls ownership(). The original
    /// journal/decision validators retain their 32 KiB and 1024-entry bounds.
    /// Inherits MIGRATION_PENDING and MONITOR_ALREADY_RUNNING effect gates.
    pub fn read_history(
        &self,
        frozen: FrozenHistory,
        p: &mut impl Platform,
    ) -> Result<PublicationHistory> {
        self.validate_plan(&frozen.plan)?;
        let original = self.original_journal(&frozen.plan, p)?;
        self.history_view(frozen, p, original)
    }
    /// Historical evidence only: caller holds Launcher and verifies
    /// native storage provenance. Unlike read_history this does not require
    /// migration/Monitor absence. It checks current SID, exact plan and the same
    /// original journal/decision validators; it never authorizes publication.
    pub fn read_evidence_history(
        &self,
        frozen: FrozenHistory,
        p: &impl Platform,
    ) -> Result<PublicationHistory> {
        self.validate_plan(&frozen.plan)?;
        let login = p.login()?;
        let owner = frozen::owner(&frozen.plan);
        if !login.valid() || login.owner_sid != owner.owner_sid {
            return Err(Error::Gate("OTHER_LOGIN_OWNER"));
        }
        let original = self.evidence_journal(&frozen.plan, p)?;
        self.history_view(frozen, p, original)
    }
    fn history_view(
        &self,
        frozen: FrozenHistory,
        p: &impl Platform,
        original: Option<(Journal, Vec<u8>)>,
    ) -> Result<PublicationHistory> {
        let mut history = PublicationHistory {
            frozen,
            pointer: self.pointer.clone(),
            journal_sha256: None,
            reached: Vec::new(),
            complete: false,
            decision: None,
        };
        if let Some((journal, bytes)) = original {
            history.journal_sha256 = Some(digest(&bytes));
            history.complete = journal.complete();
            history.reached = journal
                .receipts
                .iter()
                .map(|r| (r.artifact, r.state == State::Published))
                .collect();
            if let Some((record, path)) = self.settled(p, &journal, &bytes)? {
                // The original validator binds the filename to these exact bytes.
                let encoded = read(&path, DECISION_LIMIT)?.ok_or(Error::DecisionConflict)?;
                let hash = digest(&encoded);
                if path
                    != self
                        .decision_directory(&journal.review_digest)
                        .join(format!("abandon-{hash}.json"))
                {
                    return Err(Error::DecisionConflict);
                }
                history.decision = Some((hash, record.observations));
            }
        }
        Ok(history)
    }
    fn scope(&self, owner: &OwnerEvidence) -> Scope {
        Scope {
            pointer: self.pointer.clone(),
            startup: self.startup.clone(),
            user: self.user.clone(),
            owner: owner.clone(),
        }
    }
    fn sibling(&self, name: &str) -> PathBuf {
        self.pointer.parent().unwrap().join(name)
    }
    fn observations(&self, plan: &PublicationPlan) -> Vec<ArtifactObservation> {
        ARTIFACTS
            .iter()
            .map(|&a| {
                let (state, sha256) = match read(&self.path(plan, a), limit(a)) {
                    Ok(Some(bytes)) => {
                        let hash = digest(&bytes);
                        let state = if hash == plan.output(a).sha256 {
                            ArtifactState::Published
                        } else if plan
                            .steps
                            .iter()
                            .any(|s| s.artifact == a && s.before_digest.as_ref() == Some(&hash))
                        {
                            ArtifactState::Before
                        } else {
                            ArtifactState::ThirdBytes
                        };
                        (state, Some(hash))
                    }
                    Ok(None) => (ArtifactState::Absent, None),
                    Err(_) => (ArtifactState::Unreadable, None),
                };
                ArtifactObservation {
                    artifact: a,
                    state,
                    sha256,
                }
            })
            .collect()
    }
    fn original_journal(
        &self,
        plan: &PublicationPlan,
        p: &mut impl Platform,
    ) -> Result<Option<(Journal, Vec<u8>)>> {
        let owner = frozen::owner(plan);
        self.gates(p, owner)?;
        self.private(p, &self.directory, owner)?;
        let path = self.directory.join(format!("{}.json", plan.review_digest));
        self.private(p, &path, owner)?;
        if read(&path, JOURNAL_LIMIT)?.is_none() {
            return Ok(None);
        }
        let (journal, bytes) = self.read_journal(p, &path)?;
        if journal.steps != plan.steps || journal.generator_version != plan.generator_version {
            return Err(Error::ReceiptConflict);
        }
        Ok(Some((journal, bytes)))
    }
    fn evidence_journal(
        &self,
        plan: &PublicationPlan,
        p: &impl Platform,
    ) -> Result<Option<(Journal, Vec<u8>)>> {
        let owner = frozen::owner(plan);
        self.private(p, &self.directory, owner)?;
        let path = self.directory.join(format!("{}.json", plan.review_digest));
        self.private(p, &path, owner)?;
        if read(&path, JOURNAL_LIMIT)?.is_none() {
            return Ok(None);
        }
        let (journal, bytes) = self.journal_record(&path)?;
        self.private(p, &self.directory, owner)?;
        self.private(p, &path, owner)?;
        if journal.steps != plan.steps || journal.generator_version != plan.generator_version {
            return Err(Error::ReceiptConflict);
        }
        Ok(Some((journal, bytes)))
    }
    /// Read-only, including journals. A published observation means proven after
    /// bytes, even if its receipt lags. Before/absent bytes never cause forward work.
    pub fn inspect(&self, plan: &PublicationPlan, p: &mut impl Platform) -> Result<Inspection> {
        self.validate_plan(plan)?;
        let _launcher = p.launcher_guard()?;
        let _monitor = p.monitor_guard()?;
        self.original_journal(plan, p)?;
        let artifacts = self.observations(plan);
        let configuration_valid = artifacts
            .iter()
            .all(|o| o.state == ArtifactState::Published)
            && self.kit(plan).is_ok()
            && crate::installation::Snapshot::load(&self.pointer)
                .and_then(|s| s.verify_current())
                .is_ok();
        Ok(Inspection {
            artifacts,
            configuration_valid,
        })
    }
    fn private_directory(
        &self,
        p: &mut impl Platform,
        path: &Path,
        owner: &OwnerEvidence,
    ) -> Result<()> {
        self.private(p, path, owner)?;
        p.prepare_private_directory(path, owner)?;
        self.private(p, path, owner)
    }
    pub(crate) fn decision_directory(&self, review: &str) -> PathBuf {
        self.sibling("fleet-publication-decisions").join(review)
    }
    fn observations_valid(record: &DecisionRecord, journal: &Journal) -> bool {
        record.observations.len() == ARTIFACTS.len()
            && record.observations.iter().zip(ARTIFACTS).all(|(o, a)| {
                if o.artifact != a {
                    return false;
                }
                let step = journal.steps.iter().find(|s| s.artifact == a);
                match o.state {
                    ArtifactState::Absent | ArtifactState::Unreadable => o.sha256.is_none(),
                    _ => o.sha256.as_ref().is_some_and(|h| {
                        hash_valid(h)
                            && match o.state {
                                ArtifactState::Published => {
                                    step.is_none_or(|s| h == &s.after_digest)
                                }
                                ArtifactState::Before => {
                                    step.is_some_and(|s| s.before_digest.as_ref() == Some(h))
                                }
                                ArtifactState::ThirdBytes => step.is_none_or(|s| {
                                    h != &s.after_digest && s.before_digest.as_ref() != Some(h)
                                }),
                                _ => false,
                            }
                    }),
                }
            })
    }
    /// A filename/presence alone never settles an unfinished journal. Decisions
    /// bind the exact old bytes and native scope, not today's artifact contents.
    pub(super) fn has_decision(
        &self,
        p: &impl Platform,
        journal: &Journal,
        bytes: &[u8],
    ) -> Result<bool> {
        Ok(self.settled(p, journal, bytes)?.is_some())
    }
    fn settled(
        &self,
        p: &impl Platform,
        journal: &Journal,
        bytes: &[u8],
    ) -> Result<Option<(DecisionRecord, PathBuf)>> {
        let owner = &journal.steps[0].owner;
        let directory = self.decision_directory(&journal.review_digest);
        self.private(p, directory.parent().unwrap(), owner)?;
        self.private(p, &directory, owner)?;
        let entries = match std::fs::read_dir(&directory) {
            Ok(entries) => entries,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(_) => return Err(Error::DecisionConflict),
        };
        let mut found = None;
        for (count, entry) in entries.enumerate() {
            if count >= 1024 {
                return Err(Error::DecisionConflict);
            }
            let path = entry.map_err(|_| Error::DecisionConflict)?.path();
            if path
                .file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with(".fleet-publication-") && n.ends_with(".tmp"))
            {
                continue;
            }
            self.private(p, &path, owner)?;
            let encoded = read(&path, DECISION_LIMIT)?.ok_or(Error::DecisionConflict)?;
            let record: DecisionRecord = serde_json::from_value(
                strict_json::parse(&encoded, DECISION_LIMIT)
                    .map_err(|_| Error::DecisionConflict)?,
            )
            .map_err(|_| Error::DecisionConflict)?;
            if found.is_some()
                || record.version != 1
                || record.scope != self.scope(owner)
                || record.review_digest != journal.review_digest
                || record.journal_digest != digest(bytes)
                || !record.decision.valid()
                || !Self::observations_valid(&record, journal)
                || path != directory.join(format!("abandon-{}.json", digest(&encoded)))
            {
                return Err(Error::DecisionConflict);
            }
            found = Some((record, path));
        }
        Ok(found)
    }
    /// Explicitly settle remaining work without performing or rolling back any
    /// artifact. The original journal is immutable throughout this operation.
    pub fn abandon_remaining(
        &self,
        plan: &PublicationPlan,
        decision: &AbandonDecision,
        p: &mut impl Platform,
        boundary: &mut impl FnMut(DecisionBoundary) -> crate::Result<()>,
    ) -> Result<PathBuf> {
        self.validate_plan(plan)?;
        if !decision.valid() {
            return Err(Error::DecisionConflict);
        }
        let _launcher = p.launcher_guard()?;
        let _monitor = p.monitor_guard()?;
        let (journal, original) = self
            .original_journal(plan, p)?
            .ok_or(Error::ReceiptConflict)?;
        // Always read every artifact, including on an immutable decision replay.
        let observations = self.observations(plan);
        if let Some((record, path)) = self.settled(p, &journal, &original)? {
            return if record.decision == *decision {
                Ok(path)
            } else {
                Err(Error::DecisionConflict)
            };
        }
        if journal.complete() {
            return Err(Error::DecisionConflict);
        }
        let owner = frozen::owner(plan);
        let record = DecisionRecord {
            version: 1,
            scope: self.scope(owner),
            journal_digest: digest(&original),
            review_digest: plan.review_digest.clone(),
            decision: decision.clone(),
            observations,
        };
        let bytes = serde_json::to_vec(&record).map_err(|_| Error::DecisionConflict)?;
        let directory = self.decision_directory(&plan.review_digest);
        self.private_directory(p, directory.parent().unwrap(), owner)?;
        self.private_directory(p, &directory, owner)?;
        let path = directory.join(format!("abandon-{}.json", digest(&bytes)));
        boundary(DecisionBoundary::BeforeRecord)?;
        compare_replace::replace(
            &path,
            None,
            Some(&bytes),
            policy(DECISION_LIMIT, Error::DecisionConflict),
            || read(&path, DECISION_LIMIT),
            Some(&mut || {
                self.gates(p, owner)?;
                self.private(p, &directory, owner)?;
                self.private(p, &path, owner)?;
                if self.original_journal(plan, p)?.map(|(_, b)| b).as_ref() != Some(&original)
                    || self.observations(plan) != record.observations
                    || self.settled(p, &journal, &original)?.is_some()
                {
                    return Err(Error::DecisionConflict);
                }
                Ok(())
            }),
        )?;
        boundary(DecisionBoundary::AfterRecord)?;
        if read(&path, DECISION_LIMIT)?.as_deref() != Some(&bytes) {
            return Err(Error::DecisionConflict);
        }
        Ok(path)
    }
    /// Create-only private snapshot, including the complete original generator
    /// input. Persist this returned hash in the native caller's protected state.
    pub fn export_plan(
        &self,
        plan: &PublicationPlan,
        p: &mut impl Platform,
    ) -> Result<FrozenPlanRef> {
        self.validate_plan(plan)?;
        let bytes = frozen::encode(plan)?;
        let _launcher = p.launcher_guard()?;
        let _monitor = p.monitor_guard()?;
        let owner = frozen::owner(plan);
        self.gates(p, owner)?;
        let directory = self.sibling("fleet-publication-plans");
        self.private_directory(p, &directory, owner)?;
        let path = directory.join(format!("{}.json", plan.review_digest));
        self.private(p, &path, owner)?;
        match read(&path, FROZEN_PLAN_LIMIT)? {
            Some(current) if current == bytes => (),
            Some(_) => return Err(Error::ReceiptConflict),
            None => compare_replace::replace(
                &path,
                None,
                Some(&bytes),
                policy(FROZEN_PLAN_LIMIT, Error::ReceiptConflict),
                || read(&path, FROZEN_PLAN_LIMIT),
                Some(&mut || {
                    self.gates(p, owner)?;
                    self.private(p, &directory, owner)?;
                    self.private(p, &path, owner)
                }),
            )?,
        }
        if read(&path, FROZEN_PLAN_LIMIT)?.as_deref() != Some(&bytes) {
            return Err(Error::ReceiptConflict);
        }
        Ok(FrozenPlanRef {
            review_digest: plan.review_digest.clone(),
            sha256: digest(&bytes),
        })
    }
    /// Only protected native storage can supply a frozen plan. Public fields or
    /// today's disk input never recover a seal; the original builder must agree.
    pub fn load_plan(
        &self,
        reference: &FrozenPlanRef,
        owner: &OwnerEvidence,
        p: &mut impl Platform,
    ) -> Result<PublicationPlan> {
        if !hash_valid(&reference.review_digest) || !hash_valid(&reference.sha256) || !owner.valid()
        {
            return Err(Error::InvalidPlan);
        }
        let _launcher = p.launcher_guard()?;
        let _monitor = p.monitor_guard()?;
        self.gates(p, owner)?;
        let directory = self.sibling("fleet-publication-plans");
        let path = directory.join(format!("{}.json", reference.review_digest));
        self.private(p, &directory, owner)?;
        self.private(p, &path, owner)?;
        let bytes = read(&path, FROZEN_PLAN_LIMIT)?.ok_or(Error::InvalidPlan)?;
        if digest(&bytes) != reference.sha256 {
            return Err(Error::InvalidPlan);
        }
        let plan = frozen::decode(&bytes, &reference.review_digest)?;
        if frozen::owner(&plan) != owner {
            return Err(Error::InvalidPlan);
        }
        self.validate_plan(&plan)?;
        Ok(plan)
    }
}
