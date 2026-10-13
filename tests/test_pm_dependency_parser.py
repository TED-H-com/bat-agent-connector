"""Conservative parsing: supported observations, never invented resolution or relationships."""
import json

from bat_agent_connector.pm_dependency_parser import Inventory, workflow
from bat_agent_connector.pm_dependency_paths import allowed_path


def inventory(contents):
    files = [{"path": path, "blob_sha": "a" * 40, "size": len(content.encode()), "sha256": "b" * 64,
              "status": "read", "reason": None} for path, content in contents.items()]
    result = Inventory("fixture/repo", "c" * 40, files, contents).parse()
    return {**result, "files": files}


def test_go_require_checksum_catalog_and_toolchain_are_not_false_lock_verification():
    result = inventory({"go.mod": "module example.com/component\n\ngo 1.22\ntoolchain go1.22.1\nrequire (\n example.com/lib v1.2.3 // indirect\n)\n",
        "go.sum": "example.com/lib v1.2.3 h1:fixture\nexample.com/lib v1.2.3/go.mod h1:fixture\n"})
    assert result["components"][0]["name"] == "example.com/component"
    runtime = [edge for edge in result["edges"] if edge["relation"] == "runtime"]
    assert len(runtime) == 1 and runtime[0]["version"] == "v1.2.3"
    assert runtime[0]["resolved"] is None and runtime[0]["evidence_level"] == "declared"
    assert {e["version"] for e in result["edges"] if e["relation"] == "build"} == {"1.22", "go1.22.1"}


def test_ambiguous_uv_versions_remain_declared_and_bad_structures_are_visible():
    result = inventory({"pyproject.toml": '[project]\nname="sample"\ndependencies=["library>=1"]\n',
        "uv.lock": '[[package]]\nname="library"\nversion="1.0"\n[[package]]\nname="library"\nversion="2.0"\n',
        "other/package.json": '{"name":"invalid","dependencies":[]}', "Cargo.toml": '[package]\nname=42\n'})
    assert len(result["components"]) == 1
    assert result["edges"][0]["resolved"] is None and result["edges"][0]["evidence_level"] == "declared"
    assert {f["path"] for f in result["files"] if f["status"] == "unparsable"} == {"other/package.json", "Cargo.toml"}


def test_cargo_workspace_dependencies_require_explicit_inheritance():
    result = inventory({"Cargo.toml": '[workspace]\nmembers=["crate"]\n[workspace.dependencies]\nserde="1"\n',
        "crate/Cargo.toml": '[package]\nname="crate"\nversion="1.0.0"\npublish=false\n[dependencies]\nserde={workspace=true}\n',
        "Cargo.lock": '[[package]]\nname="serde"\nversion="1.0.200"\n'})
    assert result["components"][0]["published_name"] is None
    edge = result["edges"][0]
    assert edge["relation"] == "runtime" and edge["version"] == "1" and edge["resolved"] == "1.0.200"
    assert {r["path"] for r in edge["evidence_refs"]} == {"Cargo.toml", "crate/Cargo.toml", "Cargo.lock"}


def test_workflow_hints_bind_exact_working_directory_and_commands_not_mentions():
    result = inventory({"web/package.json": json.dumps({"name": "web", "devDependencies": {"vite": "^6", "vitest": "^3"},
        "scripts": {"test": "echo vitest", "build": "echo vite"}}),
        ".github/workflows/ci.yml": '''name: CI
jobs:
  app:
    defaults:
      run:
        working-directory: web
    steps:
      - run: |
          echo 'vitest test'
          vite build
      - run: vitest run
  wrong:
    steps:
      - run: npm test
        working-directory: unrelated
'''})
    names = {d["key"]: d["name"] for d in result["dependencies"]}
    edge = [e for e in result["edges"] if names[e["dependency_key"]] == "vite"][0]
    assert edge["relation"] == "build"
    assert edge["evidence_refs"][1]["job_id"] == "app"
    assert not any(e["basis"] == "npm_script_or_ci" and any("script" in r for r in e["evidence_refs"]) for e in result["edges"])
    assert {h["relation"] for h in result["verification_hints"]} == {"build", "test"}
    assert all(h["job_id"] != "wrong" for h in result["verification_hints"])


