use super::*;
use crate::windows_creation::{tests::windows_tests, writer::CreationWriter};

fn native_fixture() -> Fixture {
    let login = crate::windows::current_login().unwrap();
    let mut f = Fixture::with_sid(login.owner_sid);
    f.reader.expected.creator.session_id = login.session_id;
    f.owner = f
        .reader
        .expected
        .owner(
            &Record::Provisioning(Box::new(f.reader.expected.clone()))
                .encode()
                .unwrap(),
        )
        .unwrap();
    seeded_from(f)
}
fn native_platform(f: &Fixture) -> WindowsCreationPlatform {
    let mut platform = windows_tests::platform(f);
    platform.reader.security.monitor_for_fixture = true;
    platform
}

#[test]
fn windows_writer_nested_levels_have_protected_private_dacl_and_recorded_identity() {
    let f = native_fixture();
    let mut p = native_platform(&f);
    let mut w = CreationWriter::held(&mut p).unwrap();
    let kit = w.platform.reader.expected.scope.kit_root.clone();
    let paths = [
        kit.clone(),
        kit.join("client"),
        kit.join("client/bat-profiles"),
    ];
    for path in &paths {
        w.directory_level(path, &mut || Ok(())).unwrap();
        assert!(windows_tests::protected(path));
        crate::publication_state::verify_protected(path).unwrap();
    }
    let mut found = 0;
    for key in w.entries().unwrap() {
        if let Record::Directory(record) =
            Record::decode(&key, &w.read_private(&key, RECORD_LIMIT).unwrap().unwrap()).unwrap()
        {
            if paths.contains(&record.canonical_path) {
                assert_eq!(
                    record.identity,
                    w.platform.reader.identity(&record.canonical_path).unwrap()
                );
                assert_eq!(
                    record.parent_identity,
                    w.platform
                        .reader
                        .identity(record.canonical_path.parent().unwrap())
                        .unwrap()
                );
                found += 1;
            }
        }
    }
    assert_eq!(found, paths.len());
}

fn dacl(path: &Path) -> Vec<u8> {
    use std::{os::windows::ffi::OsStrExt, ptr};
    use windows_sys::Win32::{
        Foundation::LocalFree,
        Security::{
            AclSizeInformation,
            Authorization::{GetNamedSecurityInfoW, SE_FILE_OBJECT},
            GetAclInformation, ACL_SIZE_INFORMATION, DACL_SECURITY_INFORMATION,
        },
    };
    let name: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut descriptor = ptr::null_mut();
    let mut acl = ptr::null_mut();
    assert_eq!(
        unsafe {
            GetNamedSecurityInfoW(
                name.as_ptr(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                ptr::null_mut(),
                ptr::null_mut(),
                &mut acl,
                ptr::null_mut(),
                &mut descriptor,
            )
        },
        0
    );
    assert!(!acl.is_null());
    let mut size = ACL_SIZE_INFORMATION::default();
    assert_ne!(
        unsafe {
            GetAclInformation(
                acl,
                (&mut size as *mut ACL_SIZE_INFORMATION).cast(),
                std::mem::size_of::<ACL_SIZE_INFORMATION>() as u32,
                AclSizeInformation,
            )
        },
        0
    );
    let bytes = unsafe {
        std::slice::from_raw_parts(acl.cast::<u8>(), size.AclBytesInUse as usize).to_vec()
    };
    unsafe {
        LocalFree(descriptor);
    }
    bytes
}

#[test]
fn windows_writer_refuses_existing_broad_dacl_without_repair() {
    let f = native_fixture();
    let kit = f.reader.expected.scope.kit_root.clone();
    crate::publication_state::create_new_directory(&kit).unwrap();
    assert!(
        std::process::Command::new("icacls.exe")
            .arg(&kit)
            .args(["/grant", "*S-1-1-0:(OI)(CI)F"])
            .status()
            .unwrap()
            .success(),
        "mandatory broad DACL fixture"
    );
    let before = dacl(&kit);
    assert!(crate::publication_state::verify_protected(&kit).is_err());
    let mut p = native_platform(&f);
    assert!(CreationWriter::held(&mut p)
        .unwrap()
        .directory_level(&kit, &mut || Ok(()))
        .is_err());
    assert_eq!(dacl(&kit), before);
}

#[test]
fn windows_writer_refuses_junction_parent_without_touching_target() {
    let f = native_fixture();
    let mut p = native_platform(&f);
    let mut w = CreationWriter::held(&mut p).unwrap();
    let kit = w.platform.reader.expected.scope.kit_root.clone();
    w.directory_level(&kit, &mut || Ok(())).unwrap();
    let target = f.root.join("junction-target");
    crate::publication_state::create_new_directory(&target).unwrap();
    write_private(&target.join("preserved"), b"preserved");
    assert!(
        std::process::Command::new("cmd.exe")
            .args(["/C", "mklink", "/J"])
            .arg(kit.join("client"))
            .arg(&target)
            .status()
            .unwrap()
            .success(),
        "mandatory junction fixture"
    );
    assert!(w
        .directory_level(&kit.join("client/bat-profiles"), &mut || Ok(()))
        .is_err());
    assert_eq!(
        std::fs::read(target.join("preserved")).unwrap(),
        b"preserved"
    );
    assert!(!target.join("bat-profiles").exists());
}
