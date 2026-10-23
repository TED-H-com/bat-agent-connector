"""A slow shard must retain live diagnostics without changing test outcomes."""

from __future__ import annotations

import hashlib
import json
import shlex
import sys
from pathlib import Path

import pytest
import yaml

pytest_plugins = ["pytester"]
ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]


def workflow_test_step():
    return next(step for step in workflow()["pytest"]["steps"] if step.get("name", "").startswith("Tests ("))


def test_step_budget_preserves_upload_and_diagnostic_only_watchdog():
    job = workflow()["pytest"]
    step = workflow_test_step()
    # Run 39922072602: 39 completed observations; max Tests=1705s,
    # setup=11s, post-test=6s. Reserve a whole minute for upload/cleanup.
    assert job["timeout-minutes"] == 45
    assert 1705 * 1.5 < step["timeout-minutes"] * 60
    assert 11 + step["timeout-minutes"] * 60 + 60 < 45 * 60
    assert step["timeout-minutes"] == 43
    assert not step.get("continue-on-error")
    assert "--ci-watchdog-seconds=600" in step["run"]
    assert 600 >= 3 * 163.23  # Slowest phase in attempt 1's --durations=15.
    assert "-p no:faulthandler" in step["run"]
    assert "faulthandler_timeout=" not in step["run"]
    assert step["shell"] == "bash"
    upload = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/upload-artifact@"))
    assert job["steps"].index(upload) > job["steps"].index(step)
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "ci-results/"
    assert upload["with"]["if-no-files-found"] == "error"
    complete = workflow()["pytest-complete"]
    gate = complete["steps"][-1]
    assert gate["env"]["SHARDS_RESULT"] == "${{ needs.pytest.result }}"
    assert '--shards-result "$SHARDS_RESULT"' in gate["run"]


