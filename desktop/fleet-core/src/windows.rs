//! Windows OS boundary. Native-only trusted inputs, fixed APIs, no PowerShell subprocesses.
//! A held handle identifies a process object even if its numeric PID is later reused.
use crate::ownership::{monitor_mutex, ProcessState};
use crate::process_adapter::{
    self, HeldProcess, LoginIdentity, ProcessAccess, ProcessSnapshot, StopMode, StopOutcome,
    TunnelRecord,
};
use crate::Result;
use std::{
    ffi::c_void,
    fs::{File, OpenOptions},
    marker::PhantomData,
    mem::{size_of, zeroed},
    os::windows::fs::OpenOptionsExt,
    path::Path,
    ptr::{null, null_mut},
    rc::Rc,
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::*,
    Security::{Authorization::ConvertSidToStringSidW, *},
    System::{
        Diagnostics::ToolHelp::*,
        LibraryLoader::{GetModuleHandleW, GetProcAddress},
        Threading::*,
    },
    UI::Shell::CommandLineToArgvW,
};

struct Handle(HANDLE);
impl Handle {
    fn new(h: HANDLE) -> Result<Self> {
        if h.is_null() || h == INVALID_HANDLE_VALUE {
            Err("OWNER_UNPROVEN")
        } else {
            Ok(Self(h))
        }
    }
}
impl Drop for Handle {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}
struct LocalAllocation(*mut c_void);
impl Drop for LocalAllocation {
    fn drop(&mut self) {
        unsafe {
            LocalFree(self.0);
        }
    }
}
fn wide(value: &str) -> Result<Vec<u16>> {
    if value.is_empty() || value.contains('\0') || value.encode_utf16().count() > 32767 {
        return Err("OWNER_UNPROVEN");
    }
    Ok(value.encode_utf16().chain(Some(0)).collect())
}

fn login(handle: HANDLE) -> Result<LoginIdentity> {
    unsafe {
        let mut token = null_mut();
        if OpenProcessToken(handle, TOKEN_QUERY, &mut token) == 0 {
            return Err("OWNER_UNPROVEN");
        }
        let token = Handle::new(token)?;
        let mut count = 0;
        GetTokenInformation(token.0, TokenUser, null_mut(), 0, &mut count);
        if count < size_of::<TOKEN_USER>() as u32 || count > 65536 {
            return Err("OWNER_UNPROVEN");
        }
        let mut buf = vec![0usize; (count as usize).div_ceil(size_of::<usize>())];
        let mut actual = 0;
        if GetTokenInformation(
            token.0,
            TokenUser,
            buf.as_mut_ptr().cast(),
            count,
            &mut actual,
        ) == 0
            || actual > count
        {
            return Err("OWNER_UNPROVEN");
        }
        let user = &*buf.as_ptr().cast::<TOKEN_USER>();
        let mut sid = null_mut();
        if ConvertSidToStringSidW(user.User.Sid, &mut sid) == 0 {
            return Err("OWNER_UNPROVEN");
        }
        let _sid = LocalAllocation(sid.cast());
        let mut len = 0;
        while len <= 184 && *sid.add(len) != 0 {
            len += 1;
        }
        if len > 184 {
            return Err("OWNER_UNPROVEN");
        }
        let owner_sid = String::from_utf16(std::slice::from_raw_parts(sid, len))
            .map_err(|_| "OWNER_UNPROVEN")?;
        let mut session_id = 0u32;
        let mut got = 0;
        if GetTokenInformation(
            token.0,
            TokenSessionId,
            (&mut session_id as *mut u32).cast(),
            size_of::<u32>() as u32,
            &mut got,
        ) == 0
            || got != 4
        {
            return Err("OWNER_UNPROVEN");
        }
        let value = LoginIdentity {
            owner_sid,
            session_id,
        };
        if !value.valid() {
            return Err("OWNER_UNPROVEN");
        }
        Ok(value)
    }
}
pub fn current_login() -> Result<LoginIdentity> {
    login(unsafe { GetCurrentProcess() })
}

