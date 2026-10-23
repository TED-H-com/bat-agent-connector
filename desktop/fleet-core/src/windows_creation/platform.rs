//! Portable platform policy; only OS observations and guard mechanics vary.
use super::*;
use crate::{
    connection_publish::{self as publication, FrozenHistory, Publisher},
    discovery::{Backend, MonitorIdentity},
    migration::{self, Owner},
};
pub struct CreationPlatform<S> {
    pub(super) reader: Reader<S>,
    pub(super) roaming: PathBuf,
    pub(super) publisher: Publisher,
}
impl<S: Security> CreationPlatform<S> {
    pub(super) fn with_security(
        expected: ProvisioningReceipt,
        roaming: PathBuf,
        startup: &Path,
        user: &Path,
        security: S,
    ) -> Result<Self> {
        let publisher =
            Publisher::new(&expected.scope.pointer, startup, user).map_err(|_| UNPROVEN)?;
        Ok(Self {
            reader: Reader::new(expected, security)?,
            roaming,
            publisher,
        })
    }
    pub(super) fn observation(&self) -> Result<Option<MonitorIdentity>> {
        self.reader.security.held()?;
        self.reader.base()?;
        let pointer = &self.reader.expected.scope.pointer;
        let owner = if !present(pointer)? {
            self.reader.security.unconfigured(pointer, &self.roaming)?;
            None
        } else {
            // Raw recovery inspection needs intact authority scope, not a
            // parseable pointer or an obsolete whole-Configuration snapshot.
            self.reader.verify_context(&self.reader.expected)?;
            let owner = self.reader.security.discover()?;
            if owner.is_none() {
                self.reader
                    .security
                    .unpublished(&self.roaming.join("bat-fleet-monitor-launch.json"))?;
            }
            owner
        };
        self.reader.base()?;
        Ok(owner)
    }
    pub(super) fn histories(&self) -> Result<Vec<publication::PublicationHistory>> {
        let mut histories = Vec::new();
        for key in self.reader.entries()? {
            let bound = if key == EvidenceKey::Provisioning {
                RECEIPT_LIMIT
            } else {
                RECORD_LIMIT
            };
            let record = Record::decode(
                &key,
                &self.reader.read_private(&key, bound)?.ok_or(UNPROVEN)?,
            )?;
            if let Record::Publication(link) = record {
                if histories.len() >= HISTORY_LIMIT {
                    return Err(UNPROVEN);
                }
                let path = self
                    .reader
                    .expected
                    .scope
                    .state_root
                    .join("fleet-publication-plans")
                    .join(format!("{}.json", link.frozen.review_digest));
                publication::Platform::verify_private_state(
                    self,
                    &path,
                    &self.reader.expected.owner(&self.reader.root_bytes()?)?,
                )?;
                let bytes = self
                    .reader
                    .read(&path, publication::FROZEN_PLAN_LIMIT)?
                    .ok_or(UNPROVEN)?;
                let frozen = FrozenHistory::decode(&link.frozen, &bytes).map_err(|_| UNPROVEN)?;
                histories.push(
                    self.publisher
                        .read_evidence_history(frozen, self)
                        .map_err(|_| UNPROVEN)?,
                );
            }
        }
        Ok(histories)
    }
}
impl<S: Security> migration::Platform for CreationPlatform<S> {
    type LauncherGuard = S::LauncherGuard;
    type MonitorGuard = S::MonitorGuard;
    fn launcher_guard(&mut self) -> Result<Self::LauncherGuard> {
        self.reader.security.launcher_guard(&|| self.reader.base())
    }
    fn monitor_guard(&mut self) -> Result<Self::MonitorGuard> {
        self.reader.security.monitor_guard(&|| {
            if self.observation()?.is_some() {
                return Err("MONITOR_ALREADY_RUNNING");
            }
            Ok(())
        })
    }
    fn login(&self) -> Result<LoginIdentity> {
        self.reader.login()
    }
    fn discover(&mut self) -> Result<Option<MonitorIdentity>> {
        self.observation()
    }
    fn request_quit(&mut self, _: &Owner) -> Result<()> {
        Err(UNSUPPORTED)
    }
    fn launch(&mut self, _: Backend) -> Result<()> {
        Err(UNSUPPORTED)
    }
    fn validate_config(&self, _: &[u8], _: Backend) -> Result<()> {
        Err(UNSUPPORTED)
    }
    fn classify_shortcut(&self, _: &[u8]) -> Result<Backend> {
        Err(UNSUPPORTED)
    }
    fn shortcut(&self, _: Backend) -> Result<Vec<u8>> {
        Err(UNSUPPORTED)
    }
}
impl<S: Security> publication::Platform for CreationPlatform<S> {
    fn ownership(
        &self,
        owner: &OwnerEvidence,
        artifact: Artifact,
        path: &Path,
        current: Option<&[u8]>,
    ) -> Result<FileOwnership> {
        self.reader.security.held()?;
        if !present(&self.reader.key_path(&EvidenceKey::Provisioning)?)? {
            return Ok(FileOwnership::Unknown);
        }
        let captured = evidence::capture(&self.reader, owner)?;
        let histories = self.histories()?;
        let fresh = evidence::capture(&self.reader, owner)?;
        let signature = |h: &publication::PublicationHistory| {
            (
                h.frozen().reference().clone(),
                h.journal_sha256().map(str::to_owned),
                h.decision().map(|(hash, _)| hash.to_owned()),
            )
        };
        if histories.iter().map(signature).collect::<Vec<_>>()
            != self.histories()?.iter().map(signature).collect::<Vec<_>>()
            || captured.receipt_sha256() != fresh.receipt_sha256()
        {
            return Err(UNPROVEN);
        }
        let resolved = fresh.resolve(&histories, &[])?;
        self.reader.base()?;
        Ok(resolved.ownership(owner, artifact, path, current))
    }
    fn verify_private_state(&self, path: &Path, owner: &OwnerEvidence) -> Result<()> {
        self.reader.verify_context(&self.reader.expected)?;
        if self.reader.expected.owner(&self.reader.root_bytes()?)? != *owner {
            return Err(UNPROVEN);
        }
        // Until guarded directory creation lands, only recorded existing roots
        // and their direct, validated history files are available for observation.
        let root = self
            .reader
            .expected
            .directories
            .iter()
            .find(|d| path == d.path() || path.parent() == Some(d.path()))
            .ok_or(UNPROVEN)?;
        if !root
            .path()
            .starts_with(&self.reader.expected.scope.state_root)
        {
            return Err(UNPROVEN);
        }
        self.reader
            .directory(root.path(), &digest(&self.reader.root_bytes()?))?;
        self.reader.walk(path)?;
        Ok(())
    }
    fn prepare_directory(
        &mut self,
        _: &Path,
        _: &OwnerEvidence,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> std::result::Result<(), publication::Error> {
        authorize().map_err(|code| publication::Error::Authorization { code })?;
        self.reader.base()?;
        Err(publication::Error::Gate("CREATION_DIRECTORY_UNAVAILABLE"))
    }
}
