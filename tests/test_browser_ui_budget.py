"""Browser UI shards keep headroom for slow package mirrors."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_browser_shard_budget_keeps_mirror_headroom():
    job = yaml.safe_load((ROOT / ".github/workflows/desktop.yml").read_text())["jobs"]["browser-ui"]
    # Run 43036319167: shards took 14-18 min; shard 2 spent 7 min installing
    # packages from a slow mirror and was cancelled at the former 20-minute
    # limit with 122 of 1,329 tests left. Run 43044727191: 13-18 min.
    slowest_shard, slow_mirror = 18, 7
    assert job["timeout-minutes"] == 30
    assert slowest_shard * 1.5 <= job["timeout-minutes"]
    assert slowest_shard + slow_mirror < job["timeout-minutes"]
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["matrix"]["shard"] == [1, 2, 3, 4, 5]
    run = next(step["run"] for step in job["steps"] if step.get("name") == "Shared browser and native-transport UI tests")
    assert "--shard=${{ matrix.shard }}/5" in run
    # The job limit must not replace Playwright's own per-test limits.
    assert "--timeout" not in run and "--global-timeout" not in run
