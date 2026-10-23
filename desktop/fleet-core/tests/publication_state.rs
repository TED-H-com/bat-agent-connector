use bat_fleet_core::publication_state;
use std::path::PathBuf;
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let root = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("creation-storage-{:032x}", rand::random::<u128>()));
        std::fs::create_dir(&root).unwrap();
        Self(root)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        std::fs::remove_dir_all(&self.0).unwrap();
    }
}
#[test]
fn private_storage_create_new_does_not_adopt_or_truncate() {
    let temp = Temp::new();
    let directory = temp.0.join("private");
    publication_state::create_new_directory(&directory).unwrap();
    assert!(publication_state::create_new_directory(&directory).is_err());
    let path = directory.join("receipt");
    use std::io::Write;
    let mut file = publication_state::create_new_file(&path).unwrap();
    file.write_all(b"original").unwrap();
    file.sync_all().unwrap();
    drop(file);
    assert!(publication_state::create_new_file(&path).is_err());
    assert_eq!(
        publication_state::read_private(&path, 32).unwrap(),
        Some(b"original".to_vec())
    );
    assert!(publication_state::read_private(&path, 3).is_err());
    assert_eq!(
        publication_state::read_private(&directory.join("absent"), 32).unwrap(),
        None
    );
    assert!(publication_state::read_private(&directory, 32).is_err());
}
#[cfg(unix)]
#[test]
fn private_storage_links_and_public_permissions_are_not_absence() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let temp = Temp::new();
    let directory = temp.0.join("private");
    publication_state::create_new_directory(&directory).unwrap();
    let path = directory.join("receipt");
    std::fs::write(&path, b"original").unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
    assert!(publication_state::read_private(&path, 32).is_err());
    assert!(publication_state::create_new_file(&path).is_err());
    symlink(temp.0.join("not-there"), directory.join("linked")).unwrap();
    assert!(publication_state::read_private(&directory.join("linked"), 32).is_err());
    symlink(&directory, temp.0.join("ancestor")).unwrap();
    assert!(publication_state::read_private(&temp.0.join("ancestor/absent"), 32).is_err());
}

