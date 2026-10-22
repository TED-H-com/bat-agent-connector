//! OS calls only copy bounded bytes; validation and encoding run on a blocking worker.
use crate::bridge::MAX_ARTIFACT_BYTES;
use serde::Serialize;
use std::io::Cursor;
#[cfg(any(windows, test))]
use std::io::Write;

const MAX_PIXELS: usize = 16 * 1024 * 1024;
const MAX_DECODED: usize = MAX_PIXELS * 4;
#[derive(Debug, Serialize)]
pub struct ClipboardError {
    pub code: &'static str,
    pub reason: String,
}
impl ClipboardError {
    pub fn refused(reason: &str) -> Self {
        Self {
            code: "NATIVE_CLIPBOARD_REFUSED",
            reason: reason.into(),
        }
    }
    fn unsupported(reason: &str) -> Self {
        Self {
            code: "CLIENT_ACTION_NATIVE_PASTE_UNSUPPORTED",
            reason: reason.into(),
        }
    }
    fn large() -> Self {
        Self {
            code: "ARTIFACT_TOO_LARGE",
            reason: "clipboard_image_limits".into(),
        }
    }
    fn invalid() -> Self {
        Self::refused("invalid_image")
    }
}
impl From<String> for ClipboardError {
    fn from(reason: String) -> Self {
        Self::refused(&reason)
    }
}
#[derive(Clone)]
pub(crate) enum ClipboardImage {
    #[cfg_attr(not(any(windows, target_os = "macos", test)), allow(dead_code))]
    Png(Vec<u8>),
    #[cfg(any(windows, test))]
    Dib(Vec<u8>),
}
pub(crate) trait ClipboardReader {
    // Owned raw data only: the window-thread call cannot accept a processing callback.
    fn read(&mut self) -> Result<ClipboardImage, ClipboardError>;
}
pub(crate) struct SystemClipboard;

pub(crate) fn validate_dimensions(width: u32, height: u32) -> Result<usize, ClipboardError> {
    let pixels = (width as usize)
        .checked_mul(height as usize)
        .ok_or_else(ClipboardError::large)?;
    if width == 0 || height == 0 || width > 8192 || height > 8192 || pixels > MAX_PIXELS {
        return Err(ClipboardError::large());
    }
    Ok(pixels * 4)
}
pub(crate) fn encode_image(image: ClipboardImage, limit: usize) -> Result<Vec<u8>, ClipboardError> {
    let limit = limit.min(MAX_ARTIFACT_BYTES);
    match image {
        ClipboardImage::Png(mut bytes) => {
            if bytes.len() > limit {
                return Err(ClipboardError::large());
            }
            if bytes.len() < 33 || &bytes[..8] != b"\x89PNG\r\n\x1a\n" || &bytes[12..16] != b"IHDR"
            {
                return Err(ClipboardError::invalid());
            }
            validate_dimensions(
                u32::from_be_bytes(bytes[16..20].try_into().unwrap()),
                u32::from_be_bytes(bytes[20..24].try_into().unwrap()),
            )?;
            let mut decoder = png::Decoder::new(Cursor::new(&bytes[..]));
            decoder.set_limits(png::Limits { bytes: MAX_DECODED });
            let mut reader = decoder.read_info().map_err(|_| ClipboardError::invalid())?;
            if reader.info().animation_control.is_some() {
                return Err(ClipboardError::unsupported("animated_png"));
            }
            let size = reader
                .output_buffer_size()
                .filter(|n| *n <= MAX_DECODED)
                .ok_or_else(ClipboardError::large)?;
            let mut pixels = vec![0; size];
            reader
                .next_frame(&mut pixels)
                .map_err(|_| ClipboardError::invalid())?;
            reader.finish().map_err(|_| ClipboardError::invalid())?;
            drop(reader);
            bytes.truncate(png_end(&bytes)?);
            Ok(bytes)
        }
        #[cfg(any(windows, test))]
        ClipboardImage::Dib(bytes) => encode_dib(&bytes, limit),
    }
}

// Walk actual chunk boundaries, never an IEND-looking sequence inside compressed data.
// The decoder above validates the image; require a complete, zero-length IEND with its CRC.
fn png_end(bytes: &[u8]) -> Result<usize, ClipboardError> {
    let mut offset = 8usize;
    loop {
        let header = bytes
            .get(offset..offset + 8)
            .ok_or_else(ClipboardError::invalid)?;
        let length = u32::from_be_bytes(header[..4].try_into().unwrap()) as usize;
        let end = offset
            .checked_add(12)
            .and_then(|n| n.checked_add(length))
            .filter(|end| *end <= bytes.len())
            .ok_or_else(ClipboardError::invalid)?;
        if &header[4..] == b"IEND" {
            if length != 0 || bytes[end - 4..end] != [0xae, 0x42, 0x60, 0x82] {
                return Err(ClipboardError::invalid());
            }
            return Ok(end);
        }
        offset = end;
    }
}

