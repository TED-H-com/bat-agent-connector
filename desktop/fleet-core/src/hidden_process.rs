//! Windowless child constructors. Callers retain argv, IO and lifetime policy.
use std::{ffi::OsStr, process::Command};

pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Preserve caller-supplied priority, Unicode environment and breakaway flags.
pub const fn hidden_flags(flags: u32) -> u32 {
    flags | CREATE_NO_WINDOW
}

pub fn hidden_command(program: impl AsRef<OsStr>) -> Command {
    #[allow(unused_mut)]
    let mut command = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(hidden_flags(0));
    }
    command
}

pub fn hidden_tokio_command(program: impl AsRef<OsStr>) -> tokio::process::Command {
    tokio::process::Command::from(hidden_command(program))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preserves_existing_flags() {
        assert_eq!(hidden_flags(0x0100_0400), 0x0900_0400);
        assert_eq!(hidden_flags(CREATE_NO_WINDOW), CREATE_NO_WINDOW);
    }
    #[test]
    fn portable_constructors_preserve_program_and_arguments() {
        let mut command = hidden_command("example");
        command.arg("argument");
        assert_eq!(command.get_program(), "example");
        assert_eq!(command.get_args().collect::<Vec<_>>(), ["argument"]);
        assert_eq!(
            hidden_tokio_command("example").as_std().get_program(),
            "example"
        );
    }
    #[cfg(windows)]
    #[test]
    fn child_has_no_console() {
        let result = hidden_command("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command",
                "Add-Type -MemberDefinition '[DllImport(\"kernel32.dll\")] public static extern System.IntPtr GetConsoleWindow();' -Name Native -Namespace Test; if ([Test.Native]::GetConsoleWindow() -ne [IntPtr]::Zero) { exit 1 }"])
            .status().unwrap();
        assert!(result.success());
    }
}