#[cfg(windows)]
mod windows_cases {
    use super::*;
    use std::{os::windows::ffi::OsStrExt, ptr};
    use windows_sys::Win32::{
        Foundation::{CloseHandle, GetLastError, LocalFree, HANDLE, LUID},
        Security::{
            AdjustTokenPrivileges,
            Authorization::{
                ConvertStringSecurityDescriptorToSecurityDescriptorW, GetNamedSecurityInfoW,
                SetNamedSecurityInfoW, SE_FILE_OBJECT,
            },
            GetSecurityDescriptorControl, GetSecurityDescriptorDacl, GetSecurityDescriptorOwner,
            LookupPrivilegeValueW, DACL_SECURITY_INFORMATION, LUID_AND_ATTRIBUTES,
            OWNER_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION, SECURITY_ATTRIBUTES,
            SE_DACL_PROTECTED, SE_PRIVILEGE_ENABLED, TOKEN_ADJUST_PRIVILEGES, TOKEN_PRIVILEGES,
            TOKEN_QUERY, UNPROTECTED_DACL_SECURITY_INFORMATION,
        },
        Storage::FileSystem::CreateDirectoryW,
        System::Threading::{GetCurrentProcess, OpenProcessToken},
    };
    fn wide(value: &std::ffi::OsStr) -> Vec<u16> {
        value.encode_wide().chain(Some(0)).collect()
    }
    fn current() -> String {
        bat_fleet_core::windows::current_login().unwrap().owner_sid
    }
    fn private() -> String {
        format!("O:{}D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)", current())
    }
    fn descriptor<T>(sddl: &str, f: impl FnOnce(*mut core::ffi::c_void) -> T) -> T {
        let text: Vec<u16> = sddl.encode_utf16().chain(Some(0)).collect();
        let mut sd = ptr::null_mut();
        assert_ne!(
            unsafe {
                ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    text.as_ptr(),
                    1,
                    &mut sd,
                    ptr::null_mut(),
                )
            },
            0,
            "fixture descriptor: {}",
            unsafe { GetLastError() }
        );
        struct Free(*mut core::ffi::c_void);
        impl Drop for Free {
            fn drop(&mut self) {
                unsafe {
                    LocalFree(self.0);
                }
            }
        }
        let _free = Free(sd);
        f(sd)
    }
    fn directory(path: &std::path::Path, sddl: &str) {
        descriptor(sddl, |sd| {
            let attrs = SECURITY_ATTRIBUTES {
                nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
                lpSecurityDescriptor: sd,
                bInheritHandle: 0,
            };
            assert_ne!(
                unsafe { CreateDirectoryW(wide(path.as_os_str()).as_ptr(), &attrs) },
                0,
                "fixture directory: {}",
                unsafe { GetLastError() }
            );
        });
    }
    fn set_acl(path: &std::path::Path, sddl: &str, protected: bool, owner: bool) {
        descriptor(sddl, |sd| {
            let mut acl = ptr::null_mut();
            let mut present = 0;
            let mut defaulted = 0;
            let mut sid = ptr::null_mut();
            assert_ne!(
                unsafe { GetSecurityDescriptorDacl(sd, &mut present, &mut acl, &mut defaulted) },
                0
            );
            assert_ne!(present, 0);
            assert_ne!(
                unsafe { GetSecurityDescriptorOwner(sd, &mut sid, &mut defaulted) },
                0
            );
            let mut info = DACL_SECURITY_INFORMATION
                | if protected {
                    PROTECTED_DACL_SECURITY_INFORMATION
                } else {
                    UNPROTECTED_DACL_SECURITY_INFORMATION
                };
            if owner {
                info |= OWNER_SECURITY_INFORMATION;
            }
            assert_eq!(
                unsafe {
                    SetNamedSecurityInfoW(
                        wide(path.as_os_str()).as_ptr(),
                        SE_FILE_OBJECT,
                        info,
                        if owner { sid } else { ptr::null_mut() },
                        ptr::null_mut(),
                        acl,
                        ptr::null_mut(),
                    )
                },
                0,
                "fixture ACL update"
            );
        });
    }
    fn protected(path: &std::path::Path) -> bool {
        let mut sd = ptr::null_mut();
        assert_eq!(
            unsafe {
                GetNamedSecurityInfoW(
                    wide(path.as_os_str()).as_ptr(),
                    SE_FILE_OBJECT,
                    DACL_SECURITY_INFORMATION,
                    ptr::null_mut(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                    &mut sd,
                )
            },
            0
        );
        let mut control = 0;
        let mut revision = 0;
        let ok = unsafe { GetSecurityDescriptorControl(sd, &mut control, &mut revision) };
        unsafe {
            LocalFree(sd);
        }
        assert_ne!(ok, 0);
        control & SE_DACL_PROTECTED != 0
    }
    struct Privilege {
        token: HANDLE,
        previous: TOKEN_PRIVILEGES,
    }
    impl Privilege {
        fn enable(name: &str) -> Self {
            let mut token = ptr::null_mut();
            assert_ne!(
                unsafe {
                    OpenProcessToken(
                        GetCurrentProcess(),
                        TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                        &mut token,
                    )
                },
                0
            );
            let mut luid = LUID::default();
            let name: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
            assert_ne!(
                unsafe { LookupPrivilegeValueW(ptr::null(), name.as_ptr(), &mut luid) },
                0
            );
            let requested = TOKEN_PRIVILEGES {
                PrivilegeCount: 1,
                Privileges: [LUID_AND_ATTRIBUTES {
                    Luid: luid,
                    Attributes: SE_PRIVILEGE_ENABLED,
                }],
            };
            let mut previous = TOKEN_PRIVILEGES::default();
            let mut length = 0;
            assert_ne!(
                unsafe {
                    AdjustTokenPrivileges(
                        token,
                        0,
                        &requested,
                        std::mem::size_of::<TOKEN_PRIVILEGES>() as u32,
                        &mut previous,
                        &mut length,
                    )
                },
                0
            );
            assert_eq!(
                unsafe { GetLastError() },
                0,
                "required fixture privilege was not assigned"
            );
            Self { token, previous }
        }
    }
    impl Drop for Privilege {
        fn drop(&mut self) {
            unsafe {
                AdjustTokenPrivileges(
                    self.token,
                    0,
                    &self.previous,
                    0,
                    ptr::null_mut(),
                    ptr::null_mut(),
                );
                CloseHandle(self.token);
            }
        }
    }
    #[test]
    fn windows_creation_receipt_has_current_owner_protected_dacl_and_create_new() {
        let temp = Temp::new();
        let root = temp.0.join("native");
        publication_state::create_new_directory(&root).unwrap();
        assert!(protected(&root));
        publication_state::verify_protected(&root).unwrap();
        let file = root.join("receipt");
        drop(publication_state::create_new_file(&file).unwrap());
        publication_state::verify_protected(&file).unwrap();
        publication_state::verify_ancestors(&file, &root).unwrap();
        assert!(protected(&file));
        assert!(publication_state::create_new_file(&file).is_err());
        assert!(publication_state::create_new_directory(&root).is_err());
    }
    #[test]
    fn windows_creation_unprotected_private_acl_is_not_adopted_or_repaired() {
        let temp = Temp::new();
        let parent = temp.0.join("protected-parent");
        directory(&parent, &private());
        let root = parent.join("native");
        directory(&root, &private());
        set_acl(&root, &private(), false, false);
        assert!(!protected(&root));
        assert!(publication_state::verify_protected(&root).is_err());
        assert!(publication_state::create_new_directory(&root).is_err());
        assert!(!protected(&root));
        set_acl(&root, &private(), true, false);
        for name in ["receipt", "journal", "output"] {
            let file = root.join(name);
            use std::io::Write;
            let mut handle = publication_state::create_new_file(&file).unwrap();
            handle.write_all(b"preserved").unwrap();
            handle.sync_all().unwrap();
            drop(handle);
            set_acl(&file, &private(), false, false);
            assert!(!protected(&file));
            assert!(publication_state::read_private(&file, 32).is_err());
            assert!(publication_state::create_new_file(&file).is_err());
            assert!(!protected(&file));
            assert_eq!(std::fs::read(&file).unwrap(), b"preserved");
        }
    }
    #[test]
    fn windows_creation_foreign_owner_null_public_or_unknown_ace_refuses() {
        let temp = Temp::new();
        let root = temp.0.join("native");
        directory(&root, &private());
        for sddl in [
            format!("O:{}D:NO_ACCESS_CONTROL", current()),
            format!(
                "O:{}D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;;GR;;;WD)",
                current()
            ),
            format!(
                "O:{}D:P(D;;WD;;;S-1-5-21-123-456-789-4321)(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)",
                current()
            ),
        ] {
            set_acl(&root, &sddl, true, false);
            assert!(publication_state::verify_protected(&root).is_err());
            set_acl(&root, &private(), true, false);
        }
        let _privilege = Privilege::enable("SeRestorePrivilege");
        let foreign = format!(
            "O:S-1-5-21-123-456-789-4321D:P(A;OICI;FA;;;{})(A;OICI;FA;;;SY)",
            current()
        );
        set_acl(&root, &foreign, true, true);
        assert!(publication_state::verify_protected(&root).is_err());
        set_acl(&root, &private(), true, true);
    }
    #[test]
    fn windows_creation_parent_delete_child_and_security_write_refuse() {
        let temp = Temp::new();
        let root = temp.0.join("ancestor");
        directory(&root, &private());
        let child = root.join("private");
        publication_state::create_new_directory(&child).unwrap();
        publication_state::verify_ancestors(&child.join("absent"), &child).unwrap();
        // DC is the directory-service 0x2 right (FILE_ADD_FILE here), not
        // FILE_DELETE_CHILD (0x40). WD/WO/SD are 0x40000/0x80000/0x10000;
        // GW maps to file generic write, including EA and attribute writes.
        for rights in ["0x00000040", "WD", "WO", "SD", "GW"] {
            let sddl = format!(
                "O:{}D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;;{rights};;;S-1-5-21-123-456-789-4321)",
                current()
            );
            set_acl(&root, &sddl, true, false);
            assert!(publication_state::verify_ancestor(&root, false).is_err());
            assert!(publication_state::verify_ancestors(&child.join("absent"), &child).is_err());
        }
        let read = format!(
            "O:{}D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;;GR;;;WD)",
            current()
        );
        set_acl(&root, &read, true, false);
        publication_state::verify_ancestor(&root, false).unwrap();
        for rights in ["0x00000002", "0x00000004", "0x00000006"] {
            let add = format!(
                "O:{}D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;;{rights};;;S-1-5-21-123-456-789-4321)",
                current()
            );
            set_acl(&root, &add, true, false);
            publication_state::verify_ancestor(&root, false).unwrap();
            assert!(publication_state::verify_ancestor(&root, true).is_err());
        }
        set_acl(&root, &private(), true, false);
    }
    #[test]
    fn windows_creation_reparse_leaf_and_ancestor_never_follow() {
        let temp = Temp::new();
        let root = temp.0.join("native");
        publication_state::create_new_directory(&root).unwrap();
        let target = root.join("target");
        drop(publication_state::create_new_file(&target).unwrap());
        // Junction construction is mandatory and does not depend on symlink privilege.
        let junction = temp.0.join("junction");
        let output = std::process::Command::new("cmd.exe")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(&root)
            .output()
            .unwrap();
        assert!(output.status.success(), "junction fixture: {output:?}");
        assert!(publication_state::verify_protected(&junction).is_err());
        assert!(publication_state::read_private(&junction.join("target"), 32).is_err());
        assert!(publication_state::read_private(&junction.join("missing/nested"), 32).is_err());
        assert!(publication_state::create_new_file(&junction.join("absent")).is_err());
        assert_eq!(std::fs::read(&target).unwrap(), b"");
        assert!(!root.join("absent").exists());
        std::fs::remove_dir(&junction).unwrap();
        let _privilege = Privilege::enable("SeCreateSymbolicLinkPrivilege");
        let leaf = root.join("linked");
        std::os::windows::fs::symlink_file(&target, &leaf).unwrap();
        assert!(publication_state::verify_protected(&leaf).is_err());
        assert!(publication_state::read_private(&leaf, 32).is_err());
        assert!(publication_state::create_new_file(&leaf).is_err());
        let ancestor = temp.0.join("linked-ancestor");
        std::os::windows::fs::symlink_dir(&root, &ancestor).unwrap();
        assert!(publication_state::read_private(&ancestor.join("absent"), 32).is_err());
        assert!(publication_state::verify_ancestors(&ancestor.join("absent"), &root).is_err());
        std::fs::remove_dir(&ancestor).unwrap();
        std::fs::remove_file(&leaf).unwrap();
    }
    #[test]
    fn windows_creation_read_denied_is_not_absence() {
        let temp = Temp::new();
        let root = temp.0.join("native");
        publication_state::create_new_directory(&root).unwrap();
        let path = root.join("denied");
        drop(publication_state::create_new_file(&path).unwrap());
        // Retain only security-edit rights for fixture cleanup, never data read.
        let denied = format!("O:{}D:P(A;;RCWD;;;OW)(A;;FA;;;SY)", current());
        set_acl(&path, &denied, true, false);
        assert_eq!(
            std::fs::read(&path).unwrap_err().kind(),
            std::io::ErrorKind::PermissionDenied
        );
        assert!(publication_state::read_private(&path, 32).is_err());
        set_acl(&path, &private(), true, false);
    }
}

