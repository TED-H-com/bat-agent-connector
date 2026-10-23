use super::*;
use std::{os::windows::ffi::OsStrExt, ptr};
use windows_sys::Win32::{
    Foundation::LocalFree,
    Security::{
        Authorization::{
            ConvertStringSecurityDescriptorToSecurityDescriptorW, GetNamedSecurityInfoW,
            SetNamedSecurityInfoW, SE_FILE_OBJECT,
        },
        GetSecurityDescriptorControl, GetSecurityDescriptorDacl, DACL_SECURITY_INFORMATION,
        SE_DACL_PROTECTED, UNPROTECTED_DACL_SECURITY_INFORMATION,
    },
};
fn wide(path: &Path) -> Vec<u16> {
    path.as_os_str().encode_wide().chain(Some(0)).collect()
}
pub(super) fn protected(path: &Path) -> bool {
    let mut sd = ptr::null_mut();
    assert_eq!(
        unsafe {
            GetNamedSecurityInfoW(
                wide(path).as_ptr(),
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
    let (mut control, mut revision) = (0, 0);
    let ok = unsafe { GetSecurityDescriptorControl(sd, &mut control, &mut revision) };
    unsafe {
        LocalFree(sd);
    }
    assert_ne!(ok, 0);
    control & SE_DACL_PROTECTED != 0
}
fn unprotect(path: &Path) {
    let sddl: Vec<u16> = format!(
        "O:{}D:(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)",
        crate::windows::current_login().unwrap().owner_sid
    )
    .encode_utf16()
    .chain(Some(0))
    .collect();
    let mut sd = ptr::null_mut();
    assert_ne!(
        unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl.as_ptr(),
                1,
                &mut sd,
                ptr::null_mut(),
            )
        },
        0
    );
    let (mut present, mut defaulted, mut acl) = (0, 0, ptr::null_mut());
    assert_ne!(
        unsafe { GetSecurityDescriptorDacl(sd, &mut present, &mut acl, &mut defaulted) },
        0
    );
    assert_ne!(present, 0);
    assert!(!acl.is_null());
    let result = unsafe {
        SetNamedSecurityInfoW(
            wide(path).as_ptr(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION | UNPROTECTED_DACL_SECURITY_INFORMATION,
            ptr::null_mut(),
            ptr::null_mut(),
            acl,
            ptr::null_mut(),
        )
    };
    unsafe {
        LocalFree(sd);
    }
    assert_eq!(result, 0, "required unprotected DACL fixture");
}
fn fixture() -> Fixture {
    Fixture::with_sid(crate::windows::current_login().unwrap().owner_sid)
}
pub(super) fn platform(f: &Fixture) -> WindowsCreationPlatform {
    let mut p = WindowsCreationPlatform::new(
        f.reader.expected.clone(),
        f.root.clone(),
        std::env::current_exe().unwrap(),
        &f.root.join("Startup"),
        &f.root.join("user"),
    )
    .unwrap();
    // Substitute only Launcher possession. Token, path, handle ACL and object
    // identity observations stay native; no account-wide mutex is acquired.
    p.reader.security.held_for_fixture = true;
    p
}
#[test]
fn windows_creation_adapter_refuses_junction_ancestor_without_following_target() {
    let f = fixture();
    let client = f.reader.expected.scope.kit_root.join("client");
    platform(&f)
        .verify_private_state(&f.reader.root(), &f.owner)
        .unwrap();
    let target = f.root.join("junction-target");
    crate::publication_state::create_new_directory(&target).unwrap();
    write_private(&target.join("preserved"), b"original target");
    f.reader.directories.borrow_mut().clear();
    std::fs::remove_dir_all(&client).unwrap();
    assert!(
        std::process::Command::new("cmd.exe")
            .args(["/C", "mklink", "/J"])
            .arg(&client)
            .arg(&target)
            .status()
            .unwrap()
            .success(),
        "mandatory junction fixture"
    );
    assert!(platform(&f)
        .verify_private_state(&f.reader.root(), &f.owner)
        .is_err());
    assert_eq!(
        std::fs::read(target.join("preserved")).unwrap(),
        b"original target"
    );
}
#[test]
fn windows_creation_adapter_refuses_unprotected_reserved_directory_without_repair() {
    let f = fixture();
    let client = f.reader.expected.scope.kit_root.join("client");
    platform(&f)
        .verify_private_state(&f.reader.root(), &f.owner)
        .unwrap();
    unprotect(&client);
    assert!(!protected(&client));
    assert!(platform(&f)
        .verify_private_state(&f.reader.root(), &f.owner)
        .is_err());
    assert!(!protected(&client));
}
#[test]
fn windows_creation_adapter_refuses_reserved_directory_replaced_between_reads() {
    let f = fixture();
    let client = f.reader.expected.scope.kit_root.join("client");
    platform(&f)
        .verify_private_state(&f.reader.root(), &f.owner)
        .unwrap();
    let before = identity(&f.reader.security.open(&client, true, false).unwrap()).unwrap();
    f.reader.directories.borrow_mut().clear();
    std::fs::rename(&client, f.root.join("previous-client")).unwrap();
    crate::publication_state::create_new_directory(&client).unwrap();
    crate::publication_state::create_new_directory(&client.join("bat-profiles")).unwrap();
    assert_ne!(
        before,
        identity(&f.reader.security.open(&client, true, false).unwrap()).unwrap()
    );
    assert!(platform(&f)
        .verify_private_state(&f.reader.root(), &f.owner)
        .is_err());
}
