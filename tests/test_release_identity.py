"""Local candidate receipts use real package and manifest producers, never host installs."""
from __future__ import annotations

import gzip
import importlib.util
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest

from bat_agent_connector import __version__
from scripts.check_product_site import source_entry
from scripts.validate_connector_update import copy_fixture_source
from tests.package_build import _build, _build_env, _command

ROOT = Path(__file__).resolve().parents[1]
AVAILABLE = ("wheel", "sdist", "dashboard", "managed_runtime", "screenshots", "readme", "site")


def producer(path):
    spec = importlib.util.spec_from_file_location("runtime_manifest_producer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_candidates(directory):
    source_a = directory / "a-source"
    source_a.mkdir()
    copy_fixture_source(ROOT, source_a)  # Package inputs are declared by pyproject.toml.
    shutil.copytree(ROOT / "desktop/src", source_a / "desktop/src")
    shutil.copytree(ROOT / "site", source_a / "site")
    shutil.copy2(ROOT / "README.zh-TW.md", source_a / "README.zh-TW.md")
    (source_a / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/capture_site.mjs", source_a / "scripts/capture_site.mjs")
    env = _build_env(directory)  # copy link mode; forwards configured UV_CACHE_DIR.
    env.update(GIT_AUTHOR_DATE="2020-01-01T00:00:00Z", GIT_COMMITTER_DATE="2020-01-01T00:00:00Z")
    for args in (["git", "init", "-q"], ["git", "add", "."],
                 ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                  "commit", "-qm", "candidate A"]):
        _command(args, cwd=source_a, env=env)
    source_b = directory / "b-source"
    _command(["git", "clone", "-q", "--no-hardlinks", str(source_a), str(source_b)], cwd=directory, env=env)
    for name, marker in (("src/bat_agent_connector/__init__.py", "\n# candidate B\n"),
                         ("src/bat_agent_connector/dashboard/app.js", "\n// candidate B\n"),
                         ("desktop/src/app.js", "\n// candidate B\n"),
                         ("README.md", "\nCandidate B fixture.\n"),
                         ("site/index.html", "\n<!-- candidate B -->\n")):
        with (source_b / name).open("a") as stream:
            stream.write(marker)
    _command(["git", "add", "."], cwd=source_b, env=env)
    _command(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
              "commit", "-qm", "candidate B"], cwd=source_b, env=env)
    from scripts.release_identity import receipt
    sets = []
    for label, source in (("a", source_a), ("b", source_b)):
        output = directory / label
        output.mkdir()
        sha = _command(["git", "rev-parse", "HEAD"], cwd=source, env=env)
        wheel, sdist = _build(source, output), _build(source, output, "sdist")
        runtime = output / "runtime"
        runtime.mkdir()
        # Only the manifest writer is under test, not PyInstaller. Its input
        # is de-identified candidate source bytes, never a host executable.
        executable = runtime / "fixture-runtime"
        executable.write_bytes((source / "src/bat_agent_connector/__init__.py").read_bytes())
        payload = runtime / "runtime.gz"
        writer = producer(ROOT / "desktop/scripts/build-managed-runtime.py")
        writer.write_payload(executable, payload)
        writer.write_manifest(runtime, executable, payload, runtime_version=__version__, build_commit=sha,
                              platform_name="linux", architecture="x86_64", executable="fixture-runtime")
        images = output / "images"
        shutil.copytree(ROOT / "site/images", images)
        original = json.loads((images / "manifest.json").read_text())
        names = ["desktop/src/app.js", "src/bat_agent_connector/dashboard/app.js"]
        parameters = {"sourceCommit": sha,
                      "sourceTree": _command(["git", "rev-parse", "HEAD:desktop/src"], cwd=source, env=env),
                      "capturedAt": "2020-01-01T00:00:00.000Z", "fixtureClock": original["fixture_clock"],
                      "browser": original["browser"], "images": original["images"],
                      "sourceFiles": [source_entry(name, (source / name).read_bytes()) for name in names]}
        # Execute the actual capture producer's metadata statement. Importing
        # capture_site itself would boot Chromium and recapture public PNGs.
        # No copied JS object or invented producer fields live in the test.
        capture = (source / "scripts/capture_site.mjs").read_text()
        start, end = "    const manifest = {", "    await writeFile(path.join(output, 'manifest.json')"
        assert capture.count(start) == 1 and capture.count(end) == 1
        statement = capture[capture.index(start):capture.index(end)]
        script = ("import {execFileSync} from 'node:child_process';"
                  f"const p={json.dumps(parameters)}, sourceCommit=p.sourceCommit, fixtureClock=p.fixtureClock,"
                  "sourceFiles=p.sourceFiles, images=p.images, browser={version:()=>p.browser};"
                  f"const git=(...args)=>execFileSync('git',args,{{cwd:{json.dumps(str(source))},encoding:'utf8'}}).trim();"
                  "const Date=class extends globalThis.Date{constructor(){super(p.capturedAt);}};"
                  + statement + "console.log(JSON.stringify(manifest));")
        document = _command(["node", "--input-type=module", "-e", script], cwd=output, env=env)
        (images / "manifest.json").write_text(document + "\n")
        paths = {"wheel": wheel, "sdist": sdist, "dashboard": wheel,
                 "managed_runtime": runtime / "manifest.json", "screenshots": images / "manifest.json",
                 "readme": source, "site": source}
        sets.append({"source": source, "sha": sha,
                     "receipts": [receipt(name, paths[name]) for name in AVAILABLE]})
    return sets


