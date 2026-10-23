//! Private receipt storage. Windows creation follows the native startup-role
//! protected, inheritable owner/SYSTEM DACL pattern; existing ACLs are verified,
//! never repaired. Native creation authority/ancestor checks remain mandatory.
use crate::Result;
use std::path::Path;
const UNPROVEN: &str = "PUBLICATION_STATE_UNPROVEN";

pub(crate) fn verify(path: &Path) -> Result<()> {
    let metadata = match path.symlink_metadata() {
        Ok(metadata) => metadata,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(_) => return Err(UNPROVEN),
    };
    if metadata.file_type().is_symlink() || !(metadata.is_dir() || metadata.is_file()) {
        return Err(UNPROVEN);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.uid() != unsafe { libc::geteuid() } || metadata.mode() & 0o077 != 0 {
            return Err(UNPROVEN);
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(UNPROVEN);
        }
        windows::verify_acl(path)?;
    }
    Ok(())
}
pub(crate) fn create_directory(path: &Path) -> Result<()> {
    verify(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        match std::fs::DirBuilder::new().mode(0o700).create(path) {
            Ok(()) => {
                std::fs::File::open(path.parent().ok_or(UNPROVEN)?)
                    .and_then(|f| f.sync_all())
                    .map_err(|_| UNPROVEN)?;
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err(UNPROVEN),
        }
    }
    #[cfg(windows)]
    windows::create(path)?;
    verify(path)
}

#[cfg(windows)]
mod windows {
    mod protected;
    use super::*;
    pub(super) use protected::{acl_file, new_directory, new_file, open_no_follow};
    use std::{os::windows::ffi::OsStrExt, ptr};
    use windows_sys::Win32::{
        Foundation::{GetLastError, LocalFree, ERROR_ALREADY_EXISTS},
        Security::{
            Authorization::{
                ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
                GetNamedSecurityInfoW, SE_FILE_OBJECT,
            },
            GetAce, IsValidSid, ACCESS_ALLOWED_ACE, ACL, DACL_SECURITY_INFORMATION,
            OWNER_SECURITY_INFORMATION, PSID, SECURITY_ATTRIBUTES,
        },
        Storage::FileSystem::CreateDirectoryW,
    };
    struct Allocation(*mut core::ffi::c_void);
    impl Drop for Allocation {
        fn drop(&mut self) {
            unsafe {
                LocalFree(self.0);
            }
        }
    }
    fn wide(path: &Path) -> Result<Vec<u16>> {
        let mut name: Vec<_> = path.as_os_str().encode_wide().collect();
        if name.contains(&0) || name.len() >= 32767 {
            return Err(UNPROVEN);
        }
        name.push(0);
        Ok(name)
    }
    fn sid_text(sid: PSID) -> Result<String> {
        let mut string = ptr::null_mut();
        if unsafe { IsValidSid(sid) } == 0
            || unsafe { ConvertSidToStringSidW(sid, &mut string) } == 0
        {
            return Err(UNPROVEN);
        }
        let _allocation = Allocation(string.cast());
        let mut length = 0;
        while length < 184 && unsafe { *string.add(length) } != 0 {
            length += 1;
        }
        if length == 184 {
            return Err(UNPROVEN);
        }
        String::from_utf16(unsafe { std::slice::from_raw_parts(string, length) })
            .map_err(|_| UNPROVEN)
    }
    pub(super) fn verify_acl(path: &Path) -> Result<()> {
        let name = wide(path)?;
        let mut owner = ptr::null_mut();
        let mut acl: *mut ACL = ptr::null_mut();
        let mut descriptor = ptr::null_mut();
        if unsafe {
            GetNamedSecurityInfoW(
                name.as_ptr(),
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                &mut owner,
                ptr::null_mut(),
                &mut acl,
                ptr::null_mut(),
                &mut descriptor,
            )
        } != 0
        {
            return Err(UNPROVEN);
        }
        let _allocation = Allocation(descriptor);
        let current = crate::windows::current_login()?.owner_sid;
        if acl.is_null() || sid_text(owner)? != current {
            return Err(UNPROVEN);
        }
        let count = unsafe { (*acl).AceCount };
        if count == 0 || count > 64 {
            return Err(UNPROVEN);
        }
        for index in 0..count {
            let mut ace = ptr::null_mut();
            if unsafe { GetAce(acl, u32::from(index), &mut ace) } == 0 || ace.is_null() {
                return Err(UNPROVEN);
            }
            let ace = ace.cast::<ACCESS_ALLOWED_ACE>();
            // Only ordinary allow ACEs for this owner, OWNER RIGHTS or SYSTEM.
            // Reject unknown/object/callback ACE forms instead of guessing access.
            if unsafe { (*ace).Header.AceType } != 0
                || usize::from(unsafe { (*ace).Header.AceSize })
                    < std::mem::size_of::<ACCESS_ALLOWED_ACE>()
            {
                return Err(UNPROVEN);
            }
            let sid = sid_text(unsafe { ptr::addr_of_mut!((*ace).SidStart).cast() })?;
            if sid != current && sid != "S-1-3-4" && sid != "S-1-5-18" {
                return Err(UNPROVEN);
            }
        }
        Ok(())
    }
    fn security<T>(operation: impl FnOnce(&SECURITY_ATTRIBUTES) -> Result<T>) -> Result<T> {
        let sddl: Vec<u16> = format!(
            "O:{}D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)",
            crate::windows::current_login()?.owner_sid
        )
        .encode_utf16()
        .chain(Some(0))
        .collect();
        let mut descriptor = ptr::null_mut();
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl.as_ptr(),
                1,
                &mut descriptor,
                ptr::null_mut(),
            )
        } == 0
        {
            return Err(UNPROVEN);
        }
        let _allocation = Allocation(descriptor);
        let attrs = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor,
            bInheritHandle: 0,
        };
        operation(&attrs)
    }
    pub(super) fn create(path: &Path) -> Result<()> {
        security(|attrs| {
            let name = wide(path)?;
            let created = unsafe { CreateDirectoryW(name.as_ptr(), attrs) };
            let error = unsafe { GetLastError() };
            if created == 0 && error != ERROR_ALREADY_EXISTS {
                return Err(UNPROVEN);
            }
            Ok(())
        })
    }
    pub(super) fn create_file(path: &Path) -> Result<std::fs::File> {
        use std::os::windows::io::FromRawHandle;
        use windows_sys::Win32::{
            Foundation::{GENERIC_WRITE, INVALID_HANDLE_VALUE},
            Storage::FileSystem::{CreateFileW, CREATE_NEW, FILE_ATTRIBUTE_NORMAL},
        };
        security(|attrs| {
            let name = wide(path)?;
            let handle = unsafe {
                CreateFileW(
                    name.as_ptr(),
                    GENERIC_WRITE,
                    0,
                    attrs,
                    CREATE_NEW,
                    FILE_ATTRIBUTE_NORMAL,
                    ptr::null_mut(),
                )
            };
            if handle == INVALID_HANDLE_VALUE {
                return Err(UNPROVEN);
            }
            Ok(unsafe { std::fs::File::from_raw_handle(handle) })
        })
    }
}