def prepare(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    pytester.makeini("[pytest]\n")
    (pytester.path / "weights.json").write_text(json.dumps({"schema_version": 1, "files": {}}))


def run_step(pytester):
    # Exercise the actual workflow's shell/tee/exit propagation, with only the
    # interpreter, partition and diagnostic threshold adapted to this tiny suite.
    command = workflow_test_step()["run"].replace("uv run pytest", shlex.join([sys.executable, "-m", "pytest"]))
    command = command.replace("${{ matrix.shard }}", "1").replace("--ci-shard-count=5", "--ci-shard-count=1")
    command = command.replace("--ci-watchdog-seconds=600", "--ci-watchdog-seconds=1")
    command = command.replace("--durations=15", "--durations=15 --ci-durations=weights.json")
    return pytester.run("bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", command)


def test_watchdog_and_progress_are_live_across_all_phases(pytester, monkeypatch):
    prepare(pytester, monkeypatch)
    pytester.makeconftest('''
        import faulthandler
        import json
        from pathlib import Path
        import pytest

        @pytest.hookimpl(trylast=True)
        def pytest_configure(config):
            original = faulthandler.dump_traceback_later
            def record_arm(*args, **kwargs):
                with Path("armed.jsonl").open("a") as out:
                    out.write(json.dumps({"timeout": args[0], "exit": kwargs.get("exit", False)}) + "\\n")
                return original(*args, **kwargs)
            faulthandler.dump_traceback_later = record_arm
            config.add_cleanup(lambda: setattr(faulthandler, "dump_traceback_later", original))
    ''')
    pytester.makepyfile(test_slow='''
        import json
        import threading
        import time
        from pathlib import Path
        import pytest

        def background_wait(done):
            done.wait()

        @pytest.fixture(scope="session", autouse=True)
        def another_thread():
            done = threading.Event()
            thread = threading.Thread(target=background_wait, args=(done,))
            thread.start()
            yield
            done.set()
            thread.join()

        def check_live(request, phase):
            lines = Path("ci-results/progress.jsonl").read_text().splitlines()
            last = json.loads(lines[-1])
            assert last["event"] == "start"
            assert last["phase"] == phase
            assert last["nodeid"] == request.node.nodeid
            assert "test_sleeping" in last["nodeid"]
            assert all(len(line.encode()) <= 8192 for line in lines)
            # Record what was visible BEFORE this phase returned.
            with Path("live-snapshots.jsonl").open("a") as out:
                out.write(json.dumps(last) + "\\n")

        def sleeping_frame(request, phase):
            time.sleep(1.2 if request.param == phase else 0.4)
            if request.param == phase or phase == "teardown":
                check_live(request, phase)

        @pytest.fixture(params=["setup", "call", "teardown", "cumulative"])
        def slow(request):
            if request.param in ("setup", "cumulative"):
                sleeping_frame(request, "setup")
            yield request
            if request.param in ("teardown", "cumulative"):
                sleeping_frame(request, "teardown")

        def test_sleeping(slow):
            if slow.param in ("call", "cumulative"):
                sleeping_frame(slow, "call")
    ''')
    result = run_step(pytester)
    result.assert_outcomes(passed=4)
    assert result.ret == 0
    stderr = result.stderr.str()
    dump = (pytester.path / "ci-results/tracebacks.log").read_text()
    for output in (stderr, dump):
        assert output.count("Timeout (0:00:01)!") >= 4
        assert "sleeping_frame" in output
        assert "background_wait" in output  # All threads, not just the active test.
    assert "4 passed" in (pytester.path / "ci-results/pytest.log").read_text()
    arms = [json.loads(line) for line in (pytester.path / "armed.jsonl").read_text().splitlines()]
    assert arms == [{"timeout": 1.0, "exit": False}] * 4  # Exactly one timer per entire test.
    snapshots = [json.loads(line) for line in (pytester.path / "live-snapshots.jsonl").read_text().splitlines()]
    assert [row["phase"] for row in snapshots] == ["setup", "call", "teardown", "teardown"]
    progress = [json.loads(line) for line in (pytester.path / "ci-results/progress.jsonl").read_text().splitlines()]
    assert [(row["phase"], row["event"]) for row in progress] == [
        (phase, event) for _ in range(4) for phase in ("setup", "call", "teardown") for event in ("start", "finish")
    ]


def test_failed_tests_keep_exit_code_and_bounded_progress(pytester, monkeypatch):
    prepare(pytester, monkeypatch)
    pytester.makepyfile(test_failure='''
        import time
        import pytest
        @pytest.fixture
        def slow_cleanup():
            yield
            sleeping_teardown()
        def sleeping_teardown():
            time.sleep(1.2)
        @pytest.mark.parametrize("value", [0], ids=["long-" + "字" * 10000])
        def test_original_failure(value, slow_cleanup):
            assert value == 1
    ''')
    result = run_step(pytester)
    result.assert_outcomes(failed=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED  # tee must not mask failure.
    assert "sleeping_teardown" in result.stderr.str()
    assert "sleeping_teardown" in (pytester.path / "ci-results/tracebacks.log").read_text()
    lines = (pytester.path / "ci-results/progress.jsonl").read_text().splitlines()
    assert len(lines) == 6
    assert all(len(line.encode()) <= 8192 for line in lines)
    nodeid = json.loads((pytester.path / "ci-results/manifest.json").read_text())["selected_nodeids"][0]
    for line in lines:
        row = json.loads(line)
        assert row["nodeid"].startswith("test_failure.py::test_original_failure[")
        assert row["nodeid_sha256"] == hashlib.sha256(nodeid.encode()).hexdigest()


def test_plugin_without_options_creates_no_diagnostics(pytester, monkeypatch):
    prepare(pytester, monkeypatch)
    pytester.makepyfile("def test_plain(): pass")
    pytester.runpytest_subprocess("-q", "-p", "tests.ci_shard_plugin").assert_outcomes(passed=1)
    assert not (pytester.path / "ci-results").exists()


@pytest.mark.parametrize("seconds,builtin,message", [
    ("1", True, "only one timer"),
    ("0", False, "finite positive"),
    ("nan", False, "finite positive"),
    ("inf", False, "finite positive"),
])
def test_watchdog_rejects_competing_timer_and_invalid_threshold(pytester, monkeypatch, seconds, builtin, message):
    prepare(pytester, monkeypatch)
    pytester.makepyfile("def test_plain(): pass")
    result = pytester.runpytest_subprocess(
        "-p", "tests.ci_shard_plugin", *([] if builtin else ["-p", "no:faulthandler"]),
        "--ci-shard-index=1", "--ci-shard-count=1", "--ci-shard-manifest=ci-results/manifest.json",
        f"--ci-watchdog-seconds={seconds}",
    )
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert message in result.stderr.str()
    assert not (pytester.path / "ci-results").exists()
