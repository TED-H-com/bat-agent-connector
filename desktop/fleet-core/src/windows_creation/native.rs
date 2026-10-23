use super::*;
use crate::{
    connection_publish::{self as publication, FrozenHistory, Publisher},
    discovery::{self, Backend, DiscoveryConfig, MonitorIdentity, NativeIdentity},
    installation::{canonical_local, local_path},
    migration::{self, Owner},
    publication_state,
    windows::{current_login, MonitorMutex, WindowsMonitorObservation},
    windows_launcher::LauncherMutex,
    windows_publication_guards::WindowsPublicationGuards,
};
use std::{os::windows::io::AsRawHandle, rc::Rc};
use windows_sys::Win32::Storage::FileSystem::{
    FileIdInfo, GetFileInformationByHandleEx, FILE_ID_INFO,
};

struct WindowsSecurity {
    guards: WindowsPublicationGuards,
}
impl Security for WindowsSecurity {
    fn validate_path(&self, path: &Path) -> Result<()> {
        use std::path::{Component, Prefix};
        if !path.is_absolute()
            || !local_path(path)
            || !matches!(path.components().next(),
            Some(Component::Prefix(p)) if matches!(p.kind(), Prefix::Disk(_)))
        {
            return Err(UNPROVEN);
        }
        for c in path.components() {
            if let Component::Normal(name) = c {
                let name = name.to_str().ok_or(UNPROVEN)?;
                let base = name.split('.').next().unwrap_or("").to_ascii_uppercase();
                if name.contains([':', '\0'])
                    || name.ends_with(['.', ' '])
                    || ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"].contains(&base.as_str())
                    || ["COM", "LPT"].iter().any(|p| {
                        base.strip_prefix(p).is_some_and(|n| {
                            ["1", "2", "3", "4", "5", "6", "7", "8", "9", "¹", "²", "³"]
                                .contains(&n)
                        })
                    })
                {
                    return Err(UNPROVEN);
                }
            }
        }
        Ok(())
    }
    fn login(&self) -> Result<LoginIdentity> {
        current_login()
    }
    fn held(&self) -> Result<()> {
        self.guards.launcher().map(|_| ())
    }
    fn open(&self, path: &Path, private: bool, child_absent: bool) -> Result<File> {
        let file = publication_state::checked_handle(path, private, child_absent)?;
        if canonical_local(path)? != path {
            return Err(UNPROVEN);
        }
        Ok(file)
    }
    fn identity(&self, file: &File) -> Result<ObjectIdentity> {
        file_identity(file)
    }
}
pub(super) fn file_identity(file: &File) -> Result<ObjectIdentity> {
    let mut info = FILE_ID_INFO::default();
    if unsafe {
        GetFileInformationByHandleEx(
            file.as_raw_handle(),
            FileIdInfo,
            (&mut info as *mut FILE_ID_INFO).cast(),
            std::mem::size_of::<FILE_ID_INFO>() as u32,
        )
    } == 0
    {
        return Err(UNPROVEN);
    }
    Ok(ObjectIdentity {
        volume: info.VolumeSerialNumber,
        file_id: info
            .FileId
            .Identifier
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect(),
    })
}