@pytest.fixture(scope="module")
def candidates(tmp_path_factory):
    built = []

    def get():
        if not built:
            built.append(build_candidates(tmp_path_factory.mktemp("release-candidates")))
        return built[0]

    return get


def test_one_candidate_accepts_every_required_producer_receipt(candidates):
    from scripts.release_identity import validate
    a, _ = candidates()
    result = validate(a["sha"], a["receipts"], source=a["source"], required=AVAILABLE)
    assert result["one_candidate"] is True
    assert result["required"] == list(AVAILABLE)
    assert {row["status"] for row in result["components"]} == {"matches"}
    assert result["refusals"] == []


def test_site_release_guard_does_not_limit_explicit_candidate_validation(candidates, tmp_path, monkeypatch):
    from scripts import check_product_site as site_check
    from scripts.release_identity import validate
    a, _ = candidates()
    claim = next(row for row in a["receipts"] if row["component"] == "screenshots")
    published = site_check.read_screenshot_manifest(ROOT / "site/images/manifest.json")
    assert a["sha"] != published["frontend_source_commit"]
    result = validate(a["sha"], [claim], source=a["source"], required=["screenshots"])
    assert result["one_candidate"] is True
    assert result["components"][0]["status"] == "matches"

    source = tmp_path / "site-source"
    _command(["git", "clone", "-q", "--no-hardlinks", str(a["source"]), str(source)],
             cwd=tmp_path, env=_build_env(tmp_path))
    shutil.copytree(ROOT / "docs", source / "docs")
    shutil.copy2(ROOT / "CHANGELOG.md", source / "CHANGELOG.md")
    shutil.copy2(claim["artifact"], source / "site/images/manifest.json")
    monkeypatch.setattr(site_check, "ROOT", source)
    monkeypatch.setattr(site_check, "SITE", source / "site")
    # Link resolution is separate from this release gate. Keep the real manifest
    # reader, PNG/content checks and local Git source checks, with no fake fields.
    monkeypatch.setattr(site_check, "check_link", lambda *args: None)
    with pytest.raises(SystemExit) as refusal:
        site_check.main()
    assert str(refusal.value) == "Public screenshots must pin published preview source at 437ee6a7."


@pytest.mark.parametrize("component", AVAILABLE)
def test_each_single_component_swap_is_refused_and_named(candidates, component):
    from scripts.release_identity import validate
    a, b = candidates()
    receipts = [next(r for r in b["receipts"] if r["component"] == component) if r["component"] == component else r
                for r in a["receipts"]]
    result = validate(a["sha"], receipts, source=a["source"], required=AVAILABLE)
    assert result["one_candidate"] is False
    assert result["refusals"] == [{"component": component, "status": "differs", "reason": "COMMIT_DIFFERS"}]


