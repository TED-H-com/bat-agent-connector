use super::*;
use crate::{connection_setup::ArtifactLocation, migration::Platform as _};

/// Native composition only: paths are discovered by the desktop, never IPC input.
#[doc(hidden)]
pub struct NativeInput {
    pub pointer: PathBuf,
    pub kit_root: PathBuf,
    pub role_record: PathBuf,
    pub startup: StartupBinding,
    pub roaming: PathBuf,
    pub startup_directory: PathBuf,
    pub user: PathBuf,
}
pub(super) fn slot_path(pointer: &Path, kit: &Path, artifact: Artifact) -> PathBuf {
    match artifact.location() {
        ArtifactLocation::KitRelative(path) => kit.join(path),
        ArtifactLocation::InstallationPointer => pointer.into(),
    }
}
impl<S: Security> CreationPlatform<S> {
    /// Bounded native auxiliary read using the same retained no-follow checks.
    #[doc(hidden)]
    pub fn read_private_attestation(security: &S, path: &Path, limit: usize) -> Result<Vec<u8>> {
        security.held()?;
        let root = path.parent().ok_or(UNPROVEN)?;
        let retained = RefCell::default();
        let private = |p: &Path| Ok(p.starts_with(root));
        let bytes = checked_read(security, path, limit, &retained, &private)?.ok_or(UNPROVEN)?;
        if checked_read(security, path, limit, &retained, &private)?.as_ref() != Some(&bytes) {
            return Err(UNPROVEN);
        }
        Ok(bytes)
    }

    /// Construct and prove native context synchronously; no guard is returned.
    #[doc(hidden)]
    pub fn from_native_input(
        input: NativeInput,
        security: S,
        verify: &dyn Fn() -> Result<()>,
        mint: &mut dyn FnMut() -> [String; 2],
    ) -> Result<Self> {
        verify()?;
        let login = security.login()?;
        let _launcher = security.launcher_guard(&|| {
            verify()?;
            if security.login()? != login {
                return Err("OTHER_LOGIN_OWNER");
            }
            Ok(())
        })?;
        let state = input.pointer.parent().ok_or(UNPROVEN)?;
        let root = state.join("fleet-creation");
        let role_parent = input.role_record.parent().ok_or(UNPROVEN)?;
        let private = |path: &Path| {
            Ok(path.starts_with(&root)
                || path.starts_with(&input.kit_root)
                || path.starts_with(role_parent))
        };
        let retained = RefCell::new(BTreeMap::new());
        let identity = |path: &Path| {
            let file = checked_walk(&security, path, &mut retained.borrow_mut(), &private)?
                .ok_or(UNPROVEN)?;
            if !file.metadata().map_err(|_| UNPROVEN)?.is_dir() {
                return Err(UNPROVEN);
            }
            security.identity(&file)
        };
        let scope = Scope {
            platform: "windows".into(),
            pointer: input.pointer.clone(),
            kit_root: input.kit_root.clone(),
            state_root: state.into(),
            state_identity: identity(state)?,
            startup_role_record: input.role_record.clone(),
        };
        let role = checked_read(
            &security,
            &input.role_record,
            RECEIPT_LIMIT,
            &retained,
            &private,
        )?
        .ok_or(UNPROVEN)?;
        if digest(&role) != input.startup.role_record_sha256 {
            return Err(UNPROVEN);
        }
        let provision = root.join(EvidenceKey::Provisioning.relative_path()?);
        let existing = checked_read(&security, &provision, RECEIPT_LIMIT, &retained, &private)?;
        let _new_monitor = if existing.is_none() {
            Some(security.monitor_guard(&|| {
                verify()?;
                if security.login()? != login {
                    return Err("OTHER_LOGIN_OWNER");
                }
                if super::platform::process_observation(
                    &security,
                    false,
                    &input.pointer,
                    &input.roaming,
                )?
                .is_some()
                {
                    return Err("MONITOR_ALREADY_RUNNING");
                }
                Ok(())
            })?)
        } else {
            None
        };
        let receipt = if let Some(bytes) = &existing {
            let Record::Provisioning(receipt) = Record::decode(&EvidenceKey::Provisioning, bytes)?
            else {
                return Err(UNPROVEN);
            };
            if receipt.scope != scope
                || receipt.startup != input.startup
                || receipt.creator.owner_sid != login.owner_sid
            {
                return Err(UNPROVEN);
            }
            *receipt
        } else {
            // Existing unrecorded namespace residue cannot allocate replacement IDs.
            if checked_walk(&security, &root, &mut retained.borrow_mut(), &private)?.is_some() {
                return Err(UNPROVEN);
            }
            let mut slots = Vec::new();
            for artifact in ARTIFACTS {
                let path = slot_path(&input.pointer, &input.kit_root, artifact);
                if checked_walk(&security, &path, &mut retained.borrow_mut(), &private)?.is_some() {
                    return Err(UNPROVEN);
                }
                let mut parent = path.parent().ok_or(UNPROVEN)?;
                while checked_walk(&security, parent, &mut retained.borrow_mut(), &private)?
                    .is_none()
                {
                    parent = parent.parent().ok_or(UNPROVEN)?;
                }
                slots.push(SlotReservation {
                    artifact,
                    canonical_path: path.clone(),
                    observed: "absent".into(),
                    before_sha256: None,
                    nearest_existing_parent: parent.into(),
                    parent_identity: identity(parent)?,
                    missing_components: path
                        .strip_prefix(parent)
                        .map_err(|_| UNPROVEN)?
                        .iter()
                        .map(|c| c.to_str().map(str::to_owned).ok_or(UNPROVEN))
                        .collect::<Result<_>>()?,
                });
            }
            let managed_connector = security.managed_identity()?;
            if input.startup.role != StartupRole::Client && managed_connector.is_none() {
                return Err(UNSUPPORTED);
            }
            let [fleet_creation_id, reservation_id] = mint();
            let mut receipt = ProvisioningReceipt {
                startup: input.startup,
                creator: Creator {
                    owner_sid: login.owner_sid.clone(),
                    session_id: login.session_id,
                    reservation_id,
                },
                fleet_creation_id,
                scope,
                slots: slots.try_into().map_err(|_| UNPROVEN)?,
                directories: vec![],
                managed_connector,
            };
            receipt.directories = receipt
                .allowed_directories()
                .into_iter()
                .map(|canonical_path| DirectoryReservation::ReservedAbsent { canonical_path })
                .collect();
            receipt
        };
        let mut platform = Self::with_security(
            receipt,
            input.roaming,
            &input.startup_directory,
            &input.user,
            security,
        )?;
        *platform.reader.directories.borrow_mut() = retained.into_inner();
        let _monitor = if _new_monitor.is_none() {
            Some(platform.monitor_guard()?)
        } else {
            None
        };
        verify()?;
        if let Some(bytes) = existing {
            let owner = platform.reader.expected.owner(&bytes)?;
            evidence::capture(&platform.reader, &owner)?.resolve(&platform.histories()?, &[])?;
        }
        platform.reader.base()?;
        Ok(platform)
    }
}