#[cfg(any(windows, test))]
struct BoundedPng {
    bytes: Vec<u8>,
    limit: usize,
}
#[cfg(any(windows, test))]
impl Write for BoundedPng {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() > self.limit.saturating_sub(self.bytes.len()) {
            return Err(std::io::ErrorKind::FileTooLarge.into());
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
#[cfg(any(windows, test))]
fn encode_dib(bytes: &[u8], limit: usize) -> Result<Vec<u8>, ClipboardError> {
    if bytes.len() > MAX_DECODED + 124 {
        return Err(ClipboardError::large());
    }
    if bytes.len() < 40 {
        return Err(ClipboardError::invalid());
    }
    let u32_at = |i| u32::from_le_bytes(bytes[i..i + 4].try_into().unwrap());
    let header = u32_at(0) as usize;
    let width = u32_at(4) as i32;
    let height = u32_at(8) as i32;
    if width <= 0 {
        return Err(ClipboardError::invalid());
    }
    let capacity = validate_dimensions(width as u32, height.unsigned_abs())?;
    if !matches!(header, 40 | 52 | 56 | 108 | 124) || bytes.len() < header {
        return Err(ClipboardError::unsupported("dib_header"));
    }
    let bits = u16::from_le_bytes(bytes[14..16].try_into().unwrap());
    let compression = u32_at(16);
    if bytes[12..14] != [1, 0]
        || !matches!(bits, 24 | 32)
        || !matches!(compression, 0 | 3)
        || u32_at(32) != 0
    {
        return Err(ClipboardError::unsupported("dib_format"));
    }
    if header == 124 && (u32_at(112) != 0 || u32_at(116) != 0) {
        return Err(ClipboardError::unsupported("dib_profile"));
    }
    let mut offset = header;
    let mut alpha = false;
    if compression == 3 {
        if bits != 32 || bytes.len() < 52 {
            return Err(ClipboardError::unsupported("dib_masks"));
        }
        if [u32_at(40), u32_at(44), u32_at(48)] != [0x00ff0000, 0x0000ff00, 0x000000ff] {
            return Err(ClipboardError::unsupported("dib_masks"));
        }
        if header == 40 {
            offset += 12;
        }
        if header >= 56 {
            let mask = u32_at(52);
            if mask != 0 && mask != 0xff000000 {
                return Err(ClipboardError::unsupported("dib_alpha"));
            }
            alpha = mask != 0;
        }
    }
    let width = width as usize;
    let rows = height.unsigned_abs() as usize;
    let stride = (width * bits as usize).div_ceil(32) * 4;
    let end = offset
        .checked_add(stride * rows)
        .ok_or_else(ClipboardError::large)?;
    if end > bytes.len() {
        return Err(ClipboardError::invalid());
    }
    // All byte/dimension/header checks precede this pixel allocation.
    let mut rgba = vec![0; capacity];
    for y in 0..rows {
        let source_y = if height < 0 { y } else { rows - 1 - y };
        for x in 0..width {
            let src = offset + source_y * stride + x * (bits as usize / 8);
            let dst = (y * width + x) * 4;
            rgba[dst..dst + 4].copy_from_slice(&[
                bytes[src + 2],
                bytes[src + 1],
                bytes[src],
                if alpha { bytes[src + 3] } else { 255 },
            ]);
        }
    }
    let mut output = BoundedPng {
        bytes: Vec::new(),
        limit,
    };
    {
        let mut encoder = png::Encoder::new(&mut output, width as u32, rows as u32);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder
            .write_header()
            .map_err(|_| ClipboardError::large())?;
        writer
            .write_image_data(&rgba)
            .map_err(|_| ClipboardError::large())?;
        writer.finish().map_err(|_| ClipboardError::large())?;
    }
    Ok(output.bytes)
}

#[cfg(not(any(windows, target_os = "macos")))]
impl ClipboardReader for SystemClipboard {
    fn read(&mut self) -> Result<ClipboardImage, ClipboardError> {
        Err(ClipboardError::unsupported("platform"))
    }
}
#[cfg(windows)]
mod windows_clipboard {
    use super::*;
    use windows_sys::Win32::System::{DataExchange::*, Memory::*};
    const CF_DIB: u32 = 8;
    pub(super) struct OpenClipboardGuard;
    impl Drop for OpenClipboardGuard {
        fn drop(&mut self) {
            unsafe {
                CloseClipboard();
            }
        }
    }
    impl ClipboardReader for SystemClipboard {
        fn read(&mut self) -> Result<ClipboardImage, ClipboardError> {
            unsafe {
                if OpenClipboard(std::ptr::null_mut()) == 0 {
                    return Err(ClipboardError::refused("clipboard_busy"));
                }
                let _opened = OpenClipboardGuard;
                let png = RegisterClipboardFormatW(windows_sys::w!("PNG"));
                let is_png = png != 0 && IsClipboardFormatAvailable(png) != 0;
                // Windows synthesizes CF_DIB for CF_BITMAP; never read copied files (CF_HDROP).
                let format = if is_png {
                    png
                } else if IsClipboardFormatAvailable(CF_DIB) != 0 {
                    CF_DIB
                } else {
                    return Err(ClipboardError::unsupported("clipboard_format"));
                };
                let handle = GetClipboardData(format);
                if handle.is_null() {
                    return Err(ClipboardError::refused("clipboard_unavailable"));
                }
                let size = GlobalSize(handle);
                let bound = if is_png {
                    MAX_ARTIFACT_BYTES
                } else {
                    MAX_DECODED + 124
                };
                if size > bound {
                    return Err(ClipboardError::large());
                }
                if size == 0 {
                    return Err(ClipboardError::invalid());
                }
                let pointer = GlobalLock(handle);
                if pointer.is_null() {
                    return Err(ClipboardError::refused("clipboard_unavailable"));
                }
                struct Locked(windows_sys::Win32::Foundation::HGLOBAL);
                impl Drop for Locked {
                    fn drop(&mut self) {
                        unsafe {
                            GlobalUnlock(self.0);
                        }
                    }
                }
                let _locked = Locked(handle);
                let bytes = std::slice::from_raw_parts(pointer.cast::<u8>(), size);
                let owned = bytes.to_vec();
                Ok(if is_png {
                    ClipboardImage::Png(owned)
                } else {
                    ClipboardImage::Dib(owned)
                })
                // Guards release the clipboard before this owned value reaches the worker.
            }
        }
    }
}
#[cfg(target_os = "macos")]
impl ClipboardReader for SystemClipboard {
    fn read(&mut self) -> Result<ClipboardImage, ClipboardError> {
        use objc2_app_kit::NSPasteboard;
        use objc2_foundation::NSString;
        let board = NSPasteboard::generalPasteboard();
        let generation = board.changeCount();
        let data = board
            .dataForType(&NSString::from_str("public.png"))
            .ok_or_else(|| ClipboardError::unsupported("clipboard_format"))?;
        if data.len() > MAX_ARTIFACT_BYTES {
            return Err(ClipboardError::large());
        }
        let owned = unsafe { data.as_bytes_unchecked() }.to_vec();
        if board.changeCount() != generation {
            return Err(ClipboardError::refused("clipboard_changed"));
        }
        Ok(ClipboardImage::Png(owned))
    }
}

#[cfg(all(windows, test))]
mod os_tests {
    use super::*;
    use windows_sys::Win32::{
        Graphics::Gdi::*, System::DataExchange::*, UI::WindowsAndMessaging::*,
    };

