//! Test-only CPU sample from the retained child handle; no PID lookup or process launch.
use std::{
    os::windows::io::{AsRawHandle, BorrowedHandle},
    time::Duration,
};
use windows_sys::Win32::{Foundation::FILETIME, System::Threading::GetProcessTimes};

pub fn describe(handle: BorrowedHandle<'_>, wall: Duration) -> String {
    let mut created = FILETIME::default();
    let mut exited = FILETIME::default();
    let mut kernel = FILETIME::default();
    let mut user = FILETIME::default();
    if unsafe {
        GetProcessTimes(
            handle.as_raw_handle(),
            &mut created,
            &mut exited,
            &mut kernel,
            &mut user,
        )
    } == 0
    {
        return format!(
            "child_cpu=unavailable (GetProcessTimes error {:?}); cpu_wall={wall:?}",
            std::io::Error::last_os_error().raw_os_error()
        );
    }
    let user = duration(user);
    let kernel = duration(kernel);
    // More than 100% is possible when the child uses more than one CPU.
    let percent = (user + kernel).as_secs_f64() / wall.as_secs_f64().max(f64::EPSILON) * 100.0;
    format!(
        "child_cpu=user:{user:?}, kernel:{kernel:?}, total/wall:{percent:.2}%; cpu_wall={wall:?}"
    )
}

fn duration(value: FILETIME) -> Duration {
    let ticks = (u64::from(value.dwHighDateTime) << 32) | u64::from(value.dwLowDateTime);
    Duration::from_secs(ticks / 10_000_000) + Duration::from_nanos((ticks % 10_000_000) * 100)
}
