//! A cancellable, single reader for the test-owned stdout/stderr pipes.
use super::Lifecycle;
use std::{
    io::{self, Read},
    sync::{atomic::AtomicBool, atomic::Ordering, Arc, Mutex},
    thread,
    time::{Duration, Instant},
};

pub(super) struct Pipe<P> {
    pipe: P,
    stop: Arc<AtomicBool>,
    lifecycle: Option<Arc<Mutex<Lifecycle>>>,
    began: Instant,
}
impl<P> Pipe<P> {
    pub(super) fn new(
        pipe: P,
        stop: Arc<AtomicBool>,
        lifecycle: Option<Arc<Mutex<Lifecycle>>>,
        began: Instant,
    ) -> Self {
        Self {
            pipe,
            stop,
            lifecycle,
            began,
        }
    }
}
impl<P: PipeHandle> Read for Pipe<P> {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        if buffer.is_empty() {
            return Ok(0); // An empty buffer is not evidence of EOF.
        }
        loop {
            if self.stop.load(Ordering::Relaxed) {
                return Err(io::Error::other("fixture reader cancelled"));
            }
            match read_available(&mut self.pipe, buffer) {
                Ok(0) => {
                    if let Some(lifecycle) = &self.lifecycle {
                        lifecycle
                            .lock()
                            .unwrap()
                            .stdout_eof
                            .get_or_insert_with(|| self.began.elapsed());
                    }
                    return Ok(0);
                }
                Err(error)
                    if matches!(
                        error.kind(),
                        io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
                    ) =>
                {
                    thread::sleep(Duration::from_millis(10));
                }
                result => return result,
            }
        }
    }
}

#[cfg(unix)]
fn read_available(pipe: &mut impl PipeHandle, buffer: &mut [u8]) -> io::Result<usize> {
    let mut fd = libc::pollfd {
        fd: pipe.as_raw_fd(),
        events: libc::POLLIN,
        revents: 0,
    };
    match unsafe { libc::poll(&mut fd, 1, 0) } {
        -1 => Err(io::Error::last_os_error()),
        0 => Err(io::ErrorKind::WouldBlock.into()),
        // This is the only reader; readable bytes or hangup cannot be consumed
        // by a competing read between poll and read.
        _ => pipe.read(buffer),
    }
}

#[cfg(windows)]
fn read_available(pipe: &mut impl PipeHandle, buffer: &mut [u8]) -> io::Result<usize> {
    use std::ptr::null_mut;
    use windows_sys::Win32::{Foundation::ERROR_BROKEN_PIPE, System::Pipes::PeekNamedPipe};
    let mut available = 0;
    if unsafe {
        PeekNamedPipe(
            pipe.as_raw_handle(),
            null_mut(),
            0,
            null_mut(),
            &mut available,
            null_mut(),
        )
    } == 0
    {
        let error = io::Error::last_os_error();
        return if error.raw_os_error() == Some(ERROR_BROKEN_PIPE as i32) {
            Ok(0)
        } else {
            Err(error)
        };
    }
    if available == 0 {
        return Err(io::ErrorKind::WouldBlock.into());
    }
    // No other reader can consume these bytes. Never issue a blocking read on
    // an empty pipe, so an inherited writer cannot strand the reader on drop.
    let count = buffer.len().min(available as usize);
    pipe.read(&mut buffer[..count])
}

#[cfg(unix)]
pub(super) trait PipeHandle: Read + std::os::fd::AsRawFd {}
#[cfg(unix)]
impl<T: Read + std::os::fd::AsRawFd> PipeHandle for T {}
#[cfg(windows)]
pub(super) trait PipeHandle: Read + std::os::windows::io::AsRawHandle {}
#[cfg(windows)]
impl<T: Read + std::os::windows::io::AsRawHandle> PipeHandle for T {}