    #[test]
    fn windows_clipboard_bitmap_round_trip() {
        // Runs in Windows CI's native test job (--nocapture). Never silently skip an absent desktop.
        unsafe {
            let window = CreateWindowExW(
                0,
                windows_sys::w!("STATIC"),
                windows_sys::w!("clipboard-test"),
                0,
                0,
                0,
                0,
                0,
                HWND_MESSAGE,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null(),
            );
            assert!(
                !window.is_null(),
                "WINDOWS_CLIPBOARD_DEVICE_ACCEPTANCE_REQUIRED: no window station: {}",
                std::io::Error::last_os_error()
            );
            struct Window(windows_sys::Win32::Foundation::HWND);
            impl Drop for Window {
                fn drop(&mut self) {
                    unsafe {
                        DestroyWindow(self.0);
                    }
                }
            }
            let _window = Window(window);
            assert_ne!(
                OpenClipboard(window),
                0,
                "WINDOWS_CLIPBOARD_DEVICE_ACCEPTANCE_REQUIRED: OpenClipboard failed: {}",
                std::io::Error::last_os_error()
            );
            let opened = windows_clipboard::OpenClipboardGuard;
            assert_ne!(EmptyClipboard(), 0);
            let pixels = [60u8, 40, 20, 0, 90, 80, 70, 0];
            let bitmap = CreateBitmap(2, 1, 1, 32, pixels.as_ptr().cast());
            assert!(!bitmap.is_null());
            let result = SetClipboardData(2, bitmap); // CF_BITMAP, owned by OS after success.
            if result.is_null() {
                DeleteObject(bitmap);
                panic!("SetClipboardData: {}", std::io::Error::last_os_error());
            }
            drop(opened);
            let raw = SystemClipboard.read().unwrap();
            let bytes = encode_image(raw, MAX_ARTIFACT_BYTES).unwrap();
            let mut reader = png::Decoder::new(Cursor::new(bytes)).read_info().unwrap();
            let mut decoded = vec![0; reader.output_buffer_size().unwrap()];
            let info = reader.next_frame(&mut decoded).unwrap();
            assert_eq!((info.width, info.height), (2, 1));
            assert_eq!(&decoded[..8], &[20, 40, 60, 255, 70, 80, 90, 255]);
            assert_ne!(OpenClipboard(window), 0);
            let _opened = windows_clipboard::OpenClipboardGuard;
            assert_ne!(EmptyClipboard(), 0);
            eprintln!("WINDOWS_CLIPBOARD_ROUND_TRIP_OK: CF_BITMAP -> OS CF_DIB -> Rust PNG");
        }
    }
}
