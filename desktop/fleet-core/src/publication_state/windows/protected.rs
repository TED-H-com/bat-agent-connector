use super::{security, sid_text, wide, Allocation};
use crate::{
    publication_state::{acl, UNPROVEN},
    Result,
};
use std::{
    fs::File,
    os::windows::io::{AsRawHandle, FromRawHandle},
    path::Path,
    ptr::{self, NonNull},
};
use windows_sys::Win32::{
    Foundation::{GENERIC_READ, GENERIC_WRITE, INVALID_HANDLE_VALUE},
    Security::{
        Authorization::{GetSecurityInfo, SE_FILE_OBJECT},
        GetAce, GetLengthSid, GetSecurityDescriptorControl, IsValidAcl, IsValidSid,
        LookupAccountNameW, ACCESS_ALLOWED_ACE, ACE_HEADER, ACL, DACL_SECURITY_INFORMATION,
        OWNER_SECURITY_INFORMATION, PSID, SE_DACL_PROTECTED,
    },
    Storage::FileSystem::{
        CreateDirectoryW, CreateFileW, GetFileInformationByHandle, GetFileType,
        BY_HANDLE_FILE_INFORMATION, CREATE_NEW, FILE_ATTRIBUTE_NORMAL,
        FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
        FILE_READ_ATTRIBUTES, FILE_TYPE_DISK, OPEN_EXISTING, READ_CONTROL,
    },
};
pub(in crate::publication_state) fn open_no_follow(
    path: &Path,
    read: bool,
    share_mode: u32,
) -> std::io::Result<File> {
    let name = wide(path).map_err(|_| std::io::Error::from(std::io::ErrorKind::InvalidInput))?;
    let handle = unsafe {
        CreateFileW(
            name.as_ptr(),
            READ_CONTROL | FILE_READ_ATTRIBUTES | if read { GENERIC_READ } else { 0 },
            share_mode,
            ptr::null(),
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
            ptr::null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        return Err(std::io::Error::last_os_error());
    }
    Ok(unsafe { File::from_raw_handle(handle) })
}
pub(in crate::publication_state) fn new_directory(path: &Path) -> Result<()> {
    security(|attrs| {
        let name = wide(path)?;
        if unsafe { CreateDirectoryW(name.as_ptr(), attrs) } == 0 {
            return Err(UNPROVEN);
        }
        Ok(())
    })
}
pub(in crate::publication_state) fn new_file(path: &Path) -> Result<File> {
    security(|attrs| {
        let name = wide(path)?;
        let handle = unsafe {
            CreateFileW(
                name.as_ptr(),
                GENERIC_READ | GENERIC_WRITE | READ_CONTROL,
                0,
                attrs,
                CREATE_NEW,
                FILE_FLAG_OPEN_REPARSE_POINT | FILE_ATTRIBUTE_NORMAL,
                ptr::null_mut(),
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(UNPROVEN);
        }
        Ok(unsafe { File::from_raw_handle(handle) })
    })
}
fn trusted_installer() -> Option<String> {
    let name: Vec<u16> = "NT SERVICE\\TrustedInstaller"
        .encode_utf16()
        .chain(Some(0))
        .collect();
    let (mut sid_size, mut domain_size, mut use_kind) = (0, 0, 0);
    unsafe {
        LookupAccountNameW(
            ptr::null(),
            name.as_ptr(),
            ptr::null_mut(),
            &mut sid_size,
            ptr::null_mut(),
            &mut domain_size,
            &mut use_kind,
        );
    }
    if sid_size == 0 || sid_size > 68 || domain_size > 256 {
        return None;
    }
    // u32 storage keeps the SID DWORD aligned.
    let mut sid = [0u32; 17];
    let mut domain = [0u16; 256];
    if unsafe {
        LookupAccountNameW(
            ptr::null(),
            name.as_ptr(),
            sid.as_mut_ptr().cast(),
            &mut sid_size,
            domain.as_mut_ptr(),
            &mut domain_size,
            &mut use_kind,
        )
    } == 0
    {
        return None;
    }
    sid_text(sid.as_mut_ptr().cast()).ok()
}
pub(in crate::publication_state) fn acl_file(
    file: &File,
    private: bool,
    child_absent: bool,
) -> Result<()> {
    acl_file_policy(file, private, child_absent, false)
}
pub(in crate::publication_state) fn role_acl_file(file: &File) -> Result<()> {
    acl_file_policy(file, true, false, true)
}
fn acl_file_policy(
    file: &File,
    private: bool,
    child_absent: bool,
    inherited_leaf: bool,
) -> Result<()> {
    let handle = file.as_raw_handle();
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    if unsafe { GetFileType(handle) } != FILE_TYPE_DISK
        || unsafe { GetFileInformationByHandle(handle, &mut info) } == 0
        || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
    {
        return Err(UNPROVEN);
    }
    let mut owner = ptr::null_mut();
    let mut dacl: *mut ACL = ptr::null_mut();
    let mut descriptor = ptr::null_mut();
    if unsafe {
        GetSecurityInfo(
            handle,
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &mut owner,
            ptr::null_mut(),
            &mut dacl,
            ptr::null_mut(),
            &mut descriptor,
        )
    } != 0
    {
        return Err(UNPROVEN);
    }
    let _allocation = Allocation(descriptor);
    let dacl = NonNull::new(dacl).ok_or(UNPROVEN)?;
    if unsafe { IsValidAcl(dacl.as_ptr()) } == 0 {
        return Err(UNPROVEN);
    }
    let (mut control, mut revision) = (0, 0);
    if unsafe { GetSecurityDescriptorControl(descriptor, &mut control, &mut revision) } == 0 {
        return Err(UNPROVEN);
    }
    let count = unsafe { dacl.as_ref().AceCount };
    if count == 0 || count > 64 {
        return Err(UNPROVEN);
    }
    let mut entries = Vec::with_capacity(count as usize);
    for index in 0..count {
        let mut raw = ptr::null_mut();
        if unsafe { GetAce(dacl.as_ptr(), u32::from(index), &mut raw) } == 0 {
            return Err(UNPROVEN);
        }
        let ace = NonNull::new(raw)
            .ok_or(UNPROVEN)?
            .cast::<ACCESS_ALLOWED_ACE>();
        let header = unsafe { *ace.cast::<ACE_HEADER>().as_ref() };
        // Do not interpret SID layout for object/callback/unknown ACE forms.
        if header.AceType != 0
            || usize::from(header.AceSize) < std::mem::offset_of!(ACCESS_ALLOWED_ACE, SidStart) + 8
        {
            return Err(UNPROVEN);
        }
        let sid: PSID = unsafe { ptr::addr_of_mut!((*ace.as_ptr()).SidStart).cast() };
        let subauthorities = unsafe { *sid.cast::<u8>().add(1) };
        if subauthorities > 15
            || 8 + 4 * usize::from(subauthorities)
                + std::mem::offset_of!(ACCESS_ALLOWED_ACE, SidStart)
                > usize::from(header.AceSize)
        {
            return Err(UNPROVEN);
        }
        if unsafe { IsValidSid(sid) } == 0
            || unsafe { GetLengthSid(sid) } as usize
                + std::mem::offset_of!(ACCESS_ALLOWED_ACE, SidStart)
                > usize::from(header.AceSize)
        {
            return Err(UNPROVEN);
        }
        entries.push(acl::Entry {
            kind: header.AceType,
            flags: header.AceFlags,
            mask: unsafe { ace.as_ref().Mask },
            sid: sid_text(sid)?,
        });
    }
    let current = crate::windows::current_login()?.owner_sid;
    let installer = if private { None } else { trusted_installer() };
    acl::validate(
        &sid_text(owner)?,
        control & SE_DACL_PROTECTED != 0
            || (inherited_leaf && entries.iter().all(|e| e.flags & 0x10 != 0)),
        &entries,
        acl::Policy {
            current: &current,
            trusted_installer: installer.as_deref(),
            private,
            child_absent,
        },
    )
}