def test_lockfile_bins_are_evidence_for_custom_npm_tools():
    result = inventory({"package.json": json.dumps({"name": "web", "devDependencies": {"fixture-tool": "^2"}, "scripts": {"test:ui": "fixture-check --all"}}),
        "package-lock.json": json.dumps({"lockfileVersion": 3, "packages": {"node_modules/fixture-tool": {"version": "2.1.0", "bin": {"fixture-check": "bin/check.js"}}}})})
    edge = result["edges"][0]
    assert edge["relation"] == "test" and edge["resolved"] == "2.1.0"
    assert edge["evidence_refs"][1]["script"] == "test:ui"


def test_node_lock_resolution_uses_component_path_before_hoisted_root():
    result = inventory({"package-lock.json": json.dumps({"lockfileVersion": 3, "packages": {
        "node_modules/lib": {"version": "1.0.0"}, "app/node_modules/lib": {"version": "2.0.0"}}}),
        "app/package.json": '{"name":"app","dependencies":{"lib":"^2"}}'})
    assert result["edges"][0]["resolved"] == "2.0.0"


def test_fixed_allowlist_and_multiline_yaml_locations():
    assert allowed_path("desktop/src-tauri/Cargo.toml")
    assert allowed_path("requirements-dev.txt") and allowed_path(".github/workflows/test.yaml")
    for path in ("../package.json", "node_modules/a/package.json", "x/.venv/pyproject.toml", "a/../../go.mod", "a/.github/workflows/test.yml", "package.json\0"):
        assert not allowed_path(path)
    steps = workflow("jobs:\n  test:\n    steps:\n      - run: |\n          cargo test\n          cargo check\n        working-directory: crate\n")
    assert steps[0]["line"] == 4 and steps[0]["working_directory"] == "crate"
    assert "cargo test" in steps[0]["command"] and "cargo check" in steps[0]["command"]


def test_legacy_named_rust_toolchain_can_contain_toml():
    result = inventory({"Cargo.toml": '[package]\nname="crate"\nversion="1"\n', "rust-toolchain": '[toolchain]\nchannel="1.80"\n'})
    assert result["edges"][0]["version"] == "1.80" and result["edges"][0]["relation"] == "build"


def test_policy_controller_is_not_an_inferred_maintenance_owner(monkeypatch):
    from bat_agent_connector import pm_dependency_reads
    monkeypatch.setattr(pm_dependency_reads.pm_maintenance_policy, "current_policy", lambda *args: {
        "policy": {"policy_id": "policy", "document": {"controller": {"actor": "adopting-owner"}, "policy": {}}}})
    assert pm_dependency_reads._owner(None, None, "workspace") is None


def test_ci_python_module_invocation_is_actual_test_evidence():
    result = inventory({"pyproject.toml": '[project]\nname="sample"\n[dependency-groups]\nchecks=["pytest>=8"]\n',
        ".github/workflows/test.yml": 'jobs:\n  checks:\n    steps:\n      - run: uv run python -m pytest -q\n'})
    assert any(e["relation"] == "test" and e["basis"] == "python_group:checks" for e in result["edges"])


def test_repeated_build_evidence_is_one_relation_with_both_script_references():
    result = inventory({"package.json": json.dumps({"name": "web", "devDependencies": {"vite": "^6"},
        "scripts": {"build": "vite build", "build:browser": "vite build --mode browser"}})})
    assert len(result["edges"]) == 1
    assert {r["script"] for r in result["edges"][0]["evidence_refs"] if "script" in r} == {"build", "build:browser"}
