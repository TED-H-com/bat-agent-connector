"""Candidate facts come from producers; evidence states never promote each other."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.release_capability_matrix import (
    ENTRIES,
    STATES,
    apply_receipts,
    consistency,
    generate,
    make_receipt,
    native_facts,
    reviewed_claim,
)
from scripts.release_identity import _archive, _git, _write_files

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests/fixtures/release-capability-producers.json"


@pytest.fixture(scope="module")
def matrix(tmp_path_factory):
    candidate = _git(ROOT, "rev-parse", "HEAD").decode().strip()
    return generate(ROOT, candidate, scratch=tmp_path_factory.mktemp("matrix"))


def row(matrix, entry, capability, source_kind=None):
    return next(item for item in matrix["rows"] if item["entry"] == entry
                and item["capability"] == capability
                and (source_kind is None or item["source_kind"] == source_kind))


def test_real_producers_match_golden(matrix):
    # Generator: scripts/generate_capability_matrix.py --producer-golden PATH.
    # Only de-identified producer declarations, no source-tree digest or clock.
    assert matrix["producers"] == json.loads(GOLDEN.read_text())
    assert matrix["producers"]["server"]["managed"]["bootstrap_capabilities_equal"] is True
    assert matrix["producers"]["server"]["plain"]["bootstrap_capabilities_equal"] is True


def test_browser_has_no_native_fleet_or_vault(matrix):
    for capability in ("native_fleet", "credential_vault"):
        item = row(matrix, "Browser", capability, "web")
        assert item["availability"] is False
        assert item["evidence"]["coded"]["value"] is False
        assert item["evidence"]["running"]["value"] == "unknown"
    assert matrix["producers"]["web"]["central_request"] == {"method": "GET", "path": "/api/v1/capabilities"}
    assert matrix["producers"]["web"]["authenticated_request"] is True


def test_package_ci_only_supplies_its_own_state_and_row(matrix):
    selected = row(matrix, "Windows x64", "native_fleet")
    receipt = make_receipt("package_ci", matrix["candidate"], selected["id"], True, "fixture://ci/job")
    result = apply_receipts(matrix, [receipt])
    actual = row(result, "Windows x64", "native_fleet")
    assert actual["evidence"]["integration_tested"]["value"] is True
    for state in ("merged", "released", "installed", "running", "accepted", "operator"):
        assert actual["evidence"][state]["value"] == "unknown"
    assert row(result, "macOS arm64", "native_fleet")["evidence"]["integration_tested"]["value"] == "unknown"
    assert selected["evidence"]["integration_tested"]["value"] == "unknown"  # Pure reducer.


def test_release_for_another_sha_does_not_count(matrix):
    item = matrix["rows"][0]
    other = ("a" if matrix["candidate"][0] != "a" else "b") * 40
    receipt = make_receipt("release", other, item["id"], True, "fixture://release")
    result = apply_receipts(matrix, [receipt])
    assert result["rows"][0]["evidence"]["released"]["value"] == "unknown"
    assert result["rejected_receipts"][0]["reason"] == "SUBJECT_DIFFERS"


def test_unknown_stays_unknown_and_all_subjects_are_candidate(matrix):
    assert {item["entry"] for item in matrix["rows"]} == set(ENTRIES)
    for item in matrix["rows"]:
        assert item["subject_sha"] == matrix["candidate"]
        for state, evidence in item["evidence"].items():
            assert evidence["subject_sha"] == matrix["candidate"]
            if state != "coded":
                assert evidence["value"] == "unknown"
                assert evidence["receipt_kind"] is None
    native = row(matrix, "macOS arm64", "native_status")
    assert native["evidence"]["coded"]["value"] == "unknown"
    assert native["evidence"]["coded"]["reason"] == "NATIVE_PRODUCER_UNAVAILABLE"
    assert native["availability"] == "unknown"


@pytest.mark.parametrize("kind,state", [
    ("merge", "merged"), ("integration", "integration_tested"), ("release", "released"),
    ("installation", "installed"), ("process", "running"), ("acceptance", "accepted"),
    ("operator_authorization", "operator"),
])
def test_each_state_requires_its_own_receipt(matrix, kind, state):
    target = matrix["rows"][0]
    receipt = make_receipt(kind, matrix["candidate"], target["id"], False, "fixture://explicit-observation")
    result = apply_receipts(matrix, [receipt])
    assert result["rows"][0]["evidence"][state]["value"] is False
    for other in STATES:
        if other not in {"coded", state}:
            assert result["rows"][0]["evidence"][other]["value"] == "unknown"


def test_malformed_or_conflicting_receipts_cannot_promote_state(matrix):
    target = matrix["rows"][0]
    receipt = make_receipt("package_ci", matrix["candidate"], target["id"], True, "fixture://ci")
    malicious = {**receipt, "installed": True, "operator": True}
    result = apply_receipts(matrix, [malicious])
    assert result["rejected_receipts"][0]["reason"] == "RECEIPT_INVALID"
    assert result["rows"][0]["evidence"]["integration_tested"]["value"] == "unknown"
    result = apply_receipts(matrix, [receipt, {**receipt, "value": False}])
    assert result["rows"][0]["evidence"]["integration_tested"]["value"] == "unknown"
    assert result["rows"][0]["evidence"]["integration_tested"]["reason"] == "CONFLICTING_RECEIPTS"
    assert apply_receipts(matrix, [{**receipt, "kind": {}}])["rejected_receipts"][0]["reason"] == "RECEIPT_INVALID"


def test_source_gates_prove_code_only(matrix):
    assert row(matrix, "Windows x64", "native_fleet")["evidence"]["coded"]["value"] is True
    for entry in ("macOS arm64", "macOS x64", "Linux"):
        item = row(matrix, entry, "native_fleet")
        assert item["evidence"]["coded"]["value"] is False
        assert item["availability"] == "unknown"  # No native runtime producer ran.
    assert row(matrix, "Linux", "credential_vault")["evidence"]["coded"]["value"] is False
    for entry in ("Windows x64", "macOS arm64", "macOS x64"):
        assert row(matrix, entry, "credential_vault")["evidence"]["coded"]["value"] is True


def test_native_ipc_registration_is_coded_but_not_a_runtime_observation(matrix):
    for entry in ENTRIES[1:]:
        for command in ("native_status", "native_files_status", "desktop_update", "managed_control"):
            item = row(matrix, entry, "ipc." + command, "native")
            assert item["evidence"]["coded"]["value"] is True
            assert item["availability"] == "unknown"
            assert item["evidence"]["running"]["value"] == "unknown"


def test_unrecognised_native_gate_is_unknown_not_another_platforms_fact():
    candidate = _git(ROOT, "rev-parse", "HEAD").decode().strip()
    files = _archive(_git(ROOT, "archive", candidate))
    path = "desktop/src-tauri/src/credentials.rs"
    files[path] = files[path].replace(b"#[cfg(windows)]\nmod windows {", b"#[cfg(any())]\nmod windows {")
    facts = native_facts(files)
    assert facts["Windows x64"]["credential_vault"]["coded"] == "unknown"
    assert facts["macOS arm64"]["credential_vault"]["coded"] is True


def test_server_declaration_versions_do_not_become_readiness(matrix):
    item = row(matrix, "Browser", "pm_workspaces", "server")
    assert item["declaration"]["version"] == 1
    assert item["declaration"]["runner"] is False
    assert item["availability"] == "unknown"
    assert item["evidence"]["running"]["value"] == "unknown"


def test_table_claims_are_reviewed_bytes_not_prose_guesses(matrix):
    # A review receipt binds an explicit interpretation to an exact candidate cell.
    # New/changed prose is unchecked; no keyword parser turns wording into support.
    cells = matrix["platform_cells"]
    browser = next(cell for cell in cells if cell["entry"] == "Browser" and cell["column"] == "boundary")
    claim = reviewed_claim(matrix["candidate"], browser, "Browser", "native_fleet", False)
    result = consistency(matrix, [claim])
    assert not result["mismatches"]
    assert result["checked"][0]["result"] == "matches"
    wrong = {**claim, "expected": True}
    assert consistency(matrix, [wrong])["mismatches"][0]["reason"] == "CODED_CLAIM_DIFFERS"
    missing = {**claim, "capability": "not-declared"}
    assert consistency(matrix, [missing])["mismatches"][0]["reason"] == "CODED_ROW_MISSING"
    changed = deepcopy(matrix)
    changed["platform_cells"] = [{**cell, "text": "Edited platform prose"} for cell in cells]
    assert consistency(changed, [claim])["unchecked"]
    assert not consistency(changed, [claim])["checked"]


def test_both_readmes_and_site_are_inventoried_and_missing_table_is_unchecked(matrix):
    assert len(matrix["platform_cells"]) == 24
    assert {cell["file"] for cell in matrix["platform_cells"]} == {
        "README.md", "README.zh-TW.md", "scripts/build_product_site.py"}
    missing = {**matrix, "platform_cells": []}
    assert len(consistency(missing)["unchecked"]) == 3
    assert all(item["reason"] == "PLATFORM_TABLE_UNAVAILABLE" for item in consistency(missing)["unchecked"])


def test_candidate_parser_is_shared_and_dirty_files_do_not_supply_facts(matrix, tmp_path):
    with pytest.raises(ValueError):
        generate(ROOT, "not-a-sha", scratch=tmp_path)
    assert matrix["source"] == "git_objects"
    assert matrix["producers"]["web"]["native_desktop"] is False


def test_dirty_candidate_checkout_cannot_supply_producer_facts(matrix, tmp_path):
    # A real temporary Git source, not a monkeypatched file reader. Only the
    # working copy is changed after committing the real producer source.
    source = tmp_path / "checkout"
    source.mkdir()
    _write_files(source, _archive(_git(ROOT, "archive", matrix["candidate"])))
    _git(source, "init", "-q")
    _git(source, "add", ".")
    _git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "candidate")
    candidate = _git(source, "rev-parse", "HEAD").decode().strip()
    (source / "desktop/src/transport/index.ts").write_text("invalid dirty source")
    (source / "src/bat_agent_connector/api_v1.py").write_text("invalid dirty source")
    result = generate(source, candidate, scratch=tmp_path / "probe")
    assert result["producers"] == matrix["producers"]
    assert all(item["subject_sha"] == candidate for item in result["rows"])
