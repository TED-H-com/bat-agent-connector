//! Native APIs only; no subprocess, command line query, WMI or CIM.
use super::*;
use std::mem::size_of;
use windows_sys::Win32::{
    Foundation::{CloseHandle, ERROR_NO_MORE_FILES, FILETIME, HANDLE, INVALID_HANDLE_VALUE},
    System::{
        Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
            TH32CS_SNAPPROCESS,
        },
        SystemInformation::{GetSystemTimeAsFileTime, GlobalMemoryStatusEx, MEMORYSTATUSEX},
        Threading::{
            GetProcessTimes, GetSystemTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
        },
    },
};
struct Handle(HANDLE);
impl Drop for Handle {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}
fn ticks(t: FILETIME) -> u64 {
    (u64::from(t.dwHighDateTime) << 32) | u64::from(t.dwLowDateTime)
}

pub(super) fn collect(began: Instant, until: Instant) -> Sample {
    let at = began.elapsed();
    let mut utc = FILETIME::default();
    let mut idle = FILETIME::default();
    let mut kernel = FILETIME::default();
    let mut user = FILETIME::default();
    if Instant::now() >= until {
        return Err("snapshot collection budget exhausted");
    }
    unsafe {
        GetSystemTimeAsFileTime(&mut utc);
    }
    let system =
        (unsafe { GetSystemTimes(&mut idle, &mut kernel, &mut user) } != 0).then_some(System {
            idle: ticks(idle),
            kernel: ticks(kernel),
            user: ticks(user),
        });
    let mut mem = MEMORYSTATUSEX {
        dwLength: size_of::<MEMORYSTATUSEX>() as u32,
        ..Default::default()
    };
    if Instant::now() >= until {
        return Err("snapshot collection budget exhausted");
    }
    let memory = (unsafe { GlobalMemoryStatusEx(&mut mem) } != 0).then_some(Memory {
        load: mem.dwMemoryLoad,
        available: mem.ullAvailPhys,
    });
    let mut result = Snapshot {
        at,
        finished: began.elapsed(),
        utc: ticks(utc),
        system,
        memory,
        processes: vec![],
        complete: false,
    };
    if Instant::now() >= until {
        return Ok(result);
    }
    let raw = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
    if raw == INVALID_HANDLE_VALUE {
        return Ok(result);
    }
    let snapshot = Handle(raw);
    if Instant::now() >= until {
        result.finished = began.elapsed();
        return Ok(result);
    }
    let mut entry = PROCESSENTRY32W {
        dwSize: size_of::<PROCESSENTRY32W>() as u32,
        ..Default::default()
    };
    let mut found = unsafe { Process32FirstW(snapshot.0, &mut entry) } != 0;
    while found && Instant::now() < until && result.processes.len() < 4096 {
        let length = entry
            .szExeFile
            .iter()
            .position(|&c| c == 0)
            .unwrap_or(entry.szExeFile.len());
        let mut p = Process {
            pid: entry.th32ProcessID,
            parent: entry.th32ParentProcessID,
            image: String::from_utf16_lossy(&entry.szExeFile[..length]),
            cpu: None,
        };
        let raw = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, p.pid) };
        if !raw.is_null() {
            let process = Handle(raw);
            let mut created = FILETIME::default();
            let mut exited = FILETIME::default();
            if Instant::now() < until
                && unsafe {
                    GetProcessTimes(process.0, &mut created, &mut exited, &mut kernel, &mut user)
                } != 0
                && ticks(exited) == 0
            {
                p.cpu = Some(Cpu {
                    created: ticks(created),
                    user: ticks(user),
                    kernel: ticks(kernel),
                });
            }
        }
        result.processes.push(p); // Retain parent links even when CPU is inaccessible.
        if Instant::now() >= until {
            break;
        }
        found = unsafe { Process32NextW(snapshot.0, &mut entry) } != 0;
    }
    result.complete = !found
        && std::io::Error::last_os_error().raw_os_error() == Some(ERROR_NO_MORE_FILES as i32);
    result.finished = began.elapsed();
    Ok(result)
}
