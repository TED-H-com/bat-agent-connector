//! Durable native publication of a reviewed generator plan, one file at a time.
//! No IPC, process effects, credential enrollment or cross-file transaction.
use crate::{
    compare_replace::{self, Policy},
    configuration::{Configuration, Paths},
    connection_setup::{
        Artifact, ArtifactLocation, FileOwnership, OwnerEvidence, PublicationPlan, PublicationStep,
        GENERATOR_VERSION,
    },
    digest, migration, migration_io as io, strict_json,
};
use serde::{Deserialize, Serialize};
use std::path::{Component, Path, PathBuf};

mod recovery;
pub use recovery::{
    AbandonDecision, ArtifactObservation, ArtifactState, AuthorizationPoint, DecisionBoundary,
    FrozenPlanRef, Inspection, FROZEN_PLAN_LIMIT,
};

const JOURNAL_LIMIT: usize = 32768;
const ARTIFACTS: [Artifact; 4] = [
    Artifact::Inventory,
    Artifact::ProfileIndex,
    Artifact::SshReference,
    Artifact::Installation,
];

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Error {
    ThirdBytes { artifact: Artifact },
    Ownership { artifact: Artifact },
    UnfinishedPlan { review_digest: String },
    InvalidPlan,
    Authorization { code: &'static str },
    AbandonedPlan { review_digest: String },
    DecisionConflict,
    ReceiptConflict,
    Gate(&'static str),
}
impl From<&'static str> for Error {
    fn from(code: &'static str) -> Self {
        Self::Gate(code)
    }
}
type Result<T> = std::result::Result<T, Error>;

/// Uses migration's EXISTING launcher/monitor guards, login and absence gates.
/// Windows adapters retain LauncherMutex then MonitorMutex on this thread.
/// No implementation may infer file ownership from a familiar pathname. A new
/// slot needs independent creation evidence even when its file does not exist.
/// The native integration supplies this adapter; this module cannot enroll owners.
pub trait Platform: migration::Platform {
    /// Re-read the existing creation authority and bind owner, artifact and current
    /// bytes. For an absent slot, Owned.sha256 is digest(empty), NOT evidence that
    /// an absent path alone is owned. ReferenceOnly/Unknown always refuse writes.
    fn ownership(
        &self,
        owner: &OwnerEvidence,
        artifact: Artifact,
        path: &Path,
        current: Option<&[u8]>,
    ) -> crate::Result<FileOwnership>;
    /// Re-prove native state ownership/privacy, including ancestor ACLs on Windows.
    /// An absent journal/directory must be authorized by its protected parent and
    /// creation authority. This is mandatory, not a path-based default adapter.
    fn verify_private_state(&self, path: &Path, owner: &OwnerEvidence) -> crate::Result<()>;
}

/// Injectable filesystem boundaries; errors model an interrupted call. Recovery
/// always reads the real files again, never this hook's remembered progress.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Boundary {
    BeforeIntended,
    AfterIntended,
    AfterReplace,
    AfterReadBack,
    BeforePointer,
    AfterPointer,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum State {
    Intended,
    Published,
}
#[derive(Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Receipt {
    version: u32,
    review_digest: String,
    generator_version: u32,
    artifact: Artifact,
    before_digest: Option<String>,
    after_digest: String,
    owner: OwnerEvidence,
    state: State,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Journal {
    version: u32,
    review_digest: String,
    generator_version: u32,
    // Retain the entire intended sequence, including steps not reached yet. A
    // crash between published(N) and intended(N+1) must still block a new plan.
    steps: Vec<PublicationStep>,
    receipts: Vec<Receipt>,
}
impl Journal {
    fn receipt(&self, index: usize, state: State) -> Receipt {
        let step = &self.steps[index];
        Receipt {
            version: 1,
            review_digest: self.review_digest.clone(),
            generator_version: self.generator_version,
            artifact: step.artifact,
            before_digest: step.before_digest.clone(),
            after_digest: step.after_digest.clone(),
            owner: step.owner.clone(),
            state,
        }
    }
    fn complete(&self) -> bool {
        self.receipts.len() == self.steps.len()
            && self.receipts.iter().all(|r| r.state == State::Published)
    }
    fn valid(&self) -> bool {
        self.version == 1
            && self.generator_version == GENERATOR_VERSION
            && hash_valid(&self.review_digest)
            && steps_valid(&self.steps)
            && !self.steps.is_empty()
            && self.receipts.len() <= self.steps.len()
            && self
                .receipts
                .iter()
                .enumerate()
                .all(|(i, r)| *r == self.receipt(i, r.state))
    }
}
fn hash_valid(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn steps_valid(steps: &[PublicationStep]) -> bool {
    steps.len() <= 4
        && steps
            .windows(2)
            .all(|s| s[0].artifact < s[1].artifact && s[0].owner == s[1].owner)
        && steps.iter().all(|s| {
            s.owner.valid()
                && hash_valid(&s.after_digest)
                && s.before_digest
                    .as_ref()
                    .is_none_or(|b| hash_valid(b) && b != &s.after_digest)
        })
}
fn limit(a: Artifact) -> usize {
    if a == Artifact::Installation {
        16384
    } else {
        io::MAX_SLOT
    }
}
fn ancestors(path: &Path) -> crate::Result<()> {
    if !path.is_absolute()
        || path
            .components()
            .any(|p| matches!(p, Component::ParentDir | Component::CurDir))
    {
        return Err("PUBLICATION_INVALID_PATH");
    }
    for parent in path.ancestors().skip(1) {
        io::directory(parent)?;
    }
    Ok(())
}
fn read(path: &Path, bound: usize) -> Result<Option<Vec<u8>>> {
    ancestors(path)?;
    Ok(io::read(path, bound)?)
}
fn policy(bound: usize, changed: Error) -> Policy<Error> {
    Policy {
        limit: bound,
        too_large: Error::Gate("PUBLICATION_TOO_LARGE"),
        invalid_path: Error::Gate("PUBLICATION_INVALID_PATH"),
        changed,
        unavailable: Error::Gate("PUBLICATION_IO"),
        prefix: ".fleet-publication",
        create_only: true,
        skip_equal: true,
        sync_directory: true,
        private: true,
    }
}

pub struct Publisher {
    pointer: PathBuf,
    startup: PathBuf,
    user: PathBuf,
    directory: PathBuf,
}
impl Publisher {
    /// All three paths come from native application-data/startup/home discovery.
    /// In particular the pointer is NOT resolved beneath the plan's kit_root.
    pub fn new(pointer: &Path, startup: &Path, user: &Path) -> Result<Self> {
        for path in [pointer, startup, user] {
            ancestors(path)?;
        }
        if pointer.file_name().is_none_or(|n| n != "fleet.json") {
            return Err(Error::InvalidPlan);
        }
        Ok(Self {
            pointer: pointer.into(),
            startup: startup.into(),
            user: user.into(),
            directory: pointer.parent().unwrap().join("fleet-publications"),
        })
    }
    fn path(&self, plan: &PublicationPlan, artifact: Artifact) -> PathBuf {
        match artifact.location() {
            ArtifactLocation::KitRelative(p) => plan.root.join(p),
            ArtifactLocation::InstallationPointer => self.pointer.clone(),
        }
    }
    fn gates(&self, p: &mut impl Platform, owner: &OwnerEvidence) -> Result<()> {
        let login = p.login()?;
        if !login.valid() || login.owner_sid != owner.owner_sid {
            return Err(Error::Gate("OTHER_LOGIN_OWNER"));
        }
        if migration::Store::new(&self.pointer, &self.startup)?
            .pending()?
            .is_some()
        {
            return Err(Error::Gate("MIGRATION_PENDING"));
        }
        if p.discover()?.is_some() {
            return Err(Error::Gate("MONITOR_ALREADY_RUNNING"));
        }
        Ok(())
    }
    fn prove(
        &self,
        plan: &PublicationPlan,
        p: &mut impl Platform,
        step: &PublicationStep,
    ) -> Result<Option<Vec<u8>>> {
        self.gates(p, &step.owner)?;
        let path = self.path(plan, step.artifact);
        let current = read(&path, limit(step.artifact))?;
        let expected = FileOwnership::Owned {
            owner: step.owner.clone(),
            artifact: step.artifact,
            sha256: digest(current.as_deref().unwrap_or_default()),
        };
        if p.ownership(&step.owner, step.artifact, &path, current.as_deref())? != expected {
            return Err(Error::Ownership {
                artifact: step.artifact,
            });
        }
        Ok(current)
    }
    fn kit(&self, plan: &PublicationPlan) -> Result<()> {
        for a in &ARTIFACTS[..3] {
            let bytes = read(&self.path(plan, *a), limit(*a))?;
            if bytes.as_ref().map(|b| digest(b)).as_deref() != Some(&plan.output(*a).sha256) {
                return Err(Error::ThirdBytes { artifact: *a });
            }
        }
        let config = Configuration::load(Paths::new(
            &plan.root.join("client"),
            &self.user,
            None,
            None,
        )?)?;
        if !config.issues.is_empty() {
            return Err(Error::Gate("PUBLICATION_CONFIGURATION_INVALID"));
        }
        config.verify_current()?;
        Ok(())
    }
    fn private(&self, p: &impl Platform, path: &Path, owner: &OwnerEvidence) -> Result<()> {
        ancestors(path)?;
        p.verify_private_state(path, owner)?;
        crate::publication_state::verify(path)?;
        Ok(())
    }
    fn read_journal(&self, p: &mut impl Platform, path: &Path) -> Result<(Journal, Vec<u8>)> {
        let bytes = read(path, JOURNAL_LIMIT)?.ok_or(Error::ReceiptConflict)?;
        let journal: Journal = serde_json::from_value(strict_json::parse(&bytes, JOURNAL_LIMIT)?)
            .map_err(|_| Error::ReceiptConflict)?;
        if !journal.valid()
            || path
                != self
                    .directory
                    .join(format!("{}.json", journal.review_digest))
        {
            return Err(Error::ReceiptConflict);
        }
        let owner = &journal.steps[0].owner;
        self.gates(p, owner)?;
        self.private(p, &self.directory, owner)?;
        self.private(p, path, owner)?;
        Ok((journal, bytes))
    }
    fn journals(
        &self,
        p: &mut impl Platform,
        plan: &PublicationPlan,
    ) -> Result<Option<(Journal, Vec<u8>)>> {
        io::directory(&self.directory)?;
        let entries = match std::fs::read_dir(&self.directory) {
            Ok(entries) => entries,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(_) => return Err(Error::ReceiptConflict),
        };
        let mut found = None;
        for (count, entry) in entries.enumerate() {
            if count >= 1024 {
                return Err(Error::ReceiptConflict);
            }
            let path = entry.map_err(|_| Error::ReceiptConflict)?.path();
            // A killed process may leave its private create_new staging file.
            if path
                .file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with(".fleet-publication-") && n.ends_with(".tmp"))
            {
                continue;
            }
            let (journal, bytes) = self.read_journal(p, &path)?;
            if journal.review_digest == plan.review_digest {
                if journal.steps != plan.steps
                    || journal.generator_version != plan.generator_version
                {
                    return Err(Error::ReceiptConflict);
                }
                if self.has_decision(p, &journal, &bytes)? {
                    return Err(Error::AbandonedPlan {
                        review_digest: journal.review_digest,
                    });
                }
                found = Some((journal, bytes));
            } else if !journal.complete() && !self.has_decision(p, &journal, &bytes)? {
                return Err(Error::UnfinishedPlan {
                    review_digest: journal.review_digest,
                });
            }
        }
        Ok(found)
    }
    fn record(
        &self,
        plan: &PublicationPlan,
        p: &mut impl Platform,
        journal: &mut Journal,
        saved: &mut Option<Vec<u8>>,
        position: (usize, State),
    ) -> Result<()> {
        let (index, state) = position;
        let receipt = journal.receipt(index, state);
        if journal.receipts.get(index) == Some(&receipt) {
            return Ok(());
        }
        let step = &plan.steps[index];
        let current = self.prove(plan, p, step)?;
        let expected_digest = match state {
            State::Intended => step.before_digest.as_deref(),
            State::Published => Some(step.after_digest.as_str()),
        };
        if current.as_ref().map(|b| digest(b)).as_deref() != expected_digest {
            return Err(Error::ThirdBytes {
                artifact: step.artifact,
            });
        }
        self.private(p, &self.directory, &step.owner)?;
        crate::publication_state::create_directory(&self.directory)?;
        self.private(p, &self.directory, &step.owner)?;
        let path = self.directory.join(format!("{}.json", plan.review_digest));
        self.private(p, &path, &step.owner)?;
        if journal.receipts.len() == index {
            journal.receipts.push(receipt);
        } else {
            journal.receipts[index] = receipt;
        }
        let bytes = serde_json::to_vec(journal).map_err(|_| Error::ReceiptConflict)?;
        compare_replace::replace(
            &path,
            saved.as_deref(),
            Some(&bytes),
            policy(JOURNAL_LIMIT, Error::ReceiptConflict),
            || read(&path, JOURNAL_LIMIT),
            Some(&mut || {
                if self.prove(plan, p, step)? != current {
                    return Err(Error::ThirdBytes {
                        artifact: step.artifact,
                    });
                }
                self.private(p, &self.directory, &step.owner)?;
                self.private(p, &path, &step.owner)
            }),
        )?;
        if read(&path, JOURNAL_LIMIT)?.as_deref() != Some(&bytes) {
            return Err(Error::ReceiptConflict);
        }
        *saved = Some(bytes);
        Ok(())
    }
    fn validate_plan(&self, plan: &PublicationPlan) -> Result<()> {
        if !plan.matches_review()
            || plan.generator_version != GENERATOR_VERSION
            || !hash_valid(&plan.review_digest)
            || !steps_valid(&plan.steps)
            || plan.outputs.len() != 4
            || plan.outputs.iter().zip(ARTIFACTS).any(|(o, a)| {
                o.artifact != a || o.bytes.len() > limit(a) || digest(&o.bytes) != o.sha256
            })
            || plan
                .steps
                .iter()
                .any(|s| s.after_digest != plan.output(s.artifact).sha256)
        {
            return Err(Error::InvalidPlan);
        }
        for path in [&plan.root, &self.pointer, &self.startup, &self.user] {
            ancestors(path)?;
        }
        Ok(())
    }
    /// Unfenced native use only. Deadline-bound callers must use publish_authorized.
    pub fn publish(
        &self,
        plan: &PublicationPlan,
        p: &mut impl Platform,
        boundary: &mut impl FnMut(Boundary, Artifact) -> crate::Result<()>,
    ) -> Result<()> {
        self.publish_authorized(plan, p, &mut |_, _| Ok(()), boundary)
    }
    /// Authorization covers configuration effects only. Evidence remains writable
    /// after refusal; all callbacks execute under the existing installation guards.
    pub fn publish_authorized(
        &self,
        plan: &PublicationPlan,
        p: &mut impl Platform,
        authorize: &mut impl FnMut(AuthorizationPoint, Artifact) -> crate::Result<()>,
        boundary: &mut impl FnMut(Boundary, Artifact) -> crate::Result<()>,
    ) -> Result<()> {
        self.validate_plan(plan)?;
        let _launcher = p.launcher_guard()?;
        let _monitor = p.monitor_guard()?;
        // Reject an unowned later step before even the first receipt/directory write.
        for step in &plan.steps {
            self.prove(plan, p, step)?;
        }
        let (mut journal, mut saved) = match self.journals(p, plan)? {
            Some((j, b)) => (j, Some(b)),
            None => (
                Journal {
                    version: 1,
                    review_digest: plan.review_digest.clone(),
                    generator_version: plan.generator_version,
                    steps: plan.steps.clone(),
                    receipts: vec![],
                },
                None,
            ),
        };
        for (index, step) in plan.steps.iter().enumerate() {
            let a = step.artifact;
            let current = self.prove(plan, p, step)?;
            let hash = current.as_ref().map(|b| digest(b));
            if hash.as_deref() == Some(&step.after_digest) {
                if a == Artifact::Installation {
                    self.kit(plan)?;
                }
                self.record(plan, p, &mut journal, &mut saved, (index, State::Published))?;
                continue;
            }
            if hash != step.before_digest {
                return Err(Error::ThirdBytes { artifact: a });
            }
            if a == Artifact::Installation {
                self.kit(plan)?;
            }
            boundary(Boundary::BeforeIntended, a)?;
            self.record(plan, p, &mut journal, &mut saved, (index, State::Intended))?;
            boundary(Boundary::AfterIntended, a)?;
            if a == Artifact::Installation {
                boundary(Boundary::BeforePointer, a)?;
            }
            let path = self.path(plan, a);
            if self.prove(plan, p, step)? != current {
                return Err(Error::ThirdBytes { artifact: a });
            }
            authorize(AuthorizationPoint::BeforeDirectory, a)
                .map_err(|code| Error::Authorization { code })?;
            std::fs::create_dir_all(path.parent().ok_or(Error::InvalidPlan)?)
                .map_err(|_| Error::Gate("PUBLICATION_IO"))?;
            authorize(AuthorizationPoint::BeforeArtifact, a)
                .map_err(|code| Error::Authorization { code })?;
            compare_replace::replace(
                &path,
                current.as_deref(),
                Some(&plan.output(a).bytes),
                policy(limit(a), Error::ThirdBytes { artifact: a }),
                || read(&path, limit(a)),
                Some(&mut || {
                    if self.prove(plan, p, step)? != current {
                        return Err(Error::ThirdBytes { artifact: a });
                    }
                    if a == Artifact::Installation {
                        self.kit(plan)?;
                    }
                    authorize(AuthorizationPoint::ReplacementGuard, a)
                        .map_err(|code| Error::Authorization { code })
                }),
            )?;
            boundary(Boundary::AfterReplace, a)?;
            if a == Artifact::Installation {
                boundary(Boundary::AfterPointer, a)?;
            }
            if read(&path, limit(a))?
                .as_ref()
                .map(|b| digest(b))
                .as_deref()
                != Some(&step.after_digest)
            {
                return Err(Error::ThirdBytes { artifact: a });
            }
            boundary(Boundary::AfterReadBack, a)?;
            self.record(plan, p, &mut journal, &mut saved, (index, State::Published))?;
        }
        self.kit(plan)?;
        if read(&self.pointer, limit(Artifact::Installation))?
            .as_ref()
            .map(|b| digest(b))
            .as_deref()
            != Some(&plan.output(Artifact::Installation).sha256)
        {
            return Err(Error::ThirdBytes {
                artifact: Artifact::Installation,
            });
        }
        crate::installation::Snapshot::load(&self.pointer)?.verify_current()?;
        Ok(())
    }
}
