"""CI must partition the unchanged collection exactly once, preserving module fixtures."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.ci_complete import main as complete_main
from scripts.ci_complete import verify_manifests
from scripts.ci_durations import rebuild
from scripts.ci_sharding import assign_files, collection_digest, load_weights

pytest_plugins = ["pytester"]


def test_longest_first_balanced_and_deterministic():
    weights = {"d.py": 4, "c.py": 4, "b.py": 6, "a.py": 6}
    expected = {"a.py": 1, "b.py": 2, "c.py": 1, "d.py": 2}
    assert assign_files(list(weights), weights, 2) == expected
    assert assign_files(list(reversed(weights)), dict(reversed(list(weights.items()))), 2) == expected
    assert [sum(weights[path] for path, shard in expected.items() if shard == index) for index in (1, 2)] == [10, 10]


def test_missing_file_uses_table_median():
    weights = {"a.py": 2, "b.py": 4, "c.py": 10, "old.py": 100}
    # Median of the table is 7, so new.py is placed between c.py and b.py.
    assert assign_files(["a.py", "b.py", "c.py", "new.py"], weights, 2) == {
        "c.py": 1, "new.py": 2, "b.py": 2, "a.py": 1,
    }


def test_empty_table_and_zero_weights_still_distribute_files():
    assert assign_files(["c", "b", "a"], {}, 2) == {"a": 1, "b": 2, "c": 1}
    assert assign_files(["c", "b", "a"], {"a": 0, "b": 0, "c": 0}, 2) == {"a": 1, "b": 2, "c": 1}
    with pytest.raises(ValueError, match="positive"):
        assign_files([], {}, 0)


@pytest.mark.parametrize("weight", [-1, True, "3", float("inf"), float("nan")])
def test_invalid_weights_are_rejected(tmp_path, weight):
    table = tmp_path / "weights.json"
    table.write_text(json.dumps({"schema_version": 1, "files": {"a.py": weight}}))
    with pytest.raises(ValueError, match="invalid weight"):
        load_weights(table)


def manifests(version="3.10"):
    nodeids = ["tests/test_a.py::test_one[中文]", "tests/test_a.py::test_two", "tests/test_b.py::test_three"]
    return [{
        "schema_version": 1, "python_version": version, "shard_index": index, "shard_count": 2,
        "collection_count": len(nodeids), "collection_sha256": collection_digest(nodeids),
        "selected_nodeids": selected,
    } for index, selected in enumerate([nodeids[:2], nodeids[2:]], 1)]


def test_complete_partition_and_version_specific_collections():
    both = manifests() + manifests("3.14")
    both[-1]["selected_nodeids"].append("tests/test_b.py::test_only_on_314")
    full = both[2]["selected_nodeids"] + both[3]["selected_nodeids"]
    for manifest in both[2:]:
        manifest.update(collection_count=len(full), collection_sha256=collection_digest(full))
    assert len(verify_manifests(both, ["3.10", "3.14"], 2)) == 2
    assert collection_digest(full) == collection_digest(list(reversed(full)))


@pytest.mark.parametrize("change,match", [
    ("missing-shard", "missing shards"), ("missing-version", "missing shards"),
    ("doubled-shard", "doubled manifest"), ("doubled-test", "doubled test"),
    ("doubled-within-shard", "doubled test"), ("missing-test", "union does not equal"),
    ("substituted-test", "union does not equal"), ("mismatched-hash", "mismatched full collection"),
    ("mismatched-count", "mismatched full collection"), ("mismatched-shards", "mismatched shard count"),
    ("split-file", "test file split"), ("unknown-version", "Python version"),
])
def test_incomplete_or_doubled_partition_is_rejected(change, match):
    data = deepcopy(manifests())
    versions = ["3.10"]
    if change == "missing-shard":
        data.pop()
    elif change == "missing-version":
        versions.append("3.14")
    elif change == "doubled-shard":
        data.append(deepcopy(data[0]))
    elif change == "doubled-test":
        data[1]["selected_nodeids"].append(data[0]["selected_nodeids"][0])
    elif change == "doubled-within-shard":
        data[0]["selected_nodeids"] *= 2
    elif change == "missing-test":
        data[0]["selected_nodeids"].pop()
    elif change == "substituted-test":
        data[1]["selected_nodeids"][0] = "tests/test_other.py::test_other"
    elif change == "mismatched-hash":
        data[1]["collection_sha256"] = "0" * 64
    elif change == "mismatched-count":
        data[1]["collection_count"] += 1
    elif change == "mismatched-shards":
        data[1]["shard_count"] += 1
    elif change == "split-file":
        data[1]["selected_nodeids"].append(data[0]["selected_nodeids"].pop())
    elif change == "unknown-version":
        data[1]["python_version"] = "other"
    with pytest.raises(ValueError, match=match):
        verify_manifests(data, versions, 2)


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped"])
def test_complete_cli_requires_success_even_with_complete_manifests(tmp_path, monkeypatch, result):
    for manifest in manifests():
        folder = tmp_path / str(manifest["shard_index"])
        folder.mkdir()
        (folder / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr("sys.argv", ["ci_complete", str(tmp_path), "--python", "3.10", "--shards", "2",
                                       "--shards-result", result])
    assert complete_main() == (0 if result == "success" else 1)


def test_complete_cli_rejects_missing_and_malformed_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["ci_complete", str(tmp_path), "--python", "3.10", "--shards", "2"])
    assert complete_main() == 1
    (tmp_path / "manifest.json").write_text("{")
    assert complete_main() == 1


@pytest.fixture
def sample_suite(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    pytester.makeini("[pytest]\n")
    pytester.makepyfile(test_a="""
        import pytest
        @pytest.mark.parametrize('value', [1, 2])
        def test_one(value):
            assert value > 0
    """, test_b="""
        def test_two():
            pass
    """)
    table = pytester.path / "weights.json"
    table.write_text(json.dumps({"schema_version": 1, "files": {"test_a.py": 2, "test_b.py": 1}}))
    return pytester, table


def test_plugin_no_options_leaves_local_behavior_unchanged(sample_suite):
    pytester, _ = sample_suite
    pytester.runpytest_subprocess("-q").assert_outcomes(passed=3)
    pytester.runpytest_subprocess("-q", "-p", "tests.ci_shard_plugin").assert_outcomes(passed=3)
    assert sorted(path.name for path in pytester.path.glob("*.json")) == ["weights.json"]


def test_plugin_preserves_whole_files_and_full_collection_on_collect_only(sample_suite):
    pytester, table = sample_suite
    data = []
    for index, selected in [(1, 2), (2, 1)]:
        output = pytester.path / str(index) / "manifest.json"
        result = pytester.runpytest_subprocess("--collect-only", "-q", "-p", "tests.ci_shard_plugin",
            f"--ci-shard-index={index}", "--ci-shard-count=2", f"--ci-shard-manifest={output}", f"--ci-durations={table}")
        assert result.ret == 0
        manifest = json.loads(output.read_text())
        assert len(manifest["selected_nodeids"]) == selected
        data.append(manifest)
    version = data[0]["python_version"]
    assert len(verify_manifests(data, [version], 2)) == 1
    # A normal run emits file attributes and includes parameterized tests in JUnit.
    junit = pytester.path / "junit.xml"
    pytester.runpytest_subprocess("-q", "-p", "tests.ci_shard_plugin", "--ci-shard-index=1", "--ci-shard-count=2",
        f"--ci-shard-manifest={output}", f"--ci-durations={table}", "-o", "junit_family=xunit1",
        f"--junitxml={junit}").assert_outcomes(passed=2, deselected=1)
    assert 'file="test_a.py"' in junit.read_text()


@pytest.mark.parametrize("options", [
    ["--ci-shard-index=1"], ["--ci-shard-count=2"],
    ["--ci-shard-index=0", "--ci-shard-count=2", "--ci-shard-manifest=manifest.json"],
    ["--ci-shard-index=3", "--ci-shard-count=2", "--ci-shard-manifest=manifest.json"],
])
def test_plugin_rejects_incomplete_or_invalid_options(sample_suite, options):
    pytester, _ = sample_suite
    result = pytester.runpytest_subprocess("-p", "tests.ci_shard_plugin", *options)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*CI sharding requires*"])


def test_junit_refresh_sums_tests_then_takes_median_across_versions(tmp_path):
    for version, times in [("310", (1, 3)), ("313", (2, 6))]:
        folder = tmp_path / version
        folder.mkdir()
        (folder / "junit.xml").write_text(f'''<testsuites><testsuite>
          <testcase file="tests/test_a.py" time="{times[0]}" />
          <testcase file="tests/test_a.py" time="{times[1]}" />
          <testcase file="tests/test_b.py" time="0"><skipped /></testcase>
        </testsuite></testsuites>''')
    assert rebuild(tmp_path) == {"schema_version": 1, "basis": "junit-seconds-median",
                                 "files": {"tests/test_a.py": 6.0, "tests/test_b.py": 0.0}}


@pytest.mark.parametrize("attributes", ['time="1"', 'file="tests/../outside.py" time="1"',
                                        'file="tests/test_a.py" time="NaN"'])
def test_junit_refresh_rejects_unusable_reports(tmp_path, attributes):
    (tmp_path / "junit.xml").write_text(f"<testsuite><testcase {attributes}/></testsuite>")
    with pytest.raises(ValueError):
        rebuild(tmp_path)


def test_junit_refresh_requires_reports(tmp_path):
    with pytest.raises(ValueError, match="no junit.xml"):
        rebuild(tmp_path)