@pytest.mark.parametrize("change", ["absent", "missing_claim", "bad_claim", "bad_version", "bad_digest", "missing_artifact"])
def test_missing_or_malformed_evidence_is_unknown(candidates, change, tmp_path):
    from scripts.release_identity import validate
    a, _ = candidates()
    receipts = deepcopy(a["receipts"])
    wheel = receipts[0]
    if change == "absent":
        receipts.pop(0)
    elif change == "missing_claim":
        wheel.pop("commit")
    elif change == "bad_claim":
        wheel["commit"] = "not-a-commit"
    elif change == "bad_version":
        wheel["version"] = None
    elif change == "bad_digest":
        wheel["sha256"] = "not-a-digest"
    else:
        wheel["artifact"] = str(tmp_path / "missing.whl")
    result = validate(a["sha"], receipts, source=a["source"], required=AVAILABLE)
    assert not result["one_candidate"]
    assert result["refusals"][0]["component"] == "wheel"
    assert result["refusals"][0]["status"] == "unknown"


@pytest.mark.parametrize("component", AVAILABLE)
def test_missing_claim_is_unknown_for_every_component(candidates, component):
    from scripts.release_identity import validate
    a, _ = candidates()
    claim = deepcopy(next(row for row in a["receipts"] if row["component"] == component))
    claim.pop("commit")
    result = validate(a["sha"], [claim], source=a["source"], required=[component])
    assert result["refusals"] == [{"component": component, "status": "unknown", "reason": "CLAIM_UNAVAILABLE"}]


def test_tampered_artifact_digest_is_refused(candidates, tmp_path):
    from scripts.release_identity import validate
    a, _ = candidates()
    receipts = deepcopy(a["receipts"])
    artifact = tmp_path / "changed.whl"
    shutil.copy2(receipts[0]["artifact"], artifact)
    with zipfile.ZipFile(artifact, "a") as archive:
        archive.comment = b"changed after receipt"
    receipts[0]["artifact"] = str(artifact)
    result = validate(a["sha"], receipts, source=a["source"], required=AVAILABLE)
    assert result["refusals"] == [{"component": "wheel", "status": "differs", "reason": "ARTIFACT_DIGEST_DIFFERS"}]


def test_default_full_release_does_not_hide_unavailable_native_or_demo_evidence(candidates):
    from scripts.release_identity import validate
    a, _ = candidates()
    result = validate(a["sha"], a["receipts"], source=a["source"])
    assert not result["one_candidate"]
    assert {r["component"] for r in result["refusals"]} == {"desktop", "site_demo"}
    assert {r["status"] for r in result["refusals"]} == {"unknown"}


def test_duplicate_component_receipts_cannot_be_merged_into_a_pass(candidates):
    from scripts.release_identity import validate
    a, b = candidates()
    result = validate(a["sha"], [a["receipts"][0], b["receipts"][0]], source=a["source"], required=["wheel"])
    assert result["refusals"] == [{"component": "wheel", "status": "unknown", "reason": "DUPLICATE_RECEIPT"}]


@pytest.mark.parametrize("candidate", ["a" * 7, "x" * 40])
def test_malformed_candidate_is_unknown(candidates, candidate):
    from scripts.release_identity import validate
    a, _ = candidates()
    result = validate(candidate, a["receipts"], source=a["source"], required=["wheel"])
    assert result["refusals"] == [{"component": "wheel", "status": "unknown", "reason": "CANDIDATE_INVALID"}]


def test_old_runtime_without_a_commit_stays_unknown(candidates, tmp_path):
    from scripts.release_identity import receipt, validate
    a, _ = candidates()
    old = tmp_path / "old"
    current = next(r for r in a["receipts"] if r["component"] == "managed_runtime")
    shutil.copytree(Path(current["artifact"]).parent, old)
    path = old / "manifest.json"
    doc = json.loads(path.read_text())
    doc.pop("build_commit")  # Historical producer shape, not an invented field.
    path.write_text(json.dumps(doc))
    result = validate(a["sha"], [receipt("managed_runtime", path)], source=a["source"], required=["managed_runtime"])
    assert result["refusals"] == [{"component": "managed_runtime", "status": "unknown", "reason": "CLAIM_UNAVAILABLE"}]


