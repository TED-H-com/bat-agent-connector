"""Product website capture-source checks ignore only the root lockfile version."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from scripts.check_product_site import LOCKFILE, LOCKFILE_DIGEST, ROOT, SITE, lockfile_digest, source_entry


def _sample_lockfile(version: str = "0.1.1") -> dict:
    return {
        "name": "better-agent-dashboard",
        "version": version,
        "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {
                "name": "better-agent-dashboard",
                "version": version,
                "dependencies": {"@tauri-apps/api": "2.12.1"},
                "devDependencies": {"@playwright/test": "^1.59.1", "vite": "^6.2.0"},
            },
            "node_modules/@playwright/test": {
                "version": "1.59.1",
                "resolved": "https://registry.npmjs.org/@playwright/test/-/test-1.59.1.tgz",
                "integrity": "sha512-syntheticPlaywrightIntegrityValue000000000000000000000000000000000000==",
                "dev": True,
            },
            "node_modules/@tauri-apps/api": {
                "version": "2.12.1",
                "resolved": "https://registry.npmjs.org/@tauri-apps/api/-/api-2.12.1.tgz",
                "integrity": "sha512-syntheticTauriApiIntegrityValue00000000000000000000000000000000000000==",
            },
        },
    }


def test_root_version_bump_preserves_lockfile_digest() -> None:
    v011 = json.dumps(_sample_lockfile("0.1.1"), indent=2).encode("utf-8")
    v012 = json.dumps(_sample_lockfile("0.1.2"), indent=2).encode("utf-8")
    rc = _sample_lockfile("0.2.0-rc.1")
    rc["packages"][""]["version"] = "0.2.0-rc.2"

    expected = lockfile_digest(v011)
    assert lockfile_digest(v012) == expected
    assert lockfile_digest(json.dumps(rc)) == expected
    assert source_entry(LOCKFILE, v011) == {
        "file": LOCKFILE,
        "digest": LOCKFILE_DIGEST,
        "sha256": expected,
    }
    assert source_entry(LOCKFILE, v012) == source_entry(LOCKFILE, v011)

    other_bytes = b"// capture helper\n"
    assert source_entry("scripts/capture_site.mjs", other_bytes) == {
        "file": "scripts/capture_site.mjs",
        "sha256": hashlib.sha256(other_bytes).hexdigest(),
    }

    manifest = json.loads((SITE / "images/manifest.json").read_text(encoding="utf-8"))
    lockfile_entries = [entry for entry in manifest["source_files"] if entry["file"] == LOCKFILE]
    assert lockfile_entries == [source_entry(LOCKFILE, (ROOT / LOCKFILE).read_bytes())]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc["packages"][""]["dependencies"].__setitem__("@tauri-apps/api", "2.12.2"),
        lambda doc: doc["packages"][""]["devDependencies"].__setitem__("@playwright/test", "^1.60.0"),
        lambda doc: doc["packages"]["node_modules/@playwright/test"].__setitem__("version", "1.59.2"),
        lambda doc: doc["packages"]["node_modules/@playwright/test"].__setitem__(
            "integrity",
            "sha512-differentPlaywrightIntegrityValue111111111111111111111111111111111111==",
        ),
        lambda doc: doc["packages"]["node_modules/@playwright/test"].__setitem__(
            "resolved",
            "https://registry.npmjs.org/@playwright/test/-/test-1.59.1-repacked.tgz",
        ),
        lambda doc: doc["packages"].__setitem__(
            "node_modules/extra-pkg",
            {
                "version": "1.0.0",
                "resolved": "https://registry.npmjs.org/extra-pkg/-/extra-pkg-1.0.0.tgz",
                "integrity": "sha512-extra==",
            },
        ),
    ],
)
def test_dependency_version_integrity_or_resolved_change_alters_digest(mutate) -> None:
    baseline = _sample_lockfile("0.1.1")
    changed = deepcopy(baseline)
    mutate(changed)
    assert lockfile_digest(json.dumps(changed)) != lockfile_digest(json.dumps(baseline))


@pytest.mark.parametrize(
    "payload",
    [
        b'{"name": "better-agent-dashboard", "version":',
        b"\xff\xfe",
        b"[]",
        b'"0.1.2"',
        b"null",
        b'{"version": "0.1.1", "packages": []}',
        b'{"version": "0.1.1", "packages": {"": "not-an-object"}}',
        b'{"version": 123, "packages": {"": {"version": "0.1.1"}}}',
        b'{"version": "0.1.1", "packages": {"": {"version": ["0.1.1"]}}}',
    ],
)
def test_malformed_lockfile_json_fails_clearly(payload: bytes) -> None:
    with pytest.raises(ValueError, match="Malformed lockfile JSON"):
        lockfile_digest(payload)
    with pytest.raises(ValueError, match="Malformed lockfile JSON"):
        source_entry(LOCKFILE, payload)