#[cfg(windows)]
pub(crate) fn create_file(path: &Path) -> Result<std::fs::File> {
    windows::create_file(path)
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    #[test]
    fn windows_owned_receipts_have_private_dacl_and_create_new_semantics() {
        let root =
            std::env::temp_dir().join(format!("fleet-owned-state-{:032x}", rand::random::<u128>()));
        create_directory(&root).unwrap();
        let path = root.join("receipt.json");
        drop(create_file(&path).unwrap());
        verify(&root).unwrap();
        verify(&path).unwrap();
        assert!(create_file(&path).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn windows_receipt_directory_with_public_acl_is_not_adopted_or_repaired() {
        use std::{os::windows::ffi::OsStrExt, ptr};
        use windows_sys::Win32::{
            Foundation::LocalFree,
            Security::{
                Authorization::ConvertStringSecurityDescriptorToSecurityDescriptorW,
                SECURITY_ATTRIBUTES,
            },
            Storage::FileSystem::CreateDirectoryW,
        };
        let root = std::env::temp_dir().join(format!(
            "fleet-public-state-{:032x}",
            rand::random::<u128>()
        ));
        let name: Vec<u16> = root.as_os_str().encode_wide().chain(Some(0)).collect();
        let sddl: Vec<u16> = "D:P(A;OICI;FA;;;WD)"
            .encode_utf16()
            .chain(Some(0))
            .collect();
        let mut descriptor = ptr::null_mut();
        assert_ne!(
            unsafe {
                ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    sddl.as_ptr(),
                    1,
                    &mut descriptor,
                    ptr::null_mut(),
                )
            },
            0
        );
        let attrs = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor,
            bInheritHandle: 0,
        };
        assert_ne!(unsafe { CreateDirectoryW(name.as_ptr(), &attrs) }, 0);
        unsafe {
            LocalFree(descriptor);
        }
        assert_eq!(verify(&root), Err(UNPROVEN));
        assert_eq!(create_directory(&root), Err(UNPROVEN));
        std::fs::remove_dir_all(root).unwrap();
    }
}

