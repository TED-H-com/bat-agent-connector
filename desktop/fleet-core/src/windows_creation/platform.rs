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
    pub(super) migrations: migration::Store,
    // Set only by explicit guarded preparation, never by a read or load.
    pub(super) directories_prepared: bool,
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
        let migrations = migration::Store::new(&expected.scope.pointer, startup)?;
        Ok(Self {
            reader: Reader::new(expected, security)?,
            roaming,
            publisher,
            migrations,
            directories_prepared: false,
        })
    }
    pub(super) fn observation(&self) -> Result<Option<MonitorIdentity>> {
        self.reader.security.held()?;
        self.reader.base()?;
        let configured = present(&self.reader.expected.scope.pointer)?;
        if configured {
            // Raw recovery inspection needs intact authority scope, not a
            // parseable pointer or an obsolete whole-Configuration snapshot.
            self.reader.verify_context(&self.reader.expected)?;
        }
        let owner = self.process_observation(configured)?;
        self.reader.base()?;
        Ok(owner)
    }
    pub(super) fn process_observation(&self, configured: bool) -> Result<Option<MonitorIdentity>> {
        Ok(if !configured {
            self.reader
                .security
                .unconfigured(&self.reader.expected.scope.pointer, &self.roaming)?;
            None
        } else {
            let owner = self.reader.security.discover()?;
            if owner.is_none() {
                self.reader
                    .security
                    .unpublished(&self.roaming.join("bat-fleet-monitor-launch.json"))?;
            }
            owner
        })
    }
    pub(super) fn history(
        &self,
        reference: &publication::FrozenPlanRef,
    ) -> Result<publication::PublicationHistory> {
        let path = self
            .reader
            .expected
            .scope
            .state_root
            .join("fleet-publication-plans")
            .join(format!("{}.json", reference.review_digest));
        publication::Platform::verify_private_state(
            self,
            &path,
            &self.reader.expected.owner(&self.reader.root_bytes()?)?,
        )?;
        let bytes = self
            .reader
            .read(&path, publication::FROZEN_PLAN_LIMIT)?
            .ok_or(UNPROVEN)?;
        let frozen = FrozenHistory::decode(reference, &bytes).map_err(|_| UNPROVEN)?;
        self.publisher
            .read_evidence_history(frozen, self)
            .map_err(|_| UNPROVEN)
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
                histories.push(self.history(&link.frozen)?);
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
        let root = if self.reader.directory_reserved(path)? && present(path)? {
            path
        } else {
            let parent = path.parent().ok_or(UNPROVEN)?;
            if !self.reader.directory_reserved(parent)? {
                return Err(UNPROVEN);
            }
            parent
        };
        if !root.starts_with(&self.reader.expected.scope.state_root) {
            return Err(UNPROVEN);
        }
        self.reader
            .directory(root, &digest(&self.reader.root_bytes()?))?;
        self.reader.walk(path)?;
        Ok(())
    }
    fn prepare_private_directory(&mut self, path: &Path, owner: &OwnerEvidence) -> Result<()> {
        // Explicit writer preparation must already have recorded this directory.
        // The publisher must not adopt an existing name or create read-side residue.
        self.verify_private_state(path, owner)?;
        if !self
            .reader
            .walk(path)?
            .ok_or(UNPROVEN)?
            .metadata()
            .map_err(|_| UNPROVEN)?
            .is_dir()
        {
            return Err(UNPROVEN);
        }
        Ok(())
    }
    fn prepare_directory(
        &mut self,
        path: &Path,
        owner: &OwnerEvidence,
        authorize: &mut dyn FnMut() -> Result<()>,
    ) -> std::result::Result<(), publication::Error> {
        if self.directories_prepared {
            if self.reader.expected.owner(&self.reader.root_bytes()?)? != *owner {
                return Err(UNPROVEN.into());
            }
            let mut denied = None;
            let result = super::writer::CreationWriter::held(self)?
                .prepare_parents(path, &mut || {
                    authorize().inspect_err(|code| denied = Some(*code))
                });
            return match denied {
                Some(code) => Err(publication::Error::Authorization { code }),
                None => result.map_err(Into::into),
            };
        }
        authorize().map_err(|code| publication::Error::Authorization { code })?;
        self.reader.base()?;
        Err(publication::Error::Gate("CREATION_DIRECTORY_UNAVAILABLE"))
    }
}