fn creation_time(handle: HANDLE) -> Result<u64> {
    unsafe {
        let mut birth: FILETIME = zeroed();
        let mut exit: FILETIME = zeroed();
        let mut kernel: FILETIME = zeroed();
        let mut user: FILETIME = zeroed();
        if GetProcessTimes(handle, &mut birth, &mut exit, &mut kernel, &mut user) == 0 {
            return Err("OWNER_UNPROVEN");
        }
        let result = (u64::from(birth.dwHighDateTime) << 32) | u64::from(birth.dwLowDateTime);
        process_adapter::datetime_ticks(result)?;
        Ok(result)
    }
}
fn running(handle: HANDLE) -> Result<bool> {
    match unsafe { WaitForSingleObject(handle, 0) } {
        WAIT_TIMEOUT => Ok(true),
        WAIT_OBJECT_0 => Ok(false),
        _ => Err("OWNER_UNPROVEN"),
    }
}
fn executable(handle: HANDLE) -> Result<String> {
    let mut buf = vec![0u16; 32768];
    let mut count = buf.len() as u32;
    if unsafe { QueryFullProcessImageNameW(handle, 0, buf.as_mut_ptr(), &mut count) } == 0
        || count == 0
        || count >= buf.len() as u32
    {
        return Err("OWNER_UNPROVEN");
    }
    String::from_utf16(&buf[..count as usize]).map_err(|_| "OWNER_UNPROVEN")
}
#[repr(C)]
struct UnicodeString {
    length: u16,
    maximum_length: u16,
    buffer: *const u16,
}
type NtQuery = unsafe extern "system" fn(HANDLE, i32, *mut c_void, u32, *mut u32) -> i32;

/// Class60 returns the process command line for this held handle. Dynamically resolved,
/// bounded, and fail-closed: this internal NT API is not an unconditional platform guarantee.
fn arguments(handle: HANDLE) -> Result<Vec<String>> {
    unsafe {
        let module = GetModuleHandleW(wide("ntdll.dll")?.as_ptr());
        if module.is_null() {
            return Err("OWNER_UNPROVEN");
        }
        let function = GetProcAddress(module, c"NtQueryInformationProcess".as_ptr().cast())
            .ok_or("OWNER_UNPROVEN")?;
        let query: NtQuery = std::mem::transmute(function);
        let mut needed = 0u32;
        query(handle, 60, null_mut(), 0, &mut needed);
        const BOUND: usize = 65536 + 64;
        if (needed as usize) < size_of::<UnicodeString>() || needed as usize > BOUND {
            return Err("OWNER_UNPROVEN");
        }
        // Extra space permits a command line that grows once; a further change is unknown.
        let mut buf = vec![0usize; BOUND.div_ceil(size_of::<usize>())];
        let mut got = 0;
        if query(handle, 60, buf.as_mut_ptr().cast(), BOUND as u32, &mut got) < 0
            || got as usize > BOUND
            || (got as usize) < size_of::<UnicodeString>()
        {
            return Err("OWNER_UNPROVEN");
        }
        let text = &*buf.as_ptr().cast::<UnicodeString>();
        let begin = buf.as_ptr() as usize;
        let end = begin + got as usize;
        let addr = text.buffer as usize;
        let length = text.length as usize;
        if length == 0
            || !length.is_multiple_of(2)
            || text.maximum_length < text.length
            || !addr.is_multiple_of(2)
            || addr < begin + size_of::<UnicodeString>()
            || addr.checked_add(length).is_none_or(|n| n > end)
        {
            return Err("OWNER_UNPROVEN");
        }
        let raw = std::slice::from_raw_parts(text.buffer, length / 2);
        if raw.contains(&0) {
            return Err("OWNER_UNPROVEN");
        }
        let mut terminated = raw.to_vec();
        terminated.push(0);
        let mut argc = 0;
        let argv = CommandLineToArgvW(terminated.as_ptr(), &mut argc);
        if argv.is_null() {
            return Err("OWNER_UNPROVEN");
        }
        let _argv = LocalAllocation(argv.cast());
        if !(1..=129).contains(&argc) {
            return Err("OWNER_UNPROVEN");
        }
        let mut out = Vec::new();
        for i in 1..argc as usize {
            let p = *argv.add(i);
            let mut n = 0;
            while n < 32768 && *p.add(n) != 0 {
                n += 1;
            }
            if n == 32768 {
                return Err("OWNER_UNPROVEN");
            }
            out.push(
                String::from_utf16(std::slice::from_raw_parts(p, n))
                    .map_err(|_| "OWNER_UNPROVEN")?,
            );
        }
        Ok(out)
    }
}

/// Complete Toolhelp enumeration may prove absence. Access/query errors never do.
fn proven_absent(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    unsafe {
        let Ok(snapshot) = Handle::new(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)) else {
            return false;
        };
        let mut row: PROCESSENTRY32W = zeroed();
        row.dwSize = size_of::<PROCESSENTRY32W>() as u32;
        if Process32FirstW(snapshot.0, &mut row) == 0 {
            return GetLastError() == ERROR_NO_MORE_FILES;
        }
        for _ in 0..100_000 {
            if row.th32ProcessID == pid {
                return false;
            }
            if Process32NextW(snapshot.0, &mut row) == 0 {
                return GetLastError() == ERROR_NO_MORE_FILES;
            }
        }
        false
    }
}

