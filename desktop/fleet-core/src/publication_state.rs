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
    use super::*;
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
