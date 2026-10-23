//! Temporary synthetic files only. Never uses the user's actual BAT data directory.
use super::*;
use crate::powershell_fixture::{self, Fixture};
use std::{
    ptr::{null, null_mut},
    time::Duration,
};
use windows_sys::Win32::{
    Foundation::LocalFree,
    Security::Cryptography::{CryptProtectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB},
};
fn protect(value: &str, entropy: Option<&[u8]>) -> Vec<u8> {
    let mut plaintext = Zeroizing::new(
        value
            .encode_utf16()
            .flat_map(u16::to_le_bytes)
            .collect::<Vec<_>>(),
    );
    let input = CRYPT_INTEGER_BLOB {
        cbData: plaintext.len() as u32,
        pbData: plaintext.as_mut_ptr(),
    };
    let extra = entropy.map(|bytes| CRYPT_INTEGER_BLOB {
        cbData: bytes.len() as u32,
        pbData: bytes.as_ptr().cast_mut(),
    });
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: null_mut(),
    };
    assert_ne!(
        unsafe {
            CryptProtectData(
                &input,
                null(),
                extra.as_ref().map_or(null(), |v| v),
                null(),
                null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        },
        0
    );
    assert!(!output.pbData.is_null());
    let result =
        unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize) }.to_vec();
    unsafe { std::slice::from_raw_parts_mut(output.pbData, output.cbData as usize) }.zeroize();
    unsafe {
        LocalFree(output.pbData.cast());
    }
    result
}
fn hex(bytes: &[u8]) -> Vec<u8> {
    bytes
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>()
        .into_bytes()
}
fn powershell(script: &str, path: &Path) {
    let fixture = Fixture::new();
    let mut command = powershell_fixture::command();
    command
        .args([
            "-Command",
            &format!("{}{script}", powershell_fixture::READY_SCRIPT),
        ])
        .env("BAT_FLEET_FIXTURE_DPAPI", path);
    fixture.run(&mut command, Duration::from_secs(15));
}
#[test]
fn native_dpapi_current_user_format_roundtrip_tamper_entropy_and_remove() {
    let temp = Temporary::new();
    let reference = CredentialRef::for_connector(&inventory()).unwrap();
    for value in ["fixture-one", "fixture-two-繁體-🔐"] {
        let bytes = hex(&protect(value, None));
        let path = temp.write("fleet-credentials/connector-observe.dpapi", &bytes);
        assert!(!String::from_utf8_lossy(&bytes).contains(value));
        assert_eq!(resolve(&temp.0, &reference).unwrap().fixture_value(), value);
        assert_eq!(std::fs::read(&path).unwrap(), bytes);
    }
    let bytes = hex(&protect(
        "fixture-other-entropy",
        Some(b"synthetic-entropy"),
    ));
    temp.write("fleet-credentials/connector-observe.dpapi", &bytes);
    missing(resolve(&temp.0, &reference));
    let mut damaged = protect("fixture-tamper", None);
    let last = damaged.len() - 1;
    damaged[last] ^= 1;
    let path = temp.write("fleet-credentials/connector-observe.dpapi", &hex(&damaged));
    missing(resolve(&temp.0, &reference));
    std::fs::remove_file(&path).unwrap();
    missing(resolve(&temp.0, &reference));
}
#[test]
fn exact_powershell_set_format_is_readable_and_native_blob_remains_ps_compatible() {
    let temp = Temporary::new();
    let reference = CredentialRef::for_connector(&inventory()).unwrap();
    let path = temp.write("fleet-credentials/connector-observe.dpapi", b"");
    // Same API/UTF8-no-BOM output as Kit Set-FleetCredential; no real Kit file is sourced.
    powershell(
        r#"$ErrorActionPreference='Stop';
[Console]::Error.Write("BATC_PHASE first-cmdlet-ConvertTo-before`n");[Console]::Error.Flush()
$s=ConvertTo-SecureString -AsPlainText -Force 'fixture-繁體-🔐';
[Console]::Error.Write("BATC_PHASE first-cmdlet-ConvertTo-after`n");[Console]::Error.Flush()
try {
  [Console]::Error.Write("BATC_PHASE dpapi-protect-before`n");[Console]::Error.Flush()
  $blob=ConvertFrom-SecureString -SecureString $s;
  [Console]::Error.Write("BATC_PHASE dpapi-protect-after`n");[Console]::Error.Flush()
  [Console]::Error.Write("BATC_PHASE encoding-New-Object-before`n");[Console]::Error.Flush()
  $encoding=New-Object Text.UTF8Encoding($false);
  [Console]::Error.Write("BATC_PHASE encoding-New-Object-after`n");[Console]::Error.Flush()
  [Console]::Error.Write("BATC_PHASE file-write-before`n");[Console]::Error.Flush()
  [IO.File]::WriteAllText($env:BAT_FLEET_FIXTURE_DPAPI,$blob,$encoding);
  [Console]::Error.Write("BATC_PHASE file-write-after`n");[Console]::Error.Flush()
} finally { $s.Dispose() }
[Console]::Error.Write("BATC_PHASE script-end`n");[Console]::Error.Flush()"#,
        &path,
    );
    let before = std::fs::read(&path).unwrap();
    assert_eq!(
        resolve(&temp.0, &reference).unwrap().fixture_value(),
        "fixture-繁體-🔐"
    );
    assert_eq!(std::fs::read(&path).unwrap(), before);
    std::fs::write(&path, hex(&protect("fixture-native", None))).unwrap();
    powershell(
        r#"$ErrorActionPreference='Stop';
[Console]::Error.Write("BATC_PHASE file-read-before`n");[Console]::Error.Flush()
$blob=[IO.File]::ReadAllText($env:BAT_FLEET_FIXTURE_DPAPI);
[Console]::Error.Write("BATC_PHASE file-read-after`n");[Console]::Error.Flush()
[Console]::Error.Write("BATC_PHASE first-cmdlet-and-dpapi-unprotect-before`n");[Console]::Error.Flush()
$s=ConvertTo-SecureString $blob;
[Console]::Error.Write("BATC_PHASE first-cmdlet-and-dpapi-unprotect-after`n");[Console]::Error.Flush()
[Console]::Error.Write("BATC_PHASE bstr-before`n");[Console]::Error.Flush()
$p=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s);
try { if([Runtime.InteropServices.Marshal]::PtrToStringBSTR($p) -cne 'fixture-native'){exit 3} }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p);$s.Dispose() }
[Console]::Error.Write("BATC_PHASE bstr-after`n");[Console]::Error.Flush()
[Console]::Error.Write("BATC_PHASE script-end`n");[Console]::Error.Flush()"#,
        &path,
    );
}
