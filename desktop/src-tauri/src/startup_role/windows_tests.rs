use super::*;
use bat_fleet_core::publication_state;
use std::{os::windows::ffi::OsStrExt, ptr};
use windows_sys::Win32::{
    Foundation::LocalFree,
    Security::{
        Authorization::{
            ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
            GetNamedSecurityInfoW, SE_FILE_OBJECT,
        },
        GetSecurityDescriptorControl, SetFileSecurityW, DACL_SECURITY_INFORMATION,
        OWNER_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION, SECURITY_ATTRIBUTES,
        SE_DACL_PROTECTED,
    },
    Storage::FileSystem::CreateDirectoryW,
};

fn wide(path: &Path) -> Vec<u16> {
    path.as_os_str().encode_wide().chain(Some(0)).collect()
}
fn descriptor<T>(sddl: &str, use_sd: impl FnOnce(*mut core::ffi::c_void) -> T) -> T {
    let text: Vec<_> = sddl.encode_utf16().chain(Some(0)).collect();
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
        "mandatory descriptor construction"
    );
    let result = use_sd(sd);
    unsafe {
        LocalFree(sd);
    }
    result
}
fn security(path: &Path) -> (String, bool) {
    let mut owner = ptr::null_mut();
    let mut sd = ptr::null_mut();
    assert_eq!(
        unsafe {
            GetNamedSecurityInfoW(
                wide(path).as_ptr(),
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                &mut owner,
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
                &mut sd,
            )
        },
        0,
        "mandatory security readback"
    );
    let mut text = ptr::null_mut();
    assert_ne!(unsafe { ConvertSidToStringSidW(owner, &mut text) }, 0);
    let mut len = 0;
    while len < 184 && unsafe { *text.add(len) } != 0 {
        len += 1;
    }
    assert!(len < 184);
    let owner = String::from_utf16(unsafe { std::slice::from_raw_parts(text, len) }).unwrap();
    let (mut control, mut revision) = (0, 0);
    assert_ne!(
        unsafe { GetSecurityDescriptorControl(sd, &mut control, &mut revision) },
        0
    );
    unsafe {
        LocalFree(text.cast());
        LocalFree(sd);
    }
    (owner, control & SE_DACL_PROTECTED != 0)
}
fn installation() -> (tempfile::TempDir, PathBuf, PathBuf) {
    let root = tempfile::tempdir().unwrap();
    let base = bat_fleet_core::installation::canonical_local(root.path()).unwrap();
    (
        root,
        base.join("Local/AppData/example.desktop"),
        base.join("config"),
    )
}

#[test]
fn windows_real_selection_reads_its_explicit_current_owner_private_objects() {
    let (_root, data, config) = installation();
    assert!(!data.exists());
    let selected = select(&data, &config, Role::Client, 0).unwrap();
    assert_eq!(load_or_infer(&data, &config).unwrap(), selected);
    assert_eq!(evidence(&data).unwrap().snapshot, selected);
    let current = bat_fleet_core::windows::current_login().unwrap().owner_sid;
    for path in [data.join("startup-role"), record_path(&data)] {
        assert_eq!(security(&path), (current.clone(), true));
        publication_state::verify_protected(&path).unwrap();
    }
}

#[test]
fn windows_release_layout_reads_or_reports_owner_refusal_without_rewriting() {
    let (_root, data, config) = installation();
    fs::create_dir_all(&data).unwrap();
    let directory = data.join("startup-role");
    // The released writer specifies only the DACL, then creates an inherited
    // leaf with OpenOptions. Windows chooses each object's default owner.
    descriptor("D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)", |sd| {
        let attrs = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: sd,
            bInheritHandle: 0,
        };
        assert_ne!(
            unsafe { CreateDirectoryW(wide(&directory).as_ptr(), &attrs) },
            0
        );
    });
    let record = Record {
        version: 1,
        installation_ref: uuid::Uuid::new_v4().to_string(),
        generation: 1,
        role: Role::Client,
    };
    let bytes = serde_json::to_vec(&record).unwrap();
    let temporary = directory.join(".role-release");
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .unwrap();
    file.write_all(&bytes).unwrap();
    file.sync_all().unwrap();
    drop(file);
    fs::hard_link(&temporary, record_path(&data)).unwrap();
    fs::remove_file(temporary).unwrap();
    // Ancestor failure is not allowed to masquerade as an owner compatibility result.
    for parent in data.ancestors() {
        publication_state::verify_ancestor(parent, false).unwrap();
    }
    let dir_security = security(&directory);
    let leaf_security = security(&record_path(&data));
    assert!(dir_security.1);
    assert!(
        !leaf_security.1,
        "the released leaf inherits its effective DACL"
    );
    let current = bat_fleet_core::windows::current_login().unwrap().owner_sid;
    let result = load_or_infer(&data, &config);
    if dir_security.0 == current && leaf_security.0 == current {
        let snapshot = result.unwrap();
        assert_eq!(
            snapshot.installation_ref.as_ref(),
            Some(&record.installation_ref)
        );
        assert_eq!(snapshot.role, Some(Role::Client));
    } else {
        assert_eq!(result.unwrap_err(), UNREADABLE);
        let (managed, launch) =
            crate::managed::Managed::prepare(&data.join("missing-resources"), &data, &config);
        assert!(launch.is_none());
        assert_eq!(managed.status().error.as_deref(), Some(UNREADABLE));
        assert!(
            managed.startup_role().is_none(),
            "not a selectable pending role"
        );
    }
    assert_eq!(fs::read(record_path(&data)).unwrap(), bytes);
    assert_eq!(security(&directory), dir_security);
    assert_eq!(security(&record_path(&data)), leaf_security);
}

#[test]
fn windows_empty_unprotected_role_directory_is_refused_before_inference() {
    let (_root, data, config) = installation();
    fs::create_dir_all(data.join("startup-role")).unwrap();
    let before = security(&data.join("startup-role"));
    assert!(!before.1, "mandatory inherited directory fixture");
    assert_eq!(load_or_infer(&data, &config).unwrap_err(), UNREADABLE);
    assert_eq!(security(&data.join("startup-role")), before);
    assert_eq!(fs::read_dir(data.join("startup-role")).unwrap().count(), 0);
}

#[test]
fn windows_role_leaf_acl_failure_never_falls_back_to_unchecked_open() {
    let (_root, data, config) = installation();
    select(&data, &config, Role::Client, 0).unwrap();
    let path = record_path(&data);
    let bytes = fs::read(&path).unwrap();
    descriptor("D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;;FR;;;WD)", |sd| {
        assert_ne!(
            unsafe {
                SetFileSecurityW(
                    wide(&path).as_ptr(),
                    DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                    sd,
                )
            },
            0
        );
    });
    let before = security(&path);
    assert_eq!(load_or_infer(&data, &config).unwrap_err(), UNREADABLE);
    assert_eq!(fs::read(&path).unwrap(), bytes);
    assert_eq!(security(&path), before);
}