#[cfg(unix)]
#[test]
fn private_storage_fifo_is_refused_without_waiting_for_a_writer() {
    use std::os::unix::{ffi::OsStrExt, fs::OpenOptionsExt};
    let temp = Temp::new();
    let directory = temp.0.join("private");
    publication_state::create_new_directory(&directory).unwrap();
    let fifo = directory.join("receipt");
    let name = std::ffi::CString::new(fifo.as_os_str().as_bytes()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
    for read in [true, false] {
        let (tx, rx) = std::sync::mpsc::channel();
        let path = fifo.clone();
        let worker = std::thread::spawn(move || {
            let result = if read {
                publication_state::read_private(&path, 32).map(|_| ())
            } else {
                publication_state::verify_protected(&path)
            };
            tx.send(result).unwrap();
        });
        let result = rx.recv_timeout(std::time::Duration::from_secs(2));
        // Even a failing implementation must release its thread and fixture.
        if result.is_err() {
            let unblock = std::fs::OpenOptions::new()
                .read(true)
                .write(true)
                .custom_flags(libc::O_NONBLOCK)
                .open(&fifo)
                .unwrap();
            assert!(rx
                .recv_timeout(std::time::Duration::from_secs(5))
                .unwrap()
                .is_err());
            drop(unblock);
        }
        worker.join().unwrap();
        assert_eq!(result.unwrap(), Err("PUBLICATION_STATE_UNPROVEN"));
    }
}
