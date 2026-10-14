"""PR scope narrows runner matrices, never the tests or their completeness proof."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci_complete import verify_manifests
from scripts.ci_scope import FULL_PYTHONS, select_scope
from scripts.ci_sharding import collection_digest

ROOT = Path(__file__).resolve().parents[1]
# Captured with git diff --name-only 8a50a717^1 8a50a717 (#157).
# Keep the real list here so shallow CI checkouts do not need historical Git objects.
PR_157_PATHS = """CHANGELOG.md
desktop/DESIGN.md
desktop/src/pm-kanban-strings.js
desktop/src/pm-kanban.css
desktop/src/pm-kanban.js
desktop/src/pm-register-strings.js
desktop/src/pm-register.css
desktop/src/pm-register.js
desktop/src/pm-work-inspector.js
desktop/tests/board-experience.spec.ts
desktop/tests/demo/register.ts
desktop/tests/issues-experience.spec.ts
desktop/tests/pm-kanban.spec.ts
desktop/tests/register-board-states.spec.ts
desktop/tests/register-exact-status.spec.ts
desktop/tests/register-status-fixture.ts
desktop/tests/requirements-review.spec.ts
src/bat_agent_connector/dashboard/app.css
src/bat_agent_connector/dashboard/app.js""".splitlines()


@pytest.mark.parametrize("paths,pythons,native", [
    (PR_157_PATHS, ["3.10"], True),
    (["desktop/src/app.js", "desktop/src/app.css", "desktop/tests/dashboard.spec.ts"], ["3.10"], False),
    (["src/bat_agent_connector/service.py"], FULL_PYTHONS, True),
    (["src/bat_agent_connector/service.pyi"], FULL_PYTHONS, True),
    (["uv.lock"], FULL_PYTHONS, True),
    (["pyproject.toml"], FULL_PYTHONS, True),
    ([".github/workflows/ci.yml"], FULL_PYTHONS, True),
    (["docs/design/desktop.md", "CONTRIBUTING.md", "CHANGELOG.md"], ["3.10"], False),
    (["desktop/src-tauri/src/lib.rs"], ["3.10"], True),
    (["desktop/fleet-core/Cargo.lock"], ["3.10"], True),
    ([], FULL_PYTHONS, True),
    (["unknown-top-level"], ["3.10"], True),
])
def test_classification_table(paths, pythons, native):
    assert select_scope(paths)[:2] == (pythons, native)


@pytest.mark.parametrize("path", [
    ".python-version", "uv.toml", "pytest.ini", "tox.ini", "setup.cfg", ".coveragerc",
    "requirements-dev.txt", "tools/requirements.txt", "tests/conftest.py",
    "tests/ci_shard_plugin.py", "tests/ci_durations.json", "tests/fixtures/example.json",
    "scripts/run-tests.sh", "scripts/ci_scope.py", "desktop/scripts/ci-central-ui.sh",
])
def test_python_tooling_and_fixture_data_keep_full_matrix(path):
    assert select_scope([path])[:2] == (FULL_PYTHONS, True)


@pytest.mark.parametrize("path", [
    "desktop/src/app.js", "desktop/src/transport/index.ts", "desktop/src/pm-kanban.css",
    "desktop/tests/dashboard.spec.ts", "desktop/tests/managed-setup.test.ts",
    "docs/design/desktop.md", "docs/product/implementation-status.md",
    "AGENTS.md", "CHANGELOG.md", "CONTRIBUTING.md", "README.zh-TW.md", "desktop/DESIGN.md",
])
def test_each_native_allowlist_entry(path):
    assert select_scope([path])[:2] == (["3.10"], False)


@pytest.mark.parametrize("path", [
    "src/bat_agent_connector/dashboard/index.html", "src/bat_agent_connector/dashboard/app.js",
    "src/bat_agent_connector/dashboard/app.css", "src/bat_agent_connector/dashboard/i18n.js",
    "THIRD_PARTY_NOTICES.md", "README.md", "desktop/vendor/glib-0.18.5/README.md",
    "desktop/package.json", "desktop/package-lock.json", "desktop/vite.config.ts",
    "desktop/index.html", "desktop/src-tauri/tauri.conf.json",
    "desktop/tests/macos-installed-smoke.mjs", "desktop/tests/windows-bundle.mjs",
    "desktop/tests/fixtures/fleet-contract.ps1", "desktop/tests/fixtures/fleet-status.json",
    "desktop/tests/new-native-fixture.ts", "desktop/src-other/input.js", "docs-other/design.md",
])
def test_packaged_inputs_native_fixtures_and_unknown_paths_require_builds(path):
    assert select_scope([path])[1] is True


@pytest.mark.parametrize("event_name", ["push", "workflow_dispatch", "merge_group", ""])
def test_non_pr_events_keep_full_coverage(event_name):
    assert select_scope(["desktop/src/app.js"], event_name=event_name)[:2] == (FULL_PYTHONS, True)


def test_unavailable_diff_keeps_full_coverage_even_with_partial_paths():
    assert select_scope(["desktop/src/app.js"], diff_failed=True)[:2] == (FULL_PYTHONS, True)


@pytest.mark.parametrize("path", ['"desktop/src/name\\n.py"', "/docs/example.md", "docs/../example.md",
                                        "docs//example.md", "./docs/example.md"])
def test_ambiguous_paths_keep_full_coverage(path):
    assert select_scope([path])[:2] == (FULL_PYTHONS, True)


@pytest.mark.parametrize("paths,args,pythons,native,reason", [
    ("desktop/src/app.js\n", [], ["3.10"], False, "all paths on native allowlist"),
    ("src/bat_agent_connector/service.py\n", [], FULL_PYTHONS, True, "Python source/tooling changed"),
    ("src/bat_agent_connector/dashboard/app.js\n", [], ["3.10"], True, "path outside native allowlist"),
    ("\n", [], FULL_PYTHONS, True, "Empty path list"),
    ("desktop/src/app.js\n", ["--event-name", "push"], FULL_PYTHONS, True, "Non-PR event"),
    ("desktop/src/app.js\n", ["--diff-failed"], FULL_PYTHONS, True, "PR diff unavailable"),
])
def test_stdlib_cli_outputs_and_visible_reason(tmp_path, paths, args, pythons, native, reason):
    summary = tmp_path / "summary.md"
    summary.write_text("Existing summary\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-S", str(ROOT / "scripts/ci_scope.py"), *args],
        input=paths, text=True, capture_output=True, check=True,
        env={**os.environ, "GITHUB_EVENT_NAME": "pull_request", "GITHUB_STEP_SUMMARY": str(summary)},
    )
    assert result.stdout.splitlines() == [f"pythons={json.dumps(pythons)}", f"native={str(native).lower()}"]
    lines = summary.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and lines[0] == "Existing summary"
    assert reason in lines[1] and "5 shards each" in lines[1]
    assert ("all four native builds" if native else "native builds skipped") in lines[1]


def test_cli_event_environment_keeps_main_full(tmp_path):
    result = subprocess.run(
        [sys.executable, "-S", str(ROOT / "scripts/ci_scope.py")],
        input="desktop/src/app.js\n", text=True, capture_output=True, check=True,
        env={**os.environ, "GITHUB_EVENT_NAME": "push", "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")},
    )
    assert result.stdout.splitlines() == [f"pythons={json.dumps(FULL_PYTHONS)}", "native=true"]


@pytest.mark.parametrize("paths", [["desktop/src/app.js"], ["tests/conftest.py"]])
def test_selected_versions_still_require_all_five_shards(paths):
    versions, _, _ = select_scope(paths)
    nodeids = [f"tests/test_{index}.py::test_example" for index in range(1, 6)]
    manifests = [{
        "schema_version": 1, "python_version": version, "shard_index": index, "shard_count": 5,
        "collection_count": 5, "collection_sha256": collection_digest(nodeids),
        "selected_nodeids": [nodeid],
    } for version in versions for index, nodeid in enumerate(nodeids, 1)]
    assert len(verify_manifests(manifests, versions, 5)) == len(versions)
    with pytest.raises(ValueError, match="missing shards"):
        verify_manifests(manifests[:-1], versions, 5)
    with pytest.raises(ValueError, match="missing shards"):
        verify_manifests([item for item in manifests if item["python_version"] != versions[-1]], versions, 5)
