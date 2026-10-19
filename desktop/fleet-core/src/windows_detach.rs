//! Background children must leave a permitted launching job, not merely hide their console.
use std::io;

const BREAKAWAY: u32 = 0x01000000;

#[cfg(windows)]
fn allows_breakaway() -> bool {
    use windows_sys::Win32::System::{
        JobObjects::{
            IsProcessInJob, JobObjectBasicLimitInformation, QueryInformationJobObject,
            JOBOBJECT_BASIC_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_BREAKAWAY_OK,
            JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK,
        },
        Threading::GetCurrentProcess,
    };
    unsafe {
        let mut inside = 0;
        if IsProcessInJob(GetCurrentProcess(), std::ptr::null_mut(), &mut inside) == 0 {
            return false;
        }
        if inside == 0 {
            return true;
        }
        let mut limits: JOBOBJECT_BASIC_LIMIT_INFORMATION = std::mem::zeroed();
        QueryInformationJobObject(
            std::ptr::null_mut(),
            JobObjectBasicLimitInformation,
            (&mut limits as *mut JOBOBJECT_BASIC_LIMIT_INFORMATION).cast(),
            std::mem::size_of_val(&limits) as u32,
            std::ptr::null_mut(),
        ) != 0
            && limits.LimitFlags
                & (JOB_OBJECT_LIMIT_BREAKAWAY_OK | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK)
                != 0
    }
}

fn spawn_with<T>(
    allowed: bool,
    flags: u32,
    mut create: impl FnMut(u32) -> io::Result<T>,
    mut warn: impl FnMut(),
) -> io::Result<T> {
    if !allowed {
        warn();
    }
    match create(flags | if allowed { BREAKAWAY } else { 0 }) {
        Err(error) if allowed && error.raw_os_error() == Some(5) => {
            warn();
            create(flags)
        }
        result => result,
    }
}

#[cfg(windows)]
pub fn spawn(command: &mut std::process::Command, flags: u32) -> io::Result<std::process::Child> {
    use std::os::windows::process::CommandExt;
    spawn_with(
        allows_breakaway(),
        flags,
        |flags| command.creation_flags(flags).spawn(),
        || eprintln!("FLEET_SESSION_LIFETIME: supervisor may stop when the launching session ends"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn allowed_job_requests_breakaway_and_refusal_retries_once() {
        let mut attempts = vec![];
        let mut warnings = 0;
        spawn_with(
            true,
            0x08000000,
            |flags| {
                attempts.push(flags);
                if attempts.len() == 1 {
                    Err(io::Error::from_raw_os_error(5))
                } else {
                    Ok(())
                }
            },
            || warnings += 1,
        )
        .unwrap();
        assert_eq!(attempts, [0x09000000, 0x08000000]);
        assert_eq!(warnings, 1);
    }
    #[test]
    fn restrictive_job_uses_original_flags_and_other_errors_never_retry() {
        for allowed in [true, false] {
            let mut attempts = vec![];
            let mut warnings = 0;
            let result: io::Result<()> = spawn_with(
                allowed,
                0x08000000,
                |flags| {
                    attempts.push(flags);
                    Err(io::Error::from_raw_os_error(2))
                },
                || warnings += 1,
            );
            assert_eq!(result.unwrap_err().raw_os_error(), Some(2));
            assert_eq!(attempts, [if allowed { 0x09000000 } else { 0x08000000 }]);
            assert_eq!(warnings, usize::from(!allowed));
        }
    }
}
