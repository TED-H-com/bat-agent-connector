"""Product website capture-source checks ignore only the root lockfile version."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from scripts.check_product_site import (
    LOCKFILE,
    LOCKFILE_DIGEST,
    PINNED_PATHS,
    ROOT,
    SITE,
    capture_source_errors,
    lockfile_digest,
    source_entry,
)
from scripts.desktop_fixture_inputs import path_matches


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


CONVERSATION_FIXTURE = "desktop/tests/conversation-fixture.ts"


@pytest.fixture
def capture_repo(tmp_path: Path):
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-q")
    sources = {
        "desktop/src/app.ts": b"// pinned frontend\n",
        "desktop/tests/demo/index.ts": b"// pinned demo\n",
        CONVERSATION_FIXTURE: b"// pinned conversation\n",
        "src/bat_agent_connector/dashboard/app.js": b"// pinned browser asset\n",
        "scripts/capture_site.mjs": b"// current capture helper\n",
        LOCKFILE: json.dumps(_sample_lockfile()).encode(),
    }
    for name, data in sources.items():
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
    git("add", ".")
    git("-c", "user.name=Capture Test", "-c", "user.email=capture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Pinned capture inputs")
    commit = git("rev-parse", "HEAD")
    manifest = {
        "frontend_source_commit": commit,
        "frontend_source_tree": git("rev-parse", f"{commit}:desktop/src"),
        "source_files": [source_entry(name, data) for name, data in sources.items()],
    }
    assert capture_source_errors(manifest, tmp_path, commit) == []
    return tmp_path, commit, manifest


def test_pinned_conversation_ignores_working_tree_edit(capture_repo) -> None:
    root, commit, manifest = capture_repo
    (root / CONVERSATION_FIXTURE).write_bytes(b"// later main fixture\n")
    assert capture_source_errors(manifest, root, commit) == []


def test_conversation_manifest_must_match_pinned_blob(capture_repo) -> None:
    root, commit, manifest = capture_repo
    changed = b"// later main fixture\n"
    (root / CONVERSATION_FIXTURE).write_bytes(changed)
    manifest["source_files"] = [source_entry(CONVERSATION_FIXTURE, changed)]
    assert capture_source_errors(manifest, root, commit) == [
        f"Capture source does not match its manifest: {CONVERSATION_FIXTURE}"
    ]


@pytest.mark.parametrize("file", [
    "desktop/tests/new-helper.ts",
    "desktop/tests/conversation-fixture.ts.bak",
    "desktop/tests/demo-extra/helper.ts",
])
def test_unclassified_capture_source_fails_clearly(capture_repo, file: str) -> None:
    root, commit, manifest = capture_repo
    path = root / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"// new capture dependency\n")
    manifest["source_files"].append(source_entry(file, path.read_bytes()))
    assert capture_source_errors(manifest, root, commit) == [
        f"Capture source is neither pinned nor an allowed working-tree input: {file}"
    ]


def test_capture_guard_paths_are_pinned_by_checker() -> None:
    capture = (ROOT / "scripts/capture_site.mjs").read_text()
    guard = re.search(r"git\('diff', '--exit-code', sourceCommit, '--', ([^;]+)\);", capture)
    assert guard is not None
    guard_paths = ast.literal_eval(f"[{guard[1]}]")
    assert guard_paths
    # The recorded script's guard is expected to gain the conversation fixture at
    # the next recapture, when its recorded hash changes anyway. The checker is
    # already authoritative for every pinned input, including that fixture.
    assert {path.rstrip("/") for path in guard_paths} <= {path.rstrip("/") for path in PINNED_PATHS}


PAGES_WORKFLOW = ROOT / ".github/workflows/pages.yml"
REQUIRED_PAGES_INPUTS = (
    ".github/workflows/pages.yml",
    ".github/actions/bounded-apt/action.yml",
    "site/index.html",
    "site/404.html",
    "site/styles.css",
    "site/app.js",
    "site/lang.js",
    "site/README.md",
    "site/DESIGN.md",
    "site/images/README.md",
    "site/images/manifest.json",
    "desktop/package.json",
    "desktop/package-lock.json",
    "desktop/tsconfig.json",
    "desktop/vite.config.ts",
    "desktop/index.html",
    "desktop/playwright.config.ts",
    "desktop/scripts/browser-assets.mjs",
    "desktop/prototype/v1.3/vite.config.ts",
    "desktop/prototype/v1.3/index.html",
    "desktop/demo/vite.config.ts",
    "desktop/demo/index.html",
    "desktop/demo/main.ts",
    "desktop/demo/transport.ts",
    "desktop/demo/i18n.js",
    "desktop/src/main.ts",
    "desktop/src/i18n.js",
    "desktop/tests/demo/index.ts",
    "desktop/tests/demo/project.ts",
    "desktop/tests/demo/schedule.ts",
    "desktop/tests/public-demo.spec.ts",
    "desktop/tests/pm-navigation.ts",
    "desktop/tests/static-server.mjs",
    "scripts/build_product_site.py",
    "scripts/check_product_site.py",
    "scripts/verify_product_site.mjs",
    "scripts/capture_site.mjs",
    "scripts/hidden-child-process.mjs",
    "README.md",
    "README.zh-TW.md",
    "CHANGELOG.md",
    "docs/getting-started.md",
    "docs/getting-started.zh-TW.md",
    "docs/windows.md",
    "docs/windows.zh-TW.md",
    "docs/design/managed-installation.md",
    "docs/product/progress-2026-10-12.md",
    "docs/product/progress-2026-10-14.md",
    "docs/product/progress-2026-10-16.md",
    "docs/product/implementation-status.md",
    "docs/product/pm-v1.2-status.md",
    "docs/product/pm-v1.2-history-through-2026-10-12.md",
    "docs/handoff/2026-10-12-pm-v1.2.md",
    "docs/handoff/2026-10-14-docs-ux-v2.md",
)


def test_pages_workflow_paths_match_all_required_inputs() -> None:
    document = yaml.safe_load(PAGES_WORKFLOW.read_text(encoding="utf-8"))
    events = document.get("on", document.get(True))
    push_paths = events["push"]["paths"]
    pull_request_paths = events["pull_request"]["paths"]
    assert push_paths == pull_request_paths
    for required in REQUIRED_PAGES_INPUTS:
        assert (ROOT / required).is_file(), f"Missing required Pages input file: {required}"
        assert any(path_matches(required, pattern) for pattern in push_paths), (
            f"Pages workflow path filters omit required input: {required}"
        )