pub struct WindowsProcess {
    handle: Handle,
}
impl WindowsProcess {
    /// Duplicate the retained launch handle, never reopen the child's numeric PID. This
    /// also permits safe rollback if initial ownership-record persistence fails.
    pub fn from_child(child: &std::process::Child) -> Result<Self> {
        use std::os::windows::io::AsRawHandle;
        let mut duplicate = null_mut();
        unsafe {
            let current = GetCurrentProcess();
            if DuplicateHandle(
                current,
                child.as_raw_handle(),
                current,
                &mut duplicate,
                0,
                0,
                DUPLICATE_SAME_ACCESS,
            ) == 0
            {
                return Err("OWNER_UNPROVEN");
            }
        }
        Ok(Self {
            handle: Handle::new(duplicate)?,
        })
    }
    fn open(pid: u32, stop: bool) -> Result<Option<Self>> {
        if pid == 0 {
            return Err("OWNER_UNPROVEN");
        }
        let access = PROCESS_QUERY_LIMITED_INFORMATION
            | PROCESS_SYNCHRONIZE
            | if stop { PROCESS_TERMINATE } else { 0 };
        let raw = unsafe { OpenProcess(access, 0, pid) };
        if raw.is_null() {
            // ERROR_INVALID_PARAMETER is not by itself evidence of death.
            return if unsafe { GetLastError() } == ERROR_INVALID_PARAMETER && proven_absent(pid) {
                Ok(None)
            } else {
                Err("OWNER_UNPROVEN")
            };
        }
        let handle = Handle::new(raw)?;
        if !running(handle.0)? {
            return Ok(None);
        }
        Ok(Some(Self { handle }))
    }
    pub fn observe(pid: u32) -> Result<Option<ProcessSnapshot>> {
        Self::open(pid, false)?.map(|p| p.snapshot()).transpose()
    }
}
impl HeldProcess for WindowsProcess {
    fn snapshot(&self) -> Result<ProcessSnapshot> {
        if !running(self.handle.0)? {
            return Err("OWNER_UNPROVEN");
        }
        let result = ProcessSnapshot {
            pid: unsafe { GetProcessId(self.handle.0) },
            created_filetime: creation_time(self.handle.0)?,
            executable: executable(self.handle.0)?,
            arguments: arguments(self.handle.0)?,
            login: login(self.handle.0)?,
        };
        if !result.valid() || !running(self.handle.0)? {
            return Err("OWNER_UNPROVEN");
        }
        Ok(result)
    }
    fn terminate_and_wait(&mut self) -> Result<()> {
        if !running(self.handle.0)? {
            return Ok(());
        }
        if unsafe { TerminateProcess(self.handle.0, 1) } == 0 {
            return Err("STOP_UNCONFIRMED");
        }
        if unsafe { WaitForSingleObject(self.handle.0, 2000) } != WAIT_OBJECT_0 {
            return Err("STOP_UNCONFIRMED");
        }
        Ok(())
    }
}
struct WindowsAccess;
impl ProcessAccess for WindowsAccess {
    type Held = WindowsProcess;
    fn current_login(&self) -> Result<LoginIdentity> {
        current_login()
    }
    fn open_for_stop(&self, pid: u32) -> Result<Option<Self::Held>> {
        WindowsProcess::open(pid, true)
    }
    fn state(&self, pid: u32) -> ProcessState {
        match WindowsProcess::open(pid, false) {
            Ok(None) => ProcessState::Dead,
            Ok(Some(p)) => {
                match creation_time(p.handle.0).and_then(process_adapter::legacy_created) {
                    Ok(created) => ProcessState::Live { created },
                    Err(_) => ProcessState::Unknown,
                }
            }
            Err(_) => ProcessState::Unknown,
        }
    }
}
pub fn process_state(pid: u32) -> ProcessState {
    WindowsAccess.state(pid)
}

thread_local! {
    static HELD_MUTEXES: std::cell::RefCell<std::collections::HashSet<String>> = Default::default();
}
/// The Windows mutex is recursive and thread-owned. Keep this guard on one dedicated
/// supervisor thread; it deliberately cannot move between async executor threads.
pub struct MonitorMutex {
    name: String,
    handle: Handle,
    login: LoginIdentity,
    _thread: PhantomData<Rc<()>>,
}
impl MonitorMutex {
    pub fn try_acquire() -> Result<Option<Self>> {
        let identity = current_login()?;
        Self::named(&monitor_mutex(&identity.owner_sid)?, identity)
    }
    fn named(name: &str, login: LoginIdentity) -> Result<Option<Self>> {
        if HELD_MUTEXES.with(|held| held.borrow().contains(name)) {
            return Ok(None);
        }
        let encoded = wide(name)?;
        let handle = Handle::new(unsafe { CreateMutexW(null(), 0, encoded.as_ptr()) })?;
        match unsafe { WaitForSingleObject(handle.0, 0) } {
            WAIT_OBJECT_0 | WAIT_ABANDONED => {
                HELD_MUTEXES.with(|held| held.borrow_mut().insert(name.into()));
                Ok(Some(Self {
                    name: name.into(),
                    handle,
                    login,
                    _thread: PhantomData,
                }))
            }
            WAIT_TIMEOUT => Ok(None),
            _ => Err("OWNER_UNPROVEN"),
        }
    }
    pub fn login(&self) -> &LoginIdentity {
        &self.login
    }
    /// Caller already revalidated config/record and holds the sole supervisor guard.
    pub fn stop_tunnel(&self, record: &TunnelRecord, mode: StopMode<'_>) -> Result<StopOutcome> {
        if self.login != current_login()? {
            return Err("OTHER_LOGIN_OWNER");
        }
        process_adapter::stop_tunnel(&WindowsAccess, record, mode)
    }
}
impl Drop for MonitorMutex {
    fn drop(&mut self) {
        unsafe {
            ReleaseMutex(self.handle.0);
        }
        HELD_MUTEXES.with(|held| held.borrow_mut().remove(&self.name));
    }
}

