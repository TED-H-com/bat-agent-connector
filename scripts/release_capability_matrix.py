"""Candidate-scoped capability facts, not a support or release approval policy.

Only coded evidence is produced here. All other states require their own, exact
row receipt. Receipts are attributed observations, not signatures. Source gates
prove implementation presence/absence, never runtime readiness. A producer's
feature object is preserved, not converted from its version to 'supported'.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from scripts.release_identity import _archive, _commit, _git, _write_files

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = ("Browser", "Windows x64", "macOS arm64", "macOS x64", "Linux")
STATES = ("coded", "merged", "integration_tested", "released", "installed", "running", "accepted", "operator")
KINDS = {"merge": "merged", "package_ci": "integration_tested", "integration": "integration_tested",
         "release": "released", "installation": "installed", "process": "running", "acceptance": "accepted",
         "operator_authorization": "operator"}


def make_receipt(kind, subject_sha, row_id, value, source):
    """An explicit observation, with no wildcard or state-promotion semantics."""
    receipt = {"format": 1, "kind": kind, "subject_sha": subject_sha, "row_id": row_id, "value": value, "source": source}
    if not _valid_receipt(receipt):
        raise ValueError("RECEIPT_INVALID")
    return receipt


def _valid_receipt(receipt):
    return (isinstance(receipt, dict)
            and set(receipt) == {"format", "kind", "subject_sha", "row_id", "value", "source"}
            and type(receipt["format"]) is int and receipt["format"] == 1
            and isinstance(receipt["kind"], str) and receipt["kind"] in KINDS and _commit(receipt["subject_sha"]) is not None
            and type(receipt["value"]) is bool
            and all(isinstance(receipt[k], str) and 0 < len(receipt[k]) <= 2048 for k in ("row_id", "source")))


def _evidence(candidate, value="unknown", kind=None, source=None, reason="NO_RECEIPT"):
    return {"value": value, "receipt_kind": kind, "subject_sha": candidate, "source": source, "reason": reason}


def apply_receipts(matrix, receipts):
    """Pure reducer. Invalid, other-subject and conflicting observations fail closed."""
    result = deepcopy(matrix)
    indexed = {item["id"]: item for item in result["rows"]}
    grouped = {}
    for receipt in receipts:
        reason = None
        if not _valid_receipt(receipt):
            reason = "RECEIPT_INVALID"
        elif receipt["subject_sha"] != matrix["candidate"]:
            reason = "SUBJECT_DIFFERS"
        elif receipt["row_id"] not in indexed:
            reason = "ROW_UNKNOWN"
        if reason:
            result["rejected_receipts"].append({"receipt": receipt, "reason": reason})
            continue
        grouped.setdefault((receipt["row_id"], KINDS[receipt["kind"]]), []).append(receipt)
    for (row_id, state), observations in grouped.items():
        values = {item["value"] for item in observations}
        target = indexed[row_id]["evidence"]
        if target[state]["value"] != "unknown":
            values.add(target[state]["value"])
        if len(values) != 1 or target[state]["reason"] == "CONFLICTING_RECEIPTS":
            fact = _evidence(matrix["candidate"], reason="CONFLICTING_RECEIPTS")
        else:
            receipt = observations[0]
            fact = _evidence(matrix["candidate"], receipt["value"], receipt["kind"], receipt["source"], "EXPLICIT_RECEIPT")
        fact["receipts"] = observations + target[state].get("receipts", [])
        target[state] = fact
    return result


def _row(candidate, entry, kind, variant, capability, coded, source, *, declaration=None, availability="unknown",
         reason="SOURCE_DECLARATION", receipt_kind="candidate_source"):
    return {"id": f"{entry}/{kind}/{variant}/{capability}", "entry": entry, "source_kind": kind,
            "variant": variant, "capability": capability, "subject_sha": candidate,
            "availability": availability, "declaration": declaration,
            "evidence": {state: (_evidence(candidate, coded, receipt_kind if coded != "unknown" else None,
                                           source, reason) if state == "coded" else _evidence(candidate)) for state in STATES}}


def _proof(files, path, snippet):
    """Exact source assertion, not a Rust interpreter. Changed/ambiguous code is unknown."""
    source = files.get(path, b"").decode("utf-8")
    if source.count(snippet) != 1:
        return None
    return {"file": path, "line": source[:source.index(snippet)].count("\n") + 1, "assertion": snippet}


def native_facts(files):
    fleet = _proof(files, "desktop/src-tauri/src/fleet.rs", "native_controls: cfg!(windows),")
    windows = _proof(files, "desktop/src-tauri/src/credentials.rs",
                     'impl Vault for OsVault {\n        fn supported(&self) -> bool {\n            true\n        }\n        fn source(&self) -> &\'static str {\n            "windows_credential_manager"\n        }')
    mac = _proof(files, "desktop/src-tauri/src/credentials_macos.rs",
                 'fn supported(&self) -> bool {\n        true\n    }')
    linux = _proof(files, "desktop/src-tauri/src/credentials.rs",
                   '#[cfg(not(any(windows, target_os = "macos")))]\nimpl Vault for OsVault {\n    fn supported(&self) -> bool {\n        false\n    }')
    # Both availability's gate and the implementation module's gate must still agree.
    fleet_module = _proof(files, "desktop/src-tauri/src/main.rs", "#[cfg(windows)]\nmod fleet_native;")
    mac_module = _proof(files, "desktop/src-tauri/src/credentials.rs",
                        '#[cfg(target_os = "macos")]\n#[path = "credentials_macos.rs"]\nmod macos;')
    tray = _proof(files, "desktop/src-tauri/src/main.rs", "let mut tray = TrayIconBuilder::new()")
    bundled = _proof(files, "desktop/src-tauri/src/managed.rs",
                     'let (executable, version) = materialize(&resources.join("managed-runtime"), app_data)?;')
    shared = _proof(files, "desktop/src-tauri/tauri.conf.json", '"frontendDist": "../dist"')
    packages = {}
    for node in ast.parse(files["scripts/ci_scope.py"]).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "NATIVE_BUILDS" for t in node.targets):
            packages = {item["artifact"]: item["bundle"].split(",") for item in ast.literal_eval(node.value)}
    artifacts = {"Windows x64": "desktop-Windows-unsigned", "macOS arm64": "desktop-macOS-arm64-validation",
                 "macOS x64": "desktop-macOS-x64-validation", "Linux": "desktop-Linux-unsigned"}
    result = {}
    for entry in ENTRIES[1:]:
        vault = windows if entry == "Windows x64" else linux if entry == "Linux" else mac if mac_module else None
        package = "nsis" if entry == "Windows x64" else "deb" if entry == "Linux" else "dmg"
        package_proof = _proof(files, "scripts/ci_scope.py", f'"artifact": "{artifacts[entry]}"')
        result[entry] = {
            "native_fleet": {"coded": entry == "Windows x64" if fleet and fleet_module else "unknown",
                             "proof": [fleet, fleet_module]},
            "credential_vault": {"coded": entry != "Linux" if vault else "unknown", "proof": [vault]},
            "tray": {"coded": True if tray else "unknown", "proof": [tray]},
            "bundled_central": {"coded": True if bundled else "unknown", "proof": [bundled]},
            "shared_ui": {"coded": True if shared else "unknown", "proof": [shared]},
            "installer": {"coded": True if package in packages.get(artifacts[entry], []) else "unknown",
                          "proof": [package_proof], "package": package},
        }
    return result


def platform_cells(files):
    """Read table structure only. Never interpret prose as a capability or acceptance."""
    result = []
    lines = files["README.md"].decode().splitlines()
    in_table = False
    for number, line in enumerate(lines, 1):
        if line.startswith("| Entry | Package / local features | Boundary |"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            break
        cells = [part.strip() for part in line.strip("|").split("|")]
        if len(cells) != 3 or cells[0].startswith("---"):
            continue
        for column, text in zip(("features", "boundary"), cells[1:], strict=True):
            result.append({"id": f"README.md:{number}:{column}", "file": "README.md", "line": number,
                           "entry": cells[0], "column": column, "text": text})
    # The site's real producer declares table rows as literal tuples. No execution,
    # HTML copy, or fuzzy keyword matching is needed to inventory their exact cells.
    path = "scripts/build_product_site.py"
    for node in ast.parse(files[path]).body:
        if not isinstance(node, ast.For) or not isinstance(node.target, ast.Tuple):
            continue
        if [getattr(t, "id", None) for t in node.target.elts] != ["en", "zh", "d", "dz", "bound", "bz"]:
            continue
        for values, item in zip(ast.literal_eval(node.iter), node.iter.elts, strict=True):
            en, zh, detail, detail_zh, boundary, boundary_zh = values
            for column, text in (("features", [detail, detail_zh]), ("boundary", [boundary, boundary_zh])):
                result.append({"id": f"{path}:{item.lineno}:{column}", "file": path, "line": item.lineno,
                               "entry": en, "column": column, "text": text})
    return result


def _cell_digest(cell):
    # Only this reviewed cell's bytes, never a digest of all frontend sources.
    return hashlib.sha256(json.dumps(cell, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def reviewed_claim(candidate, cell, entry, capability, expected):
    """Explicit review input; cannot silently survive edited or differently scoped prose.

    A reviewer supplies the interpretation. This is not a generated support policy.
    It is stored with private review evidence, not in the public platform tables.
    """
    if _commit(candidate) is None or type(expected) is not bool or entry not in ENTRIES:
        raise ValueError("CLAIM_INVALID")
    return {"subject_sha": candidate, "cell_id": cell["id"], "cell_sha256": _cell_digest(cell),
            "entry": entry, "capability": capability, "expected": expected}


def consistency(matrix, claims=()):
    cells = {item["id"]: item for item in matrix["platform_cells"]}
    result = {"checked": [], "mismatches": [], "unchecked": []}
    reviewed = set()
    for claim in claims:
        cell = cells.get(claim.get("cell_id"))
        if (cell is None or claim.get("subject_sha") != matrix["candidate"]
                or claim.get("cell_sha256") != _cell_digest(cell) or type(claim.get("expected")) is not bool):
            result["unchecked"].append({"claim": claim, "reason": "REVIEW_NOT_BOUND_TO_CANDIDATE_CELL"})
            continue
        reviewed.add(cell["id"])
        matching = [item for item in matrix["rows"] if item["entry"] == claim.get("entry")
                    and item["capability"] == claim.get("capability") and item["source_kind"] != "server"]
        values = [item["evidence"]["coded"]["value"] for item in matching]
        reason = ("CODED_ROW_MISSING" if not matching else "CODED_UNKNOWN" if "unknown" in values
                  else "CODED_CLAIM_DIFFERS" if any(v != claim["expected"] for v in values) else None)
        observation = {"claim": claim, "source": f'{cell["file"]}:{cell["line"]}',
                       "result": "differs" if reason else "matches", "reason": reason}
        result["checked"].append(observation)
        if reason:
            result["mismatches"].append(observation)
    # A checked capability does not mean every phrase in that cell was checked.
    for cell in cells.values():
        result["unchecked"].append({"cell": cell, "reason": "PROSE_REQUIRES_REVIEW",
                                    "has_explicit_claims": cell["id"] in reviewed})
    return result


def generate(source, candidate, *, scratch, receipts=(), claims=(), tools=ROOT):
    """Generate offline from exact Git objects. Local candidate code must be trusted.

    Unlike a receipt-only validator this executes ApiV1 from the named source, in
    an empty disk sandbox with an allowlisted environment. It never starts HTTP,
    a background daemon, native IPC, BAT, a model or an installer.
    """
    if _commit(candidate) is None:
        raise ValueError("CANDIDATE_INVALID")
    files = _archive(_git(source, "archive", candidate))
    snapshot, state = Path(scratch) / "source", Path(scratch) / "state"
    snapshot.mkdir(parents=True)
    state.mkdir()
    _write_files(snapshot, files)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(state), "TMPDIR": str(state),
           "XDG_CONFIG_HOME": str(state), "XDG_STATE_HOME": str(state), "PYTHONDONTWRITEBYTECODE": "1"}
    helper = ROOT / "scripts/capability_matrix_producers.py"
    program = "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_path(sys.argv.pop(1),run_name='__main__')"
    server = json.loads(subprocess.check_output(  # noqa: S603 -- fixed offline producer, trusted candidate source
        [sys.executable, "-I", "-c", program, str(snapshot / "src"), str(helper), str(state)],
        cwd=state, env=env, timeout=60))
    node = shutil.which("node")
    if node is None:
        raise ValueError("BROWSER_PRODUCER_UNAVAILABLE")
    web = json.loads(subprocess.check_output(  # noqa: S603 -- fixed local script, no shell/network/native IPC
        [node, str(ROOT / "scripts/capability_matrix_browser.mjs"), str(snapshot), str(tools)],
        cwd=state, env=env, timeout=30))
    native = native_facts(files)
    rows = []
    for entry in ENTRIES:
        for variant, declaration in server.items():
            source_ref = {"endpoints": ["/api/v1/capabilities", "/api/v1/bootstrap"],
                          "producer": "src/bat_agent_connector/api_v1.py", "principal": declaration["principal"],
                          "context": "empty Config(hosts={}); temporary disk journal; no network"}
            for capability, value in {**declaration["features"], "artifacts": declaration["artifacts"]}.items():
                rows.append(_row(candidate, entry, "server", variant, capability, True, source_ref,
                                 declaration=value, reason="PRODUCER_FIELD_PRESENT", receipt_kind="api_producer"))
            rows.append(_row(candidate, entry, "server", variant, "managed_installation",
                             declaration["managed_installation_present"], source_ref,
                             reason="PRODUCER_FIELD_PRESENT" if variant == "managed" else "PRODUCER_FIELD_ABSENT",
                             receipt_kind="api_producer"))
    web_source = {"file": "desktop/src/transport/index.ts", "producer": "scripts/capability_matrix_browser.mjs",
                  "adapters": ["locale=en", "empty fetch response; no server fields", "browser without native IPC"]}
    for capability, present in (("shared_ui", web["native_desktop"] is False),
                                ("authenticated_central", web["central_request"] == {"method": "GET", "path": "/api/v1/capabilities"}),
                                ("native_fleet", not web["native_fleet_unavailable"]),
                                ("credential_vault", not web["credential_vault_unavailable"])):
        rows.append(_row(candidate, "Browser", "web", "browser", capability, present, web_source,
                         availability=False if not present else "unknown", receipt_kind="browser_transport_producer",
                         reason="BROWSER_ROUTE_OBSERVED"))
    for entry, capabilities in native.items():
        for capability, fact in capabilities.items():
            rows.append(_row(candidate, entry, "native", "source", capability, fact["coded"], fact["proof"],
                             declaration={"package": fact["package"]} if "package" in fact else None,
                             reason="SOURCE_GATE" if fact["coded"] != "unknown" else "SOURCE_GATE_UNVERIFIED"))
        rows.append(_row(candidate, entry, "native", "runtime", "native_status", "unknown", None,
                         reason="NATIVE_PRODUCER_UNAVAILABLE"))
    result = {"format": 1, "candidate": candidate, "source": "git_objects", "rows": rows,
              "producers": {"server": server, "web": web}, "platform_cells": platform_cells(files), "rejected_receipts": []}
    result = apply_receipts(result, receipts)
    result["consistency"] = consistency(result, claims)
    return result
