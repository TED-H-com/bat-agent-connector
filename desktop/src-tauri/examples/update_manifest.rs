//! Release-only verifier. It writes a feed for an already signed artifact; it never signs or publishes.
use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{fs, path::Path};

fn manifest(
    artifact: &[u8],
    name: &str,
    public: &str,
    signature: &str,
    version: &str,
    source: &str,
    workflow: &str,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    if artifact.is_empty()
        || artifact.len() > 128 * 1024 * 1024
        || !name.ends_with(".exe")
        || name.len() > 160
        || !name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b" ._-".contains(&b))
        || version.is_empty()
        || version.len() > 128
        || !version
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b".-+".contains(&b))
        || source.len() != 40
        || !source.bytes().all(|b| b.is_ascii_hexdigit())
        || !workflow
            .strip_prefix("2026-10-08.")
            .is_some_and(|v| !v.is_empty() && v.bytes().all(|b| b.is_ascii_digit()))
    {
        return Err("invalid release metadata".into());
    }
    let public = STANDARD.decode(public.trim())?;
    let signature_text = STANDARD.decode(signature.trim())?;
    let public = minisign_verify::PublicKey::decode(std::str::from_utf8(&public)?)?;
    let signed = minisign_verify::Signature::decode(std::str::from_utf8(&signature_text)?)?;
    public.verify(artifact, &signed, false)?;
    let versions: Vec<_> = signed
        .trusted_comment()
        .split('\t')
        .filter_map(|part| part.strip_prefix("version:"))
        .collect();
    if versions != [version] {
        return Err("signature must bind exactly this release version".into());
    }
    Ok(json!({
        "version": version,
        "platforms": {"windows-x86_64-nsis": {
            "url": format!("https://github.com/TED-H-com/bat-agent-connector/releases/download/v{version}/{}", name.replace(' ', "%20")),
            "signature": signature.trim()
        }},
        "bat_dashboard": {"api_version": 1, "contract_version":"2026-10-08",
            "source_sha":source,"workflow_version":workflow,
            "artifact_sha256":format!("{:x}", Sha256::digest(artifact))}
    }))
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 7 {
        return Err("usage: update_manifest ARTIFACT SIGNATURE PUBLIC_KEY VERSION SOURCE_SHA WORKFLOW OUTPUT".into());
    }
    let artifact = Path::new(&args[0]);
    if fs::metadata(artifact)?.len() > 128 * 1024 * 1024
        || fs::metadata(&args[1])?.len() > 8192
        || fs::metadata(&args[2])?.len() > 4096
    {
        return Err("release input too large".into());
    }
    let output = manifest(
        &fs::read(artifact)?,
        artifact
            .file_name()
            .and_then(|s| s.to_str())
            .ok_or("artifact name")?,
        &fs::read_to_string(&args[2])?,
        &fs::read_to_string(&args[1])?,
        &args[3],
        &args[4],
        &args[5],
    )?;
    fs::write(&args[6], serde_json::to_vec_pretty(&output)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn release_feed_requires_exact_artifact_key_and_signed_version() {
        let keys = minisign::KeyPair::generate_unencrypted_keypair().unwrap();
        let public = STANDARD.encode(keys.pk.to_box().unwrap().to_string());
        let bytes = b"temporary artifact";
        let sign = |comment| {
            STANDARD.encode(
                minisign::sign(
                    Some(&keys.pk),
                    &keys.sk,
                    bytes.as_slice(),
                    Some(comment),
                    None,
                )
                .unwrap()
                .to_string(),
            )
        };
        let signature = sign("timestamp:1\tversion:0.2.0");
        let source = "a".repeat(40);
        let feed = manifest(
            bytes,
            "Dashboard setup.exe",
            &public,
            &signature,
            "0.2.0",
            &source,
            "2026-10-08.10",
        )
        .unwrap();
        assert!(feed["platforms"]["windows-x86_64-nsis"]["url"]
            .as_str()
            .unwrap()
            .ends_with("Dashboard%20setup.exe"));
        for (payload, signature, version) in [
            (b"tampered".as_slice(), signature.clone(), "0.2.0"),
            (bytes.as_slice(), signature, "0.3.0"),
            (bytes.as_slice(), sign("timestamp:1"), "0.2.0"),
            (
                bytes.as_slice(),
                sign("version:0.2.0\tversion:0.3.0"),
                "0.2.0",
            ),
        ] {
            assert!(manifest(
                payload,
                "fixture.exe",
                &public,
                &signature,
                version,
                &source,
                "2026-10-08.10"
            )
            .is_err());
        }
        let other = minisign::KeyPair::generate_unencrypted_keypair().unwrap();
        let other = STANDARD.encode(other.pk.to_box().unwrap().to_string());
        assert!(manifest(
            bytes,
            "fixture.exe",
            &other,
            &sign("version:0.2.0"),
            "0.2.0",
            &source,
            "2026-10-08.10"
        )
        .is_err());
    }
}
