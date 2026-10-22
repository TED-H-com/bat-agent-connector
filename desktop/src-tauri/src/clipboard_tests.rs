// Included in files::tests to exercise the production spool and scope binding.
use crate::clipboard::{ClipboardError, ClipboardImage, ClipboardReader};

struct FakeClipboard<'a> {
    image: ClipboardImage<'a>,
    reads: usize,
    disconnect: Option<Arc<Bridge>>,
}
impl ClipboardReader for FakeClipboard<'_> {
    fn read(
        &mut self,
        consume: impl FnOnce(ClipboardImage<'_>) -> Result<Vec<u8>, ClipboardError>,
    ) -> Result<Vec<u8>, ClipboardError> {
        self.reads += 1;
        if let Some(bridge) = &self.disconnect {
            bridge.disconnect();
        }
        consume(self.image)
    }
}
fn clipboard_png() -> Vec<u8> {
    let mut bytes = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut bytes, 1, 1);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        encoder
            .write_header()
            .unwrap()
            .write_image_data(&[20, 40, 60, 255])
            .unwrap();
    }
    bytes
}
#[test]
fn clipboard_lease_is_required_focused_fresh_and_single_use() {
    let (_root, _bridge, files) = fixture();
    let bytes = clipboard_png();
    let mut reader = FakeClipboard {
        image: ClipboardImage::Png(&bytes),
        reads: 0,
        disconnect: None,
    };
    assert!(files
        .paste_with("draft", true, &mut reader, MAX_ARTIFACT_BYTES as u64)
        .is_err());
    assert!(files.paste_arm("draft".into(), false).is_err());
    files.paste_arm("draft".into(), true).unwrap();
    files.state.lock().unwrap().paste_lease.as_mut().unwrap().2 -= Duration::from_secs(3);
    assert!(files
        .paste_with("draft", true, &mut reader, MAX_ARTIFACT_BYTES as u64)
        .is_err());
    files.paste_arm("draft".into(), true).unwrap();
    assert!(files
        .paste_with("draft", false, &mut reader, MAX_ARTIFACT_BYTES as u64)
        .is_err());
    files.paste_arm("draft".into(), true).unwrap();
    assert!(files
        .paste_with("other", true, &mut reader, MAX_ARTIFACT_BYTES as u64)
        .is_err());
    assert_eq!(reader.reads, 0);
    files.paste_arm("draft".into(), true).unwrap();
    assert!(files
        .paste_with("draft", true, &mut reader, MAX_ARTIFACT_BYTES as u64)
        .is_ok());
    assert!(files
        .paste_with("draft", true, &mut reader, MAX_ARTIFACT_BYTES as u64)
        .is_err());
    assert_eq!(reader.reads, 1);
}
#[test]
fn clipboard_receipt_matches_pick_and_scope_change_refuses() {
    let (root, bridge, files) = fixture();
    let bytes = clipboard_png();
    let picked = select(root.path(), &files, &bytes);
    let mut reader = FakeClipboard {
        image: ClipboardImage::Png(&bytes),
        reads: 0,
        disconnect: None,
    };
    files.paste_arm("draft".into(), true).unwrap();
    let pasted = files
        .paste_with("draft", true, &mut reader, MAX_ARTIFACT_BYTES as u64)
        .unwrap();
    let pick_json = serde_json::to_value(picked).unwrap();
    let paste_json = serde_json::to_value(&pasted).unwrap();
    assert_eq!(
        pick_json.as_object().unwrap().keys().collect::<Vec<_>>(),
        paste_json.as_object().unwrap().keys().collect::<Vec<_>>()
    );
    assert_eq!(pasted.media_type, "image/png");
    assert_eq!(pasted.digest, format!("{:x}", Sha256::digest(&bytes)));
    assert!(pasted.display_name.starts_with("pasted-image-"));
    assert!(pasted.display_name.ends_with("Z.png"));
    assert_eq!(pasted.stage, "selected");
    assert_eq!(
        files
            .root
            .read(format!("{}.bin", pasted.transfer_id))
            .unwrap(),
        bytes
    );
    let before = files.state.lock().unwrap().records.len();
    files.paste_arm("draft".into(), true).unwrap();
    reader.disconnect = Some(bridge);
    assert!(files
        .paste_with("draft", true, &mut reader, MAX_ARTIFACT_BYTES as u64)
        .is_err());
    assert_eq!(files.state.lock().unwrap().records.len(), before);
}
#[test]
fn clipboard_bounds_precede_pixel_allocation() {
    use crate::clipboard::{encode_image, validate_dimensions};
    assert!(validate_dimensions(8193, 1).is_err());
    assert!(validate_dimensions(8192, 8192).is_err());
    // A header-only oversized DIB must fail the size bound, not reach pixel allocation/read.
    let mut dib = [0u8; 40];
    dib[..4].copy_from_slice(&40u32.to_le_bytes());
    dib[4..8].copy_from_slice(&100_000i32.to_le_bytes());
    dib[8..12].copy_from_slice(&100_000i32.to_le_bytes());
    dib[12..14].copy_from_slice(&1u16.to_le_bytes());
    dib[14..16].copy_from_slice(&32u16.to_le_bytes());
    assert_eq!(
        encode_image(ClipboardImage::Dib(&dib), MAX_ARTIFACT_BYTES)
            .unwrap_err()
            .code,
        "ARTIFACT_TOO_LARGE"
    );
    let bytes = clipboard_png();
    assert_eq!(
        encode_image(ClipboardImage::Png(&bytes), bytes.len() - 1)
            .unwrap_err()
            .code,
        "ARTIFACT_TOO_LARGE"
    );
    let mut oversized = bytes.clone();
    oversized[16..20].copy_from_slice(&100_000u32.to_be_bytes());
    oversized[20..24].copy_from_slice(&100_000u32.to_be_bytes());
    assert_eq!(
        encode_image(ClipboardImage::Png(&oversized), MAX_ARTIFACT_BYTES)
            .unwrap_err()
            .code,
        "ARTIFACT_TOO_LARGE"
    );
}