/// Construct, use and drop inside one synchronous native blocking call. All
/// context paths and startup bindings come from trusted native discovery, never
/// caller ownership claims. Token SID/session are always read from current_login.
/// This observation core cannot reserve slots or prepare artifact directories.
pub struct WindowsCreationPlatform {
    reader: Reader<WindowsSecurity>,
    roaming: PathBuf,
    discovery: DiscoveryConfig,
    publisher: Publisher,
}
impl WindowsCreationPlatform {
    pub fn new(
        expected: ProvisioningReceipt,
        roaming: PathBuf,
        executable: PathBuf,
        startup: &Path,
        user: &Path,
    ) -> Result<Self> {
        let login = current_login()?;
        let security = WindowsSecurity {
            guards: WindowsPublicationGuards::new(login),
        };
        let roaming = canonical_local(&roaming)?;
        let executable = canonical_local(&executable)?;
        if !roaming.is_dir() || !executable.is_file() {
            return Err(UNPROVEN);
        }
        let text = |p: &Path| p.to_str().map(str::to_owned).ok_or(UNPROVEN);
        let native = NativeIdentity::new(&text(&executable)?, &text(&expected.scope.pointer)?)?;
        let discovery = DiscoveryConfig::new(
            &text(&expected.scope.kit_root.join("client"))?,
            &text(expected.path(Artifact::Inventory))?,
            &text(expected.path(Artifact::ProfileIndex))?,
            &roaming,
            Some(native),
        )?;
        let publisher =
            Publisher::new(&expected.scope.pointer, startup, user).map_err(|_| UNPROVEN)?;
        Ok(Self {
            reader: Reader::new(expected, security)?,
            roaming,
            discovery,
            publisher,
        })
    }
    fn observation(&self) -> Result<Option<MonitorIdentity>> {
        self.reader.security.held()?;
        self.reader.base()?;
        let pointer = &self.reader.expected.scope.pointer;
        let owner = if !present(pointer)? {
            crate::unconfigured::verify_absence(
                pointer,
                &self.roaming,
                &WindowsMonitorObservation,
            )?;
            None
        } else {
            // Raw recovery inspection needs intact authority scope, not a
            // parseable pointer or an obsolete whole-Configuration snapshot.
            self.reader.verify_context(&self.reader.expected)?;
            WindowsPublicationGuards::observed(
                discovery::discover(&self.discovery, &WindowsMonitorObservation)?,
                &self.roaming.join("bat-fleet-monitor-launch.json"),
                &WindowsMonitorObservation,
            )?
        };
        self.reader.base()?;
        Ok(owner)
    }
    fn histories(&self) -> Result<Vec<publication::PublicationHistory>> {
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
impl migration::Platform for WindowsCreationPlatform {
    type LauncherGuard = Rc<LauncherMutex>;
    type MonitorGuard = Rc<MonitorMutex>;
    fn launcher_guard(&mut self) -> Result<Self::LauncherGuard> {
        self.reader
            .security
            .guards
            .launcher_guard(|| self.reader.base())
    }
    fn monitor_guard(&mut self) -> Result<Self::MonitorGuard> {
        self.reader.security.guards.monitor_guard(|| {
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
impl publication::Platform for WindowsCreationPlatform {
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

#[cfg(test)]
mod tests {
    use super::*;
    struct OwnedPath(PathBuf);
    impl Drop for OwnedPath {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    fn fixture() -> (OwnedPath, WindowsSecurity) {
        let path = canonical_local(&std::env::temp_dir())
            .unwrap()
            .join(format!("wca-acl-{:032x}", rand::random::<u128>()));
        publication_state::create_new_directory(&path).unwrap();
        (
            OwnedPath(path),
            WindowsSecurity {
                guards: WindowsPublicationGuards::new(current_login().unwrap()),
            },
        )
    }
    #[test]
    fn windows_creation_protected_objects_use_current_token_and_create_new() {
        use std::io::Write;
        let (root, security) = fixture();
        let path = root.0.join("receipt.json");
        let mut file = publication_state::create_new_file(&path).unwrap();
        file.write_all(b"test-owned bytes").unwrap();
        file.sync_all().unwrap();
        drop(file);
        let first = security.open(&path, true, false).unwrap();
        assert_eq!(security.login().unwrap(), current_login().unwrap());
        assert_eq!(
            file_identity(&first).unwrap(),
            file_identity(&security.open(&path, true, false).unwrap()).unwrap()
        );
        assert!(publication_state::create_new_directory(&root.0).is_err());
        assert!(publication_state::create_new_file(&path).is_err());
        assert_eq!(std::fs::read(&path).unwrap(), b"test-owned bytes");
    }
    #[test]
    fn windows_creation_unprotected_object_is_refused_without_repair() {
        use std::{os::windows::ffi::OsStrExt, ptr};
        use windows_sys::Win32::{
            Foundation::LocalFree,
            Security::{
                Authorization::{GetSecurityInfo, SetNamedSecurityInfoW, SE_FILE_OBJECT},
                ACL, DACL_SECURITY_INFORMATION, UNPROTECTED_DACL_SECURITY_INFORMATION,
            },
        };
        let (root, security) = fixture();
        let handle = security.open(&root.0, true, false).unwrap();
        let (mut dacl, mut descriptor): (*mut ACL, _) = (ptr::null_mut(), ptr::null_mut());
        assert_eq!(
            unsafe {
                GetSecurityInfo(
                    handle.as_raw_handle(),
                    SE_FILE_OBJECT,
                    DACL_SECURITY_INFORMATION,
                    ptr::null_mut(),
                    ptr::null_mut(),
                    &mut dacl,
                    ptr::null_mut(),
                    &mut descriptor,
                )
            },
            0
        );
        assert!(!dacl.is_null());
        let name: Vec<u16> = root.0.as_os_str().encode_wide().chain(Some(0)).collect();
        let status = unsafe {
            SetNamedSecurityInfoW(
                name.as_ptr(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION | UNPROTECTED_DACL_SECURITY_INFORMATION,
                ptr::null_mut(),
                ptr::null_mut(),
                dacl,
                ptr::null_mut(),
            )
        };
        unsafe {
            LocalFree(descriptor);
        }
        assert_eq!(
            status, 0,
            "required unprotected ACL fixture must be constructible"
        );
        drop(handle);
        assert!(security.open(&root.0, true, false).is_err());
        assert!(publication_state::create_new_directory(&root.0).is_err());
    }
}