#[cfg(any(windows, test))]
mod acl;

/// Verify an existing private object, including SE_DACL_PROTECTED on Windows.
/// This is an access-control property, never a creation/ownership decision.
pub fn verify_protected(path: &Path) -> Result<()> {
    no_links(path)?;
    #[cfg(windows)]
    let file = windows::open_no_follow(path, false).map_err(|_| UNPROVEN)?;
    #[cfg(unix)]
    let file = {
        use std::os::unix::fs::OpenOptionsExt;
        std::fs::OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
            .open(path)
            .map_err(|_| UNPROVEN)?
    };
    private_handle(&file)
}
fn private_handle(file: &std::fs::File) -> Result<()> {
    let metadata = file.metadata().map_err(|_| UNPROVEN)?;
    if !(metadata.is_file() || metadata.is_dir()) {
        return Err(UNPROVEN);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.uid() != unsafe { libc::geteuid() } || metadata.mode() & 0o077 != 0 {
            return Err(UNPROVEN);
        }
    }
    #[cfg(windows)]
    windows::acl_file(file, true, false)?;
    Ok(())
}
fn no_links(path: &Path) -> Result<()> {
    use std::path::Component;
    if !path.is_absolute()
        || path
            .components()
            .any(|c| matches!(c, Component::ParentDir | Component::CurDir))
        || path
            .to_str()
            .is_none_or(|s| s.contains('\0') || s.split(['/', '\\']).any(|c| c == "." || c == ".."))
    {
        return Err(UNPROVEN);
    }
    for p in path.ancestors() {
        match p.symlink_metadata() {
            Ok(m) => {
                if m.file_type().is_symlink() || (p != path && !m.is_dir()) {
                    return Err(UNPROVEN);
                }
                #[cfg(windows)]
                {
                    use std::os::windows::fs::MetadataExt;
                    if m.file_attributes() & 0x400 != 0 {
                        return Err(UNPROVEN);
                    }
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
            Err(_) => return Err(UNPROVEN),
        }
    }
    Ok(())
}
/// Check one existing environmental directory's replacement/security rights.
/// `child_absent` also forbids untrusted child-creation grants. Caller rechecks
/// object identity while holding its native guards; readable is not owned.
pub fn verify_ancestor(path: &Path, child_absent: bool) -> Result<()> {
    no_links(path)?;
    #[cfg(windows)]
    {
        let file = windows::open_no_follow(path, false).map_err(|_| UNPROVEN)?;
        if !file.metadata().map_err(|_| UNPROVEN)?.is_dir() {
            return Err(UNPROVEN);
        }
        windows::acl_file(&file, false, child_absent)
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = path.symlink_metadata().map_err(|_| UNPROVEN)?;
        let _ = child_absent;
        if !metadata.is_dir()
            || (metadata.uid() != 0 && metadata.uid() != unsafe { libc::geteuid() })
            || metadata.mode() & 0o022 != 0
        {
            return Err(UNPROVEN);
        }
        Ok(())
    }
}
/// Walk every ancestor through the filesystem root. Existing directories within
/// `protected_root` require private protection; outside it use environmental ACL
/// policy. Missing components are permitted only below an existing protected
/// root. This grants no right to create any of them.
pub fn verify_ancestors(path: &Path, protected_root: &Path) -> Result<()> {
    no_links(path)?;
    no_links(protected_root)?;
    if !path.starts_with(protected_root) || !protected_root.is_dir() {
        return Err(UNPROVEN);
    }
    verify_protected(protected_root)?;
    let mut child_absent = match path.symlink_metadata() {
        Ok(_) => false,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => true,
        Err(_) => return Err(UNPROVEN),
    };
    for parent in path.ancestors().skip(1) {
        match parent.symlink_metadata() {
            Ok(m) => {
                if !m.is_dir() {
                    return Err(UNPROVEN);
                }
                if parent.starts_with(protected_root) {
                    verify_protected(parent)?;
                } else {
                    verify_ancestor(parent, child_absent)?;
                }
                child_absent = false;
            }
            Err(e)
                if e.kind() == std::io::ErrorKind::NotFound
                    && parent.starts_with(protected_root) =>
            {
                child_absent = true
            }
            Err(_) => return Err(UNPROVEN),
        }
    }
    Ok(())
}
/// Strict create-new with explicit private permissions. An existing object is
/// always a conflict; no adoption or ACL repair. Parent authorization is separate.
pub fn create_new_directory(path: &Path) -> Result<()> {
    no_links(path)?;
    #[cfg(windows)]
    windows::new_directory(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(path)
            .map_err(|_| UNPROVEN)?;
        std::fs::File::open(path.parent().ok_or(UNPROVEN)?)
            .and_then(|f| f.sync_all())
            .map_err(|_| UNPROVEN)?;
    }
    verify_protected(path)
}
/// Strict create-new private file, with handle security verified before return.
/// Caller writes/syncs/readbacks and revalidates its native creation capability.
pub fn create_new_file(path: &Path) -> Result<std::fs::File> {
    no_links(path)?;
    #[cfg(windows)]
    let file = windows::new_file(path)?;
    #[cfg(unix)]
    let file = {
        use std::os::unix::fs::OpenOptionsExt;
        std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(path)
            .map_err(|_| UNPROVEN)?
    };
    private_handle(&file)?;
    Ok(file)
}
/// Bounded no-follow private read (1..=32 MiB). Access denied, links, directories,
/// weak ACLs and overlarge bytes are errors; only genuine NotFound is None.
/// This does not substitute for verify_ancestors or native object-identity guards.
pub fn read_private(path: &Path, limit: usize) -> Result<Option<Vec<u8>>> {
    use std::io::Read;
    if limit == 0 || limit > 32 * 1024 * 1024 {
        return Err(UNPROVEN);
    }
    no_links(path)?;
    #[cfg(windows)]
    let opened = windows::open_no_follow(path, true);
    #[cfg(unix)]
    let opened = {
        use std::os::unix::fs::OpenOptionsExt;
        std::fs::OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
            .open(path)
    };
    let file = match opened {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(UNPROVEN),
    };
    if !file.metadata().map_err(|_| UNPROVEN)?.is_file() {
        return Err(UNPROVEN);
    }
    private_handle(&file)?;
    let mut bytes = Vec::new();
    (&file)
        .take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| UNPROVEN)?;
    if bytes.len() > limit {
        return Err(UNPROVEN);
    }
    private_handle(&file)?;
    no_links(path)?;
    Ok(Some(bytes))
}
