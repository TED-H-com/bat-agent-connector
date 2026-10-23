use super::*;
use std::{os::windows::ffi::OsStrExt, ptr};
use windows_sys::Win32::{
    Foundation::LocalFree,
    Security::{
        Authorization::ConvertStringSecurityDescriptorToSecurityDescriptorW, GetFileSecurityW,
        SetFileSecurityW, DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION,
        UNPROTECTED_DACL_SECURITY_INFORMATION,
    },
};
fn dacl(path: &Path) -> Vec<u8> {
    let name: Vec<_> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut size = 0;
    unsafe {
        GetFileSecurityW(
            name.as_ptr(),
            DACL_SECURITY_INFORMATION,
            ptr::null_mut(),
            0,
            &mut size,
        );
    }
    assert!(size > 0, "mandatory descriptor readback");
    let mut bytes = vec![0u8; size as usize];
    assert_ne!(
        unsafe {
            GetFileSecurityW(
                name.as_ptr(),
                DACL_SECURITY_INFORMATION,
                bytes.as_mut_ptr().cast(),
                size,
                &mut size,
            )
        },
        0
    );
    bytes
}
fn set_dacl(path: &Path, broad: bool) {
    let sddl = if broad {
        "D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FR;;;WD)"
    } else {
        "D:(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)"
    };
    let text: Vec<_> = sddl.encode_utf16().chain(Some(0)).collect();
    let name: Vec<_> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut descriptor = ptr::null_mut();
    assert_ne!(
        unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                text.as_ptr(),
                1,
                &mut descriptor,
                ptr::null_mut(),
            )
        },
        0
    );
    let flags = DACL_SECURITY_INFORMATION
        | if broad {
            PROTECTED_DACL_SECURITY_INFORMATION
        } else {
            UNPROTECTED_DACL_SECURITY_INFORMATION
        };
    let result = unsafe { SetFileSecurityW(name.as_ptr(), flags, descriptor) };
    unsafe {
        LocalFree(descriptor);
    }
    assert_ne!(result, 0, "mandatory ACL fixture construction");
}
#[test]
fn windows_startup_role_reader_refuses_broad_or_unprotected_directory_and_explicit_leaf_without_repair(
) {
    for file in [false, true] {
        for broad in [false, true] {
            let f = Fixture::new();
            let path = startup_role::record_path(&f.data);
            let original = std::fs::read(&path).unwrap();
            let target = if file {
                path.clone()
            } else {
                path.parent().unwrap().into()
            };
            set_dacl(&target, broad);
            let before = dacl(&target);
            assert!(
                matches!(startup_role::evidence(&f.data), Err(e) if e == startup_role::UNREADABLE)
            );
            assert_eq!(std::fs::read(&path).unwrap(), original);
            assert_eq!(dacl(&target), before, "reader must not repair the ACL");
        }
    }
}
