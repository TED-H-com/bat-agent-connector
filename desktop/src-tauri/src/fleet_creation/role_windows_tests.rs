use super::*;
use std::{
    os::windows::ffi::OsStrExt,
    ptr::{self, NonNull},
};
use windows_sys::Win32::{
    Foundation::LocalFree,
    Security::{
        Authorization::{
            ConvertSecurityDescriptorToStringSecurityDescriptorW,
            ConvertStringSecurityDescriptorToSecurityDescriptorW, SetNamedSecurityInfoW,
            SE_FILE_OBJECT,
        },
        GetAce, GetFileSecurityW, GetSecurityDescriptorControl, GetSecurityDescriptorDacl,
        IsValidAcl, IsWellKnownSid, WinWorldSid, ACCESS_ALLOWED_ACE, ACE_HEADER,
        DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION, SE_DACL_PROTECTED,
        UNPROTECTED_DACL_SECURITY_INFORMATION,
    },
};
pub(super) fn dacl(path: &Path) -> Vec<u8> {
    dacl_case(path, "shared ACL readback")
}
fn dacl_case(path: &Path, case: &str) -> Vec<u8> {
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
    assert!(size > 0, "{case}: mandatory descriptor readback");
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
        0,
        "{case}: mandatory descriptor readback"
    );
    bytes
}
struct AclReadback {
    sddl: String,
    protected: bool,
    explicit: bool,
    world: bool,
}
fn inspect_dacl(bytes: &[u8], case: &str) -> AclReadback {
    let sd = bytes.as_ptr().cast_mut().cast();
    let (mut control, mut revision) = (0, 0);
    assert_ne!(
        unsafe { GetSecurityDescriptorControl(sd, &mut control, &mut revision) },
        0,
        "{case}: descriptor control"
    );
    let mut text = ptr::null_mut();
    let mut length = 0;
    assert_ne!(
        unsafe {
            ConvertSecurityDescriptorToStringSecurityDescriptorW(
                sd,
                1,
                DACL_SECURITY_INFORMATION,
                &mut text,
                &mut length,
            )
        },
        0,
        "{case}: SDDL readback"
    );
    assert!(
        length > 0 && !text.is_null(),
        "{case}: nonempty SDDL buffer"
    );
    let units = unsafe { std::slice::from_raw_parts(text, length as usize) };
    let end = units
        .iter()
        .position(|c| *c == 0)
        .unwrap_or_else(|| panic!("{case}: terminated SDDL"));
    let sddl = String::from_utf16(&units[..end]);
    unsafe {
        LocalFree(text.cast());
    }
    let sddl = sddl.unwrap_or_else(|e| panic!("{case}: SDDL encoding: {e}"));
    let (mut present, mut defaulted) = (0, 0);
    let mut acl = ptr::null_mut();
    assert_ne!(
        unsafe { GetSecurityDescriptorDacl(sd, &mut present, &mut acl, &mut defaulted) },
        0,
        "{case}, sddl={sddl}: DACL readback"
    );
    assert!(
        present != 0 && !acl.is_null(),
        "{case}, sddl={sddl}: mandatory DACL"
    );
    let acl = NonNull::new(acl).unwrap_or_else(|| panic!("{case}, sddl={sddl}: mandatory DACL"));
    assert_ne!(
        unsafe { IsValidAcl(acl.as_ptr()) },
        0,
        "{case}, sddl={sddl}: valid DACL"
    );
    let (mut explicit, mut world) = (false, false);
    for index in 0..unsafe { acl.as_ref().AceCount } {
        let mut raw = ptr::null_mut();
        assert_ne!(
            unsafe { GetAce(acl.as_ptr(), u32::from(index), &mut raw) },
            0,
            "{case}, sddl={sddl}: ACE readback"
        );
        assert!(!raw.is_null(), "{case}, sddl={sddl}: mandatory ACE");
        let ace = NonNull::new(raw)
            .unwrap_or_else(|| panic!("{case}, sddl={sddl}: mandatory ACE"))
            .cast::<ACCESS_ALLOWED_ACE>();
        let header = unsafe { ace.cast::<ACE_HEADER>().as_ref() };
        assert_eq!(
            header.AceType, 0,
            "{case}, sddl={sddl}: ordinary allow ACE fixture"
        );
        assert!(
            usize::from(header.AceSize) >= std::mem::size_of::<ACCESS_ALLOWED_ACE>(),
            "{case}, sddl={sddl}: complete ACE"
        );
        explicit |= header.AceFlags & 0x10 == 0;
        let sid = unsafe { ptr::addr_of_mut!((*ace.as_ptr()).SidStart).cast() };
        world |= header.AceFlags & 8 == 0 && unsafe { IsWellKnownSid(sid, WinWorldSid) } != 0;
    }
    AclReadback {
        sddl,
        protected: control & SE_DACL_PROTECTED != 0,
        explicit,
        world,
    }
}
pub(super) fn set_dacl(path: &Path, broad: bool) {
    let file = path.is_file();
    let sddl = if broad {
        "D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FR;;;WD)"
    } else {
        "D:(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)"
    };
    let case =
        format!("file={file}, broad={broad}, requested_sddl={sddl}, reader=not run (fixture)");
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
        0,
        "{case}: descriptor construction"
    );
    let (mut present, mut defaulted) = (0, 0);
    let mut acl = ptr::null_mut();
    assert_ne!(
        unsafe { GetSecurityDescriptorDacl(descriptor, &mut present, &mut acl, &mut defaulted) },
        0,
        "{case}: source DACL"
    );
    assert!(
        present != 0 && !acl.is_null(),
        "{case}: source DACL must be present"
    );
    let acl = NonNull::new(acl).unwrap_or_else(|| panic!("{case}: source DACL must be present"));
    let flags = DACL_SECURITY_INFORMATION
        | if broad {
            PROTECTED_DACL_SECURITY_INFORMATION
        } else {
            UNPROTECTED_DACL_SECURITY_INFORMATION
        };
    // Protection flags are set through the API that applies inheritance state.
    let result = unsafe {
        SetNamedSecurityInfoW(
            name.as_ptr(),
            SE_FILE_OBJECT,
            flags,
            ptr::null_mut(),
            ptr::null_mut(),
            acl.as_ptr(),
            ptr::null_mut(),
        )
    };
    unsafe {
        LocalFree(descriptor);
    }
    assert_eq!(result, 0, "{case}: mandatory ACL fixture construction");
    let readback = inspect_dacl(&dacl_case(path, &case), &case);
    let case = format!(
        "file={file}, broad={broad}, sddl={}, reader=not run (fixture)",
        readback.sddl
    );
    if broad {
        assert!(
            readback.protected && readback.world,
            "{case}: fixture must be protected with an effective WD ACE"
        );
    } else {
        assert!(
            !readback.protected && readback.explicit,
            "{case}: fixture must be unprotected with an explicit ACE"
        );
    }
}
#[test]
fn windows_startup_role_reader_refuses_broad_or_unprotected_directory_and_explicit_leaf_without_repair(
) {
    for file in [false, true] {
        for broad in [false, true] {
            let f = Fixture::new();
            let path = startup_role::record_path(&f.data);
            let original = std::fs::read(&path).unwrap_or_else(|e| {
                panic!("file={file}, broad={broad}, reader=not run: original record: {e}")
            });
            let target = if file {
                path.clone()
            } else {
                path.parent().unwrap().into()
            };
            set_dacl(&target, broad);
            let fixture = format!("file={file}, broad={broad}, reader=not run (fixture)");
            let before = dacl_case(&target, &fixture);
            let readback = inspect_dacl(&before, &fixture);
            let result = startup_role::evidence(&f.data).map(|e| e.snapshot);
            let case = format!(
                "file={file}, broad={broad}, sddl={}, reader={result:?}",
                readback.sddl
            );
            assert!(
                matches!(&result, Err(e) if e == startup_role::UNREADABLE),
                "{case}"
            );
            let bytes =
                std::fs::read(&path).unwrap_or_else(|e| panic!("{case}: record readback: {e}"));
            assert_eq!(bytes, original, "{case}: record bytes must not change");
            let after = dacl_case(&target, &case);
            let after_readback = inspect_dacl(&after, &case);
            assert_eq!(
                after, before,
                "{case}, after_sddl={}: reader must not repair the ACL",
                after_readback.sddl
            );
        }
    }
}