/// Same `.lock` sidecar and zero sharing as File.Open(..., FileShare.None).
/// Do not unlink this file: replacement would allow two unrelated lock handles.
pub struct PreferenceLock {
    _file: File,
}
impl PreferenceLock {
    pub fn try_acquire(preference_path: &Path) -> Result<Option<Self>> {
        if !preference_path.is_absolute() || preference_path.file_name().is_none() {
            return Err("SELECTION_LOCK_FAILED");
        }
        let mut sidecar = preference_path.as_os_str().to_os_string();
        sidecar.push(".lock");
        match OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .share_mode(0)
            .open(Path::new(&sidecar))
        {
            Ok(file) => Ok(Some(Self { _file: file })),
            Err(e) if e.raw_os_error() == Some(ERROR_SHARING_VIOLATION as i32) => Ok(None),
            Err(_) => Err("SELECTION_LOCK_FAILED"),
        }
    }
    pub fn acquire(preference_path: &Path) -> Result<Self> {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if let Some(guard) = Self::try_acquire(preference_path)? {
                return Ok(guard);
            }
            let Some(left) = deadline.checked_duration_since(Instant::now()) else {
                return Err("SELECTION_BUSY");
            };
            std::thread::sleep(left.min(Duration::from_millis(20)));
        }
    }
}

/// OS observer for read-only discovery. No process enumeration occurs until explicitly called.
pub struct WindowsMonitorObservation;
impl crate::discovery::Observation for WindowsMonitorObservation {
    fn current_login(&self) -> Result<LoginIdentity> {
        current_login()
    }
    fn state(&self, pid: u32) -> ProcessState {
        process_state(pid)
    }
    fn observe(&self, pid: u32) -> Result<Option<ProcessSnapshot>> {
        WindowsProcess::observe(pid)
    }
    fn legacy_candidates(&self) -> Result<Vec<ProcessSnapshot>> {
        let owner = current_login()?;
        let mut result = Vec::new();
        for pid in powershell_pids()? {
            let snapshot = (|| {
                let Some(process) = WindowsProcess::open(pid, false)? else {
                    return Ok(None);
                };
                if login(process.handle.0)?.owner_sid != owner.owner_sid {
                    return Ok(None);
                }
                process.snapshot().map(Some)
            })();
            if let Some(snapshot) = crate::discovery::candidate(snapshot) {
                result.push(snapshot);
            }
            if result.len() > 1024 {
                return Err("OWNER_UNPROVEN");
            }
        }
        Ok(result)
    }
}
fn powershell_pids() -> Result<Vec<u32>> {
    unsafe {
        let snapshot = Handle::new(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0))?;
        let mut row: PROCESSENTRY32W = zeroed();
        row.dwSize = size_of::<PROCESSENTRY32W>() as u32;
        if Process32FirstW(snapshot.0, &mut row) == 0 {
            return if GetLastError() == ERROR_NO_MORE_FILES {
                Ok(vec![])
            } else {
                Err("OWNER_UNPROVEN")
            };
        }
        let mut pids = Vec::new();
        for _ in 0..100_000 {
            let length = row
                .szExeFile
                .iter()
                .position(|n| *n == 0)
                .ok_or("OWNER_UNPROVEN")?;
            let name =
                String::from_utf16(&row.szExeFile[..length]).map_err(|_| "OWNER_UNPROVEN")?;
            if name.eq_ignore_ascii_case("powershell.exe") || name.eq_ignore_ascii_case("pwsh.exe")
            {
                pids.push(row.th32ProcessID);
            }
            if Process32NextW(snapshot.0, &mut row) == 0 {
                return if GetLastError() == ERROR_NO_MORE_FILES {
                    Ok(pids)
                } else {
                    Err("OWNER_UNPROVEN")
                };
            }
        }
        Err("OWNER_UNPROVEN")
    }
}

#[cfg(test)]
mod tests;
