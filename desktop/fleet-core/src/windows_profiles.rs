//! Fixed Windows BAT discovery and process creation; no manual process is stopped.
use crate::{
    installation::{canonical_local, Snapshot},
    process_adapter::{HeldProcess, LoginIdentity, ProcessSnapshot},
    profile_launch::{Executable, Platform},
    tunnel::SpawnFailure,
    windows::{current_login, WindowsProcess},
    windows_launcher::LauncherMutex,
    Result,
};
use std::{
    mem::{size_of, zeroed},
    os::windows::{ffi::OsStringExt, process::CommandExt},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    ptr::null_mut,
};
use windows_sys::{
    core::GUID,
    Win32::{
        Foundation::{
            CloseHandle, GetLastError, ERROR_NO_MORE_FILES, HANDLE, INVALID_HANDLE_VALUE,
        },
        System::{
            Com::CoTaskMemFree,
            Diagnostics::ToolHelp::{
                CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
                TH32CS_SNAPPROCESS,
            },
            Threading::CREATE_NO_WINDOW,
        },
        UI::Shell::{FOLDERID_LocalAppData, FOLDERID_ProgramFiles, SHGetKnownFolderPath},
    },
};

fn known_folder(id: &GUID) -> Result<PathBuf> {
    let mut raw = null_mut();
    let result = unsafe { SHGetKnownFolderPath(id, 0, null_mut(), &mut raw) };
    let path = (|| {
        if result < 0 || raw.is_null() {
            return Err("BAT_EXECUTABLE_UNAVAILABLE");
        }
        let mut len = 0;
        while len < 32768 && unsafe { *raw.add(len) } != 0 {
            len += 1;
        }
        if len == 32768 {
            return Err("BAT_EXECUTABLE_UNPROVEN");
        }
        let value = std::ffi::OsString::from_wide(unsafe { std::slice::from_raw_parts(raw, len) });
        canonical_local(Path::new(&value))
    })();
    unsafe { CoTaskMemFree(raw.cast()) };
    path
}
fn candidates(program: &Path, local: &Path) -> [(PathBuf, PathBuf); 3] {
    [
        (
            program.into(),
            program.join("BetterAgentTerminal/BetterAgentTerminal.exe"),
        ),
        (
            local.into(),
            local.join("Programs/BetterAgentTerminal/BetterAgentTerminal.exe"),
        ),
        (
            local.into(),
            local.join("BetterAgentTerminal/BetterAgentTerminal.exe"),
        ),
    ]
}
pub fn discover_executable() -> Result<Executable> {
    for (root, path) in candidates(
        &known_folder(&FOLDERID_ProgramFiles)?,
        &known_folder(&FOLDERID_LocalAppData)?,
    ) {
        match path.symlink_metadata() {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(_) => return Err("BAT_EXECUTABLE_UNAVAILABLE"),
            Ok(meta) => {
                use std::os::windows::fs::MetadataExt;
                if !meta.is_file()
                    || meta.file_type().is_symlink()
                    || meta.file_attributes() & 0x400 != 0
                {
                    return Err("BAT_EXECUTABLE_UNPROVEN");
                }
                let path = canonical_local(&path)?;
                if !path.starts_with(&root) {
                    return Err("BAT_EXECUTABLE_UNPROVEN");
                }
                return Executable::read(&path);
            }
        }
    }
    Err("BAT_EXECUTABLE_UNAVAILABLE")
}
struct Handle(HANDLE);
impl Drop for Handle {
    fn drop(&mut self) {
        unsafe { CloseHandle(self.0) };
    }
}
fn bat_pids() -> Result<Vec<u32>> {
    unsafe {
        let raw = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if raw == INVALID_HANDLE_VALUE || raw.is_null() {
            return Err("BAT_PROCESS_UNPROVEN");
        }
        let handle = Handle(raw);
        let mut row: PROCESSENTRY32W = zeroed();
        row.dwSize = size_of::<PROCESSENTRY32W>() as u32;
        if Process32FirstW(handle.0, &mut row) == 0 {
            return if GetLastError() == ERROR_NO_MORE_FILES {
                Ok(vec![])
            } else {
                Err("BAT_PROCESS_UNPROVEN")
            };
        }
        let mut result = vec![];
        for _ in 0..100000 {
            let len = row
                .szExeFile
                .iter()
                .position(|n| *n == 0)
                .ok_or("BAT_PROCESS_UNPROVEN")?;
            let name =
                String::from_utf16(&row.szExeFile[..len]).map_err(|_| "BAT_PROCESS_UNPROVEN")?;
            if name.eq_ignore_ascii_case("BetterAgentTerminal.exe") {
                result.push(row.th32ProcessID);
            }
            if result.len() > 1024 {
                return Err("BAT_PROCESS_UNPROVEN");
            }
            if Process32NextW(handle.0, &mut row) == 0 {
                return if GetLastError() == ERROR_NO_MORE_FILES {
                    Ok(result)
                } else {
                    Err("BAT_PROCESS_UNPROVEN")
                };
            }
        }
        Err("BAT_PROCESS_UNPROVEN")
    }
}
fn command(
    executable: &Path,
    values: impl IntoIterator<Item = (std::ffi::OsString, std::ffi::OsString)>,
) -> Result<Command> {
    let mut command = crate::hidden_process::hidden_command(executable);
    command
        .current_dir(executable.parent().ok_or("BAT_EXECUTABLE_UNPROVEN")?)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(crate::hidden_process::hidden_flags(CREATE_NO_WINDOW))
        .env_clear();
    let values: Vec<_> = values.into_iter().collect();
    command.envs(crate::monitor_launch::environment(values.clone()));
    // Fixed shell/runtime variables are needed by BAT's local terminal; no app/proxy token is inherited.
    command.envs(values.into_iter().filter(|(key, _)| {
        key.to_str().is_some_and(|key| {
            [
                "COMSPEC",
                "PATHEXT",
                "PROGRAMFILES",
                "PROGRAMFILES(X86)",
                "PROGRAMW6432",
                "HOMEDRIVE",
                "HOMEPATH",
            ]
            .contains(&key.to_ascii_uppercase().as_str())
        })
    }));
    Ok(command)
}
pub struct WindowsProfiles<'a> {
    launcher: &'a LauncherMutex,
    installation: &'a Snapshot,
    child: Option<Child>,
}
impl<'a> WindowsProfiles<'a> {
    pub fn new(launcher: &'a LauncherMutex, installation: &'a Snapshot) -> Self {
        Self {
            launcher,
            installation,
            child: None,
        }
    }
}
impl Platform for WindowsProfiles<'_> {
    fn verify(&self) -> Result<()> {
        if &current_login()? != self.launcher.login() {
            return Err("OTHER_LOGIN_OWNER");
        }
        self.installation.verify_current()
    }
    fn login(&self) -> Result<LoginIdentity> {
        current_login()
    }
    fn executable(&self) -> Result<Executable> {
        self.verify()?;
        discover_executable()
    }
    fn running(&self) -> Result<Vec<ProcessSnapshot>> {
        self.verify()?;
        let login = current_login()?;
        let mut processes = vec![];
        for pid in bat_pids()? {
            if let Some(process) = WindowsProcess::observe(pid)? {
                if process.login.owner_sid == login.owner_sid {
                    processes.push(process);
                }
            }
        }
        Ok(processes)
    }
    fn observe(&self, pid: u32) -> Result<Option<ProcessSnapshot>> {
        WindowsProcess::observe(pid)
    }
    fn spawn(
        &mut self,
        executable: &Executable,
    ) -> std::result::Result<ProcessSnapshot, SpawnFailure> {
        self.verify().map_err(|_| SpawnFailure::NotStarted)?;
        executable.verify().map_err(|_| SpawnFailure::NotStarted)?;
        if !self
            .running()
            .map_err(|_| SpawnFailure::NotStarted)?
            .is_empty()
        {
            return Err(SpawnFailure::NotStarted);
        }
        let child = command(executable.path(), std::env::vars_os())
            .map_err(|_| SpawnFailure::NotStarted)?
            .spawn()
            .map_err(|_| SpawnFailure::NotStarted)?;
        self.child = Some(child);
        // Keep sent-effect uncertainty if BAT exits/relaunches before identity capture. Never kill it.
        WindowsProcess::from_child(self.child.as_ref().unwrap())
            .and_then(|p| p.snapshot())
            .map_err(|_| SpawnFailure::Unconfirmed)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixed_candidates_and_empty_argv_preserve_shell_runtime_without_tokens() {
        let paths = candidates(Path::new("C:\\Apps"), Path::new("C:\\User"));
        assert_eq!(
            paths[0].1,
            Path::new("C:\\Apps\\BetterAgentTerminal\\BetterAgentTerminal.exe")
        );
        assert_eq!(
            paths[1].1,
            Path::new("C:\\User\\Programs\\BetterAgentTerminal\\BetterAgentTerminal.exe")
        );
        assert_eq!(
            paths[2].1,
            Path::new("C:\\User\\BetterAgentTerminal\\BetterAgentTerminal.exe")
        );
        let cmd = command(
            Path::new("C:\\Apps\\BetterAgentTerminal.exe"),
            [
                ("PATH".into(), "C:\\Tools".into()),
                ("ComSpec".into(), "C:\\Windows\\cmd.exe".into()),
                ("BATC_DESKTOP_TOKEN".into(), "synthetic".into()),
                ("HTTPS_PROXY".into(), "synthetic".into()),
            ],
        )
        .unwrap();
        assert_eq!(cmd.get_args().count(), 0);
        assert_eq!(cmd.get_program(), "C:\\Apps\\BetterAgentTerminal.exe");
        let keys: Vec<_> = cmd
            .get_envs()
            .map(|(k, _)| k.to_string_lossy().to_ascii_uppercase())
            .collect();
        assert_eq!(keys.len(), 2);
        assert!(keys.contains(&"PATH".into()));
        assert!(keys.contains(&"COMSPEC".into()));
    }
}
