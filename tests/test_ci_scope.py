"""PR scope narrows runner matrices, never the tests or their completeness proof."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.ci_complete import verify_manifests
from scripts.ci_scope import FULL_PYTHONS, native_builds, select_scope
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
    (PR_157_PATHS, ["3.10"], "linux"),
    (["desktop/src/app.js", "desktop/src/app.css", "desktop/tests/dashboard.spec.ts"], ["3.10"], "none"),
    (["src/bat_agent_connector/service.py"], FULL_PYTHONS, "all"),
    (["src/bat_agent_connector/service.pyi"], FULL_PYTHONS, "all"),
    (["uv.lock"], FULL_PYTHONS, "all"),
    (["pyproject.toml"], FULL_PYTHONS, "all"),
    ([".github/workflows/ci.yml"], FULL_PYTHONS, "all"),
    (["docs/design/desktop.md", "CONTRIBUTING.md", "CHANGELOG.md"], ["3.10"], "none"),
    (["desktop/src-tauri/src/lib.rs"], ["3.10"], "all"),
    (["desktop/fleet-core/Cargo.lock"], ["3.10"], "all"),
    ([], FULL_PYTHONS, "all"),
    (["unknown-top-level"], ["3.10"], "all"),
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
    assert select_scope([path])[:2] == (FULL_PYTHONS, "all")


@pytest.mark.parametrize("path", [
    "desktop/src/app.js", "desktop/src/transport/index.ts", "desktop/src/pm-kanban.css",
    "desktop/tests/dashboard.spec.ts", "desktop/tests/managed-setup.test.ts",
    "desktop/tests/demo/register.ts", "desktop/tests/register-status-fixture.ts",
    "desktop/tests/new-browser-helper.ts",
    "docs/design/desktop.md", "docs/product/implementation-status.md",
    "AGENTS.md", "CHANGELOG.md", "CONTRIBUTING.md", "README.zh-TW.md", "desktop/DESIGN.md",
])
def test_each_native_allowlist_entry(path):
    assert select_scope([path])[:2] == (["3.10"], "none")


@pytest.mark.parametrize("path", [
    "THIRD_PARTY_NOTICES.md", "README.md", "desktop/vendor/glib-0.18.5/README.md",
    "desktop/package.json", "desktop/package-lock.json", "desktop/vite.config.ts",
    "desktop/index.html", "desktop/src-tauri/tauri.conf.json",
    "desktop/tests/macos-installed-smoke.mjs", "desktop/tests/windows-bundle.mjs",
    "desktop/tests/fixtures/fleet-contract.ps1", "desktop/tests/fixtures/fleet-status.json",
    "desktop/tests/new-native-fixture.swift", "desktop/tests/managed-runtime-fixture.py",
    "desktop/tests/fixtures/nested/data.json", "desktop/tests/fixtures/script.js",
    "desktop/tests/helper.ts.json", "desktop/src-other/input.js", "docs-other/design.md",
])
def test_packaged_inputs_native_fixtures_and_unknown_paths_require_builds(path):
    assert select_scope([path])[1] == "all"


@pytest.mark.parametrize("asset", ["index.html", "app.js", "app.css", "i18n.js"])
def test_generated_dashboard_assets_keep_linux_package_validation(asset):
    paths = ["desktop/src/app.js", "docs/design/desktop.md", f"src/bat_agent_connector/dashboard/{asset}"]
    assert select_scope(paths)[:2] == (["3.10"], "linux")


def test_one_native_path_keeps_all_builds_even_with_dashboard_assets():
    assert select_scope(["src/bat_agent_connector/dashboard/app.js", "desktop/src-tauri/src/lib.rs"])[:2] == (
        ["3.10"], "all",
    )


def test_dashboard_prefix_does_not_allow_neighboring_package_data():
    assert select_scope(["src/bat_agent_connector/dashboard-extra/app.js"])[1] == "all"


@pytest.mark.parametrize("event_name", ["push", "workflow_dispatch", "merge_group", ""])
def test_non_pr_events_keep_full_coverage(event_name):
    assert select_scope(["desktop/src/app.js"], event_name=event_name)[:2] == (FULL_PYTHONS, "all")


def test_unavailable_diff_keeps_full_coverage_even_with_partial_paths():
    assert select_scope(["desktop/src/app.js"], diff_failed=True)[:2] == (FULL_PYTHONS, "all")


@pytest.mark.parametrize("path", ['"desktop/src/name\\n.py"', "/docs/example.md", "docs/../example.md",
                                        "docs//example.md", "./docs/example.md"])
def test_ambiguous_paths_keep_full_coverage(path):
    assert select_scope([path])[:2] == (FULL_PYTHONS, "all")


# Original native entries, captured from desktop.yml before moving their single
# production definition into ci_scope.py. Assert complete dictionaries, not counts.
EXPECTED_BUILDS = {
    "none": [],
    "linux": [{"os": "ubuntu-24.04", "bundle": "deb", "artifact": "desktop-Linux-unsigned"}],
    "all": [
        {"os": "ubuntu-24.04", "bundle": "deb", "artifact": "desktop-Linux-unsigned"},
        {"os": "windows-latest", "bundle": "nsis", "artifact": "desktop-Windows-unsigned"},
        {"os": "macos-15", "bundle": "app,dmg", "artifact": "desktop-macOS-arm64-validation"},
        {"os": "macos-15-intel", "bundle": "app,dmg", "artifact": "desktop-macOS-x64-validation"},
    ],
}


@pytest.mark.parametrize("level", ["none", "linux", "all"])
def test_generated_build_list_has_exact_entries_and_no_stray_combinations(level):
    builds = json.loads(json.dumps(native_builds(level)))
    assert builds == EXPECTED_BUILDS[level]
    assert len({entry["os"] for entry in builds}) == len(builds)
    assert all(set(entry) == {"os", "bundle", "artifact"} for entry in builds)


def test_desktop_build_matrix_uses_complete_scope_output_without_hardcoded_entries():
    jobs = yaml.safe_load((ROOT / ".github/workflows/desktop.yml").read_text())["jobs"]
    assert jobs["scope"]["outputs"]["builds"] == "${{ steps.select.outputs.builds }}"
    build = jobs["build"]
    assert build["needs"] == "scope"
    assert build["if"] == "${{ needs.scope.outputs.native != 'none' }}"
    assert build["strategy"]["matrix"] == {"include": "${{ fromJSON(needs.scope.outputs.builds) }}"}
    assert build["runs-on"] == "${{ matrix.os }}"
    assert build["name"] == "build (${{ matrix.os }}, ${{ matrix.bundle }}, ${{ matrix.artifact }})"
    assert any(
        step.get("uses", "").startswith("actions/upload-artifact@")
        and step["with"]["name"] == "${{ matrix.artifact }}"
        for step in build["steps"]
    )


def test_unknown_native_level_is_rejected():
    with pytest.raises(ValueError, match="Unknown native level"):
        native_builds("unknown")


@pytest.mark.parametrize("paths,args,pythons,native,fallback", [
    ("desktop/src/app.js\n", [], ["3.10"], "none", ""),
    ("src/bat_agent_connector/service.py\n", [], FULL_PYTHONS, "all", ""),
    ("src/bat_agent_connector/dashboard/app.js\n", [], ["3.10"], "linux", ""),
    ("\n", [], FULL_PYTHONS, "all", "Empty path list"),
    ("desktop/src/app.js\n", ["--event-name", "push"], FULL_PYTHONS, "all", "Non-PR event"),
    ("desktop/src/app.js\n", ["--diff-failed"], FULL_PYTHONS, "all", "PR diff unavailable"),
])
@pytest.mark.parametrize("workflow", ["ci", "desktop"])
def test_stdlib_cli_outputs_and_workflow_specific_reason(tmp_path, paths, args, pythons, native, fallback, workflow):
    summary = tmp_path / "summary.md"
    summary.write_text("Existing summary\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-S", str(ROOT / "scripts/ci_scope.py"), "--workflow", workflow, *args],
        input=paths, text=True, capture_output=True, check=True,
        env={**os.environ, "GITHUB_EVENT_NAME": "pull_request", "GITHUB_STEP_SUMMARY": str(summary)},
    )
    assert result.stdout.splitlines() == [
        f"pythons={json.dumps(pythons)}", f"native={native}", f"builds={json.dumps(EXPECTED_BUILDS[native])}",
    ]
    lines = summary.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and lines[0] == "Existing summary"
    if workflow == "ci":
        reason = "Python source/tooling changed" if pythons == FULL_PYTHONS else "No Python source/tooling changed"
        assert (fallback or reason) in lines[1]
        assert "5 shards each" in lines[1] and "native" not in lines[1].lower()
    else:
        reason = {
            "none": "all paths on native allowlist",
            "linux": "generated dashboard assets need packaged Linux fixtures",
            "all": "path outside native allowlist and generated dashboard assets",
        }[native]
        assert (fallback or reason) in lines[1]
        assert {"none": "Native builds skipped", "linux": "Linux native build only",
                "all": "All four native builds"}[native] in lines[1]
        assert "pytest" not in lines[1] and "Python" not in lines[1]
    if fallback:
        assert fallback in lines[1]


def test_cli_event_environment_keeps_main_full(tmp_path):
    result = subprocess.run(
        [sys.executable, "-S", str(ROOT / "scripts/ci_scope.py")],
        input="desktop/src/app.js\n", text=True, capture_output=True, check=True,
        env={**os.environ, "GITHUB_EVENT_NAME": "push", "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")},
    )
    assert result.stdout.splitlines() == [
        f"pythons={json.dumps(FULL_PYTHONS)}", "native=all", f"builds={json.dumps(EXPECTED_BUILDS['all'])}",
    ]


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


def test_merge_first_parent_diff_excludes_changes_only_on_main(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output([
            "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-C", str(repo), *args,
        ], text=True, stderr=subprocess.DEVNULL).strip()

    git("init", "--initial-branch=main")
    (repo / "README.md").write_text("Base\n")
    git("add", ".")
    git("commit", "-m", "Base")
    git("branch", "pr")
    backend = repo / "src/bat_agent_connector/service.py"
    backend.parent.mkdir(parents=True)
    backend.write_text("# Only changed on main\n")
    git("add", ".")
    git("commit", "-m", "Backend on main")
    main_tip = git("rev-parse", "HEAD")
    git("checkout", "pr")
    frontend = repo / "desktop/src/app.js"
    frontend.parent.mkdir(parents=True)
    frontend.write_text("// PR change\n")
    git("add", ".")
    git("commit", "-m", "Frontend on PR")
    pr_tip = git("rev-parse", "HEAD")
    git("checkout", "main")
    git("merge", "--no-ff", "pr", "-m", "Synthetic PR merge")
    old_diff = git("diff", "--no-renames", "--name-only", main_tip, pr_tip).splitlines()
    merge_diff = git("diff", "--no-renames", "--name-only", "HEAD^1", "HEAD").splitlines()
    assert "src/bat_agent_connector/service.py" in old_diff
    assert select_scope(old_diff)[:2] == (FULL_PYTHONS, "all")
    assert merge_diff == ["desktop/src/app.js"]
    assert select_scope(merge_diff)[:2] == (["3.10"], "none")