#[test]
fn clipboard_dib_converts_bgr_and_orientation_to_png() {
    use crate::clipboard::encode_image;
    for height in [2i32, -2] {
        let mut dib = vec![0u8; 48];
        dib[..4].copy_from_slice(&40u32.to_le_bytes());
        dib[4..8].copy_from_slice(&1i32.to_le_bytes());
        dib[8..12].copy_from_slice(&height.to_le_bytes());
        dib[12..14].copy_from_slice(&1u16.to_le_bytes());
        dib[14..16].copy_from_slice(&24u16.to_le_bytes());
        dib[40..].copy_from_slice(&[60, 40, 20, 0, 90, 80, 70, 0]);
        let png = encode_image(ClipboardImage::Dib(&dib), MAX_ARTIFACT_BYTES).unwrap();
        let mut reader = png::Decoder::new(std::io::Cursor::new(png))
            .read_info()
            .unwrap();
        let mut pixels = vec![0; reader.output_buffer_size().unwrap()];
        reader.next_frame(&mut pixels).unwrap();
        let expected = if height < 0 {
            [20, 40, 60, 255, 70, 80, 90, 255]
        } else {
            [70, 80, 90, 255, 20, 40, 60, 255]
        };
        assert_eq!(pixels, expected);
        assert_eq!(
            encode_image(ClipboardImage::Dib(&dib), 1).unwrap_err().code,
            "ARTIFACT_TOO_LARGE"
        );
        dib[16..20].copy_from_slice(&1u32.to_le_bytes());
        assert_eq!(
            encode_image(ClipboardImage::Dib(&dib), MAX_ARTIFACT_BYTES)
                .unwrap_err()
                .code,
            "CLIENT_ACTION_NATIVE_PASTE_UNSUPPORTED"
        );
    }
}
#[test]
fn clipboard_reuses_spool_capacity_and_record_limits() {
    let (root, _bridge, files) = fixture();
    let bytes = clipboard_png();
    let selected = select(root.path(), &files, &bytes);
    let record = files
        .record(&selected.transfer_id, &files.scope().unwrap())
        .unwrap();
    let mut reader = FakeClipboard {
        image: ClipboardImage::Png(&bytes),
        reads: 0,
        disconnect: None,
    };
    files
        .state
        .lock()
        .unwrap()
        .records
        .extend((0..MAX_RECORDS).map(|i| (format!("file_{i:032x}"), record.clone())));
    files.paste_arm("draft".into(), true).unwrap();
    assert!(files
        .paste_with("draft", true, &mut reader, MAX_ARTIFACT_BYTES as u64)
        .is_err());
    files.state.lock().unwrap().records.clear();
    let reserved = File::create(root.path().join("cache").join("capacity.bin")).unwrap();
    reserved.set_len(MAX_SPOOL).unwrap();
    files.paste_arm("draft".into(), true).unwrap();
    assert!(files
        .paste_with("draft", true, &mut reader, MAX_ARTIFACT_BYTES as u64)
        .is_err());
    assert!(files.state.lock().unwrap().records.is_empty());
}

#[test]
fn clipboard_png_padding_is_trimmed_before_hashing_and_spooling() {
    let (_root, _bridge, files) = fixture();
    let exact = clipboard_png();
    let mut allocation = exact.clone();
    allocation.extend_from_slice(&[0, 0, 0, 0, 0xab, 0xcd]);
    let mut reader = FakeClipboard {
        image: ClipboardImage::Png(&allocation),
        reads: 0,
        disconnect: None,
    };
    files.paste_arm("draft".into(), true).unwrap();
    let receipt = files
        .paste_with("draft", true, &mut reader, MAX_ARTIFACT_BYTES as u64)
        .unwrap();
    assert_eq!(receipt.size_bytes, exact.len() as u64);
    assert_eq!(receipt.digest, format!("{:x}", Sha256::digest(&exact)));
    assert_eq!(
        files
            .root
            .read(format!("{}.bin", receipt.transfer_id))
            .unwrap(),
        exact
    );
}

#[test]
fn clipboard_truncated_png_is_invalid_image() {
    use crate::clipboard::encode_image;
    let exact = clipboard_png();
    for missing in [1, 4, 12] {
        let result = encode_image(
            ClipboardImage::Png(&exact[..exact.len() - missing]),
            MAX_ARTIFACT_BYTES,
        );
        assert_eq!(result.unwrap_err().reason, "invalid_image");
    }
}