def normalized_receipts(receipts):
    return [{**r, "artifact": "<artifact>", "commit": "<commit>", "version": "<version>", "sha256": "<sha256>"}
            for r in receipts]


def test_producer_receipt_golden_is_current(candidates):
    a, _ = candidates()
    golden = ROOT / "tests/fixtures/release-identity.json"
    assert normalized_receipts(a["receipts"]) == json.loads(golden.read_text())


def test_cli_names_every_refusal(candidates, tmp_path):
    a, _ = candidates()
    receipt = tmp_path / "wheel-receipt.json"
    receipt.write_text(json.dumps(a["receipts"][0]))
    result = subprocess.run([sys.executable, "scripts/validate_release_candidate.py", "validate", "--candidate", a["sha"],
                             "--source", str(a["source"]), "--receipt", str(receipt)],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 1
    doc = json.loads(result.stdout)
    assert doc["one_candidate"] is False
    assert {r["component"] for r in doc["refusals"]} == set(doc["required"]) - {"wheel"}


@pytest.mark.parametrize("component", ["wheel", "screenshots", "managed_runtime", "desktop", "site_demo"])
def test_receipt_cli_reports_unreadable_or_unsupported_evidence_as_unknown(tmp_path, component):
    path = tmp_path / "malformed"
    path.write_text("not an archive or JSON")
    result = subprocess.run([sys.executable, "scripts/validate_release_candidate.py", "receipt", component, str(path)],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "unknown"
    assert json.loads(result.stdout)["component"] == component


@pytest.mark.parametrize("component", ["wheel", "dashboard"])
def test_relabelled_package_still_has_to_match_candidate_bytes(candidates, tmp_path, component):
    from scripts.release_identity import receipt, validate
    a, b = candidates()
    wheel_a, wheel_b = a["receipts"][0]["artifact"], b["receipts"][0]["artifact"]
    changed = tmp_path / "relabelled.whl"
    with zipfile.ZipFile(wheel_a) as good, zipfile.ZipFile(wheel_b) as other, zipfile.ZipFile(changed, "w") as output:
        for entry in other.infolist():
            data = good.read(entry.filename) if entry.filename == "bat_agent_connector/build-info.json" else other.read(entry)
            output.writestr(entry, data)
    result = validate(a["sha"], [receipt(component, changed)], source=a["source"], required=[component])
    reason = "PACKAGE_SOURCE_DIFFERS" if component == "wheel" else "DASHBOARD_SOURCE_DIFFERS"
    assert result["refusals"] == [{"component": component, "status": "differs", "reason": reason}]


@pytest.mark.parametrize("field,reason", [("frontend_source_tree", "SCREENSHOT_TREE_DIFFERS"),
                                          ("source_files", "SCREENSHOT_SOURCE_DIFFERS")])
def test_screenshot_source_checks_survive_a_relabelled_commit(candidates, tmp_path, field, reason):
    from scripts.release_identity import receipt, validate
    a, b = candidates()
    paths = [Path(next(r for r in candidate["receipts"] if r["component"] == "screenshots")["artifact"])
             for candidate in (a, b)]
    images = tmp_path / "images"
    shutil.copytree(paths[0].parent, images)
    doc, other = json.loads(paths[0].read_text()), json.loads(paths[1].read_text())
    doc[field] = other[field]  # Negative case mutates only real producer fields.
    path = images / "manifest.json"
    path.write_text(json.dumps(doc))
    result = validate(a["sha"], [receipt("screenshots", path)], source=a["source"], required=["screenshots"])
    assert result["refusals"] == [{"component": "screenshots", "status": "differs", "reason": reason}]


def test_runtime_payload_cannot_be_changed_under_an_old_receipt(candidates, tmp_path):
    from scripts.release_identity import validate
    a, _ = candidates()
    old = next(r for r in a["receipts"] if r["component"] == "managed_runtime")
    target = tmp_path / "runtime"
    shutil.copytree(Path(old["artifact"]).parent, target)
    (target / "runtime.gz").write_bytes(gzip.compress(b"changed payload", mtime=0))
    claim = {**old, "artifact": str(target / "manifest.json")}
    result = validate(a["sha"], [claim], source=a["source"], required=["managed_runtime"])
    assert result["refusals"] == [{"component": "managed_runtime", "status": "differs", "reason": "RUNTIME_PAYLOAD_DIFFERS"}]


def runtime_manifest_fixture(directory):
    source, payload = directory / "runtime", directory / "runtime.gz"
    source.write_bytes(b"de-identified runtime manifest fixture\n")
    writer = producer(ROOT / "desktop/scripts/build-managed-runtime.py")
    writer.write_payload(source, payload)
    return writer.write_manifest(
        directory, source, payload, runtime_version="fixture-1", build_commit="0" * 40,
        platform_name="linux", architecture="x86_64", executable="batc-managed-runtime")


def test_runtime_manifest_golden_is_current(tmp_path):
    actual = runtime_manifest_fixture(tmp_path)
    assert actual == json.loads((ROOT / "tests/fixtures/runtime-manifest.json").read_text())


@pytest.mark.parametrize("component", ["readme", "site"])
def test_source_receipt_version_must_come_from_candidate_metadata(candidates, tmp_path, component):
    from scripts.release_identity import receipt, validate
    a, _ = candidates()
    source = tmp_path / "source"
    env = _build_env(tmp_path)
    _command(["git", "clone", "-q", "--no-hardlinks", str(a["source"]), str(source)], cwd=tmp_path, env=env)
    metadata = source / "pyproject.toml"
    metadata.write_text(metadata.read_text().replace(f'version = "{__version__}"', 'version = "999.0.0"', 1))
    # The source files and Git HEAD still match A. The real receipt producer
    # must not let unrelated, modified project metadata relabel their version.
    claim = receipt(component, source)
    assert claim["version"] == "999.0.0"
    result = validate(a["sha"], [claim], source=a["source"], required=[component])
    assert result["refusals"] == [{"component": component, "status": "differs", "reason": "DOCUMENT_VERSION_DIFFERS"}]


def test_validation_only_reads_inputs_and_runs_fixed_local_git(candidates, monkeypatch):
    from scripts.release_identity import validate
    a, _ = candidates()

    def snapshot():
        roots = [a["source"], Path(a["receipts"][0]["artifact"]).parent.parent]
        return {str(path): (path.stat().st_mtime_ns, path.read_bytes())
                for root in roots for path in root.rglob("*") if path.is_file()}

    before = snapshot()
    run, commands = subprocess.run, []

    def local_git(args, **kwargs):
        assert Path(args[0]).name == "git"
        assert args[1:3] == ["-C", str(a["source"])]
        assert args[3] in {"archive", "rev-parse", "ls-tree"}
        commands.append(args)
        return run(args, **kwargs)

    def no_network(*args, **kwargs):
        pytest.fail("validator attempted network access")

    monkeypatch.setattr(subprocess, "run", local_git)
    monkeypatch.setattr(socket, "create_connection", no_network)
    try:
        assert validate(a["sha"], a["receipts"], source=a["source"], required=AVAILABLE)["one_candidate"]
        assert commands
    finally:
        assert snapshot() == before


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Regenerate normalized producer receipts on disk; no UI capture.")
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="release-goldens-", dir=args.scratch) as temporary:
        directory = Path(temporary)
        a, _ = build_candidates(directory)
        (ROOT / "tests/fixtures/release-identity.json").write_text(
            json.dumps(normalized_receipts(a["receipts"]), indent=2) + "\n")
        (ROOT / "tests/fixtures/runtime-manifest.json").write_text(
            json.dumps(runtime_manifest_fixture(directory), indent=2) + "\n")
