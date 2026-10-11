"""W06 integration into an existing PR head, with real git, a bare remote and a fake GitHub (plan §14, C01-C03)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from bat_agent_connector import (
    api_auth,
    checkpoints,
    cli,
    delivery,
    integration,
    pr_delivery,
    registry,
    resource_policy,
)
from bat_agent_connector.config import ConfigError, parse_config
from bat_agent_connector.operations import AmbiguousOutcome, OperationError
from bat_agent_connector.task_daemon import TaskDaemon
from tests.fakegithub import TOKEN, FakeGitHub
from tests.operation_helpers import settle_operations
from tests.test_checkpoints import MANUAL, LocalRunner, RealGitLog, bat_git_status, bat_writes, git

OPERATOR = api_auth.Principal("operator-dashboard", frozenset({"observe", "operate", "start", "integrate", "merge"}))
_GIT = tuple(int(x) for x in re.findall(r"\d+", subprocess.run(["git", "version"], capture_output=True,
                                                               text=True).stdout)[:2])
pytestmark = pytest.mark.skipif(_GIT < (2, 40), reason="integration tests need git 2.40 or later")


class RaceRunner(LocalRunner):
    """Runs host scripts locally, keyed by their `# batc-int:<kind>` tag, with switches for races and lost replies."""

    def __init__(self) -> None:
        super().__init__()
        self.ran: Counter = Counter()
        self.inject: dict[str, str] = {}
        self.before: dict[str, object] = {}
        self.after: dict[str, object] = {}
        self.lose_before: set[str] = set()
        self.lose_after: set[str] = set()
        self.lose_nth: dict[str, int] = {}  # lose the reply of the n-th run of this kind

    async def run(self, host: str, script: str, timeout_s: float | None = None) -> str:
        m = re.match(r"# batc-int:(\S+)", script)
        kind = m.group(1) if m else None
        if kind in self.inject:
            script = script.replace("# batc-int:before-push", self.inject[kind])
        if kind in self.before:
            self.before.pop(kind)()
        if kind in self.lose_before:
            self.lose_before.discard(kind)
            raise AmbiguousOutcome("reply lost before running")
        out = await super().run(host, script, timeout_s)
        self.ran[kind] += 1
        if kind in self.after:
            self.after.pop(kind)()
        if kind in self.lose_after or self.lose_nth.get(kind) == self.ran[kind]:
            self.lose_after.discard(kind)
            raise AmbiguousOutcome("reply lost after running")
        return out

    def of(self, kind: str) -> list[str]:
        return [s for s in self.scripts if s.startswith(f"# batc-int:{kind} ")]


def commit(repo, path: str, text: str, msg: str) -> str:
    (Path(repo) / path).write_text(text)
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def tree_digest(path) -> str:
    h = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(root, f)
            h.update(os.path.relpath(p, path).encode() + b"\0")
            if not os.path.islink(p):
                h.update(Path(p).read_bytes())
    return h.hexdigest()


class World:
    def __init__(self, tmp: Path, mock, gh: FakeGitHub) -> None:
        self.tmp, self.mock, self.gh = tmp, mock, gh
        self.remote = tmp / "remote.git"
        git(tmp, "init", "-q", "--bare", str(self.remote))
        git(self.remote, "config", "core.logAllRefUpdates", "always")
        seed = tmp / "seed"
        git(tmp, "clone", "-q", str(self.remote), str(seed))
        commit(seed, "a.txt", "one\ntwo\nthree\n", "base a")
        self.main = commit(seed, "b.txt", "bee\n", "base b")
        git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")
        git(seed, "checkout", "-q", "-b", "feature-1")
        self.f1 = commit(seed, "c.txt", "sea\n", "feature work")
        git(seed, "push", "-q", "origin", "feature-1")
        self.seed = seed
        self.sync_pull()
        self.human = tmp / "human" / "app"
        git(tmp, "clone", "-q", str(self.remote), str(self.human))
        git(self.human, "checkout", "-q", "-b", "operator-work", "origin/feature-1")
        self.H = commit(self.human, "h.txt", "operator\n", "Operator's unpushed work")
        (self.human / "b.txt").write_text("bee (being edited)\n")  # a modified tracked file
        (self.human / "scratch.txt").write_text("untracked\n")
        mock.metas[MANUAL] = {"cwd": str(self.human), "isStreaming": False}
        mock.git_logs = RealGitLog()
        mock.handlers["git:status"] = bat_git_status
        mock.git_branch[str(self.human)] = "operator-work"
        gh.add_pr(1, self.f1, head_ref="feature-1")
        gh.track_remote(1, str(self.remote))
        (tmp / "managed").mkdir()
        self.runner = RaceRunner()
        self.keys = 0
        self.daemons: list[TaskDaemon] = []
        self.d = self.daemon()

    def config(self, managed: Path | None = None, **repo_extra):
        host = {"url": self.mock.url, "fingerprint": self.mock.fingerprint, "token_ref": "env:BATC_TEST_TOKEN",
                "writes": True, "orchestrate": True, "orchestrate_register_tabs": False, "orchestrate_max_sessions": 4,
                "managed_roots": [str(managed or self.tmp / "managed")]}
        integrate = {"hosts": ["h1"], "remote_url": str(self.remote), **repo_extra}
        return parse_config({"hosts": {"h1": host}, "safety": {"write_min_interval_s": 0},
                             "github": {"token_ref": "env:FAKE_GH_TOKEN", "api_url": self.gh.url, "wait_max_s": 600,
                                        "repos": [{"repository": "o/r", "integrate": integrate}]}})

    def daemon(self, managed: Path | None = None, **repo_extra) -> TaskDaemon:
        d = TaskDaemon(self.config(managed, **repo_extra), self.tmp / "tasks.db")
        d.ops.context["git_runner"] = self.runner
        self.daemons.append(d)
        return d

    def sync_pull(self) -> None:
        git(self.remote, "update-ref", "refs/pull/1/head", git(self.remote, "rev-parse", "refs/heads/feature-1"))

    def remote_head(self, ref: str = "feature-1") -> str:
        return git(self.remote, "rev-parse", f"refs/heads/{ref}")

    def reflog(self, ref: str = "feature-1") -> int:
        return len(git(self.remote, "reflog", "show", f"refs/heads/{ref}").splitlines())

    def third_party_push(self, path: str = "x.txt", text: str = "theirs\n", base: str | None = None) -> str:
        git(self.seed, "fetch", "-q", "origin")
        git(self.seed, "checkout", "-q", "-B", "feature-1", base or "origin/feature-1")
        sha = commit(self.seed, path, text, "someone else's push")
        git(self.seed, "push", "-q", "-f" if base else "-q", "origin", "feature-1")
        self.sync_pull()
        return sha

    async def run(self, action, target, params=None, pre=None, key=None, principal=OPERATOR):
        self.keys += 1
        op, _ = self.d.ops.create(principal, action=action, target=target, params=params or {},
                                  preconditions=pre or {}, idempotency_key=key or f"{action}-{self.keys}")
        return await self.settle(op["operation_id"])

    async def settle(self, op_id, rounds=8):
        for _ in range(rounds):
            self.d.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op_id,))
            await settle_operations(self.d.ops)
            op = self.d.ops.get(op_id)
            if op["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
                return op
        return self.d.ops.get(op_id)

    async def checkpoint(self, **params) -> dict:
        await self.d.inventory.refresh_host("h1")
        op = await self.run("checkpoint.create", {"host": "h1", "session_id": MANUAL}, {"last_n": 3, **params})
        assert op["status"] == "succeeded", op
        return checkpoints.get(self.d.journal.db, op["result"]["checkpoint_id"])

    async def agent_result(self, cp: dict, files=(("a.txt", "one\ntwo\nthree\nagent\n"),)) -> tuple[str, str, str]:
        """checkpoint.continue, then the agent commits in its worktree with plain git (its own work)."""
        op = await self.run("checkpoint.continue", {"checkpoint_id": cp["checkpoint_id"]}, {"instructions": "go"})
        assert op["status"] == "succeeded", op
        wt = op["result"]["worktree_path"]
        git(wt, "config", "user.email", "agent@example.invalid")
        git(wt, "config", "user.name", "agent")
        sha = None
        for path, text in files:
            sha = commit(wt, path, text, f"agent: {path}")
        return op["operation_id"], sha, wt

    async def preview(self, sources, key=None, expected=None, sync=True) -> dict:
        if sync:  # GitHub moves refs/pull/<n>/head after every push to the PR branch
            self.sync_pull()
        op = await self.run("integration.preview", {"host": "h1", "repository": "o/r", "pull_number": 1},
                            {"sources": sources}, {"expected_head_sha": expected} if expected else None, key=key)
        assert op["status"] == "succeeded", (op["error_code"], op["status_reason"],
                                             [(x["name"], x["status"], x.get("error")) for x in op.get("steps") or []])
        return op["result"]

    async def apply(self, doc: dict, **kw) -> dict:
        return await self.run("integration.apply", {"host": "h1", "repository": "o/r", "pull_number": 1},
                              {"preview_id": doc["preview_id"]},
                              {"expected_head_sha": doc["target"]["head_sha"], "preview_digest": doc["digest"]},
                              key="integrate." + doc["preview_id"], **kw)

    def receipts(self, op_id: str) -> list[dict]:
        return integration.receipts(self.d.journal.db, op_id)

    def area(self) -> Path:
        return next((self.tmp / "managed" / ".batc-integration").iterdir()) / "repo.git"


@pytest.fixture(autouse=True)
def hermetic_git(tmp_path, monkeypatch):
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\tname = Test\n\temail = test@example.invalid\n[init]\n\tdefaultBranch = main\n"
                   "[commit]\n\tgpgsign = false\n[protocol]\n\tversion = 2\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return cfg


@pytest.fixture
def gh():
    fake = FakeGitHub()
    fake.start()
    yield fake
    fake.stop()


@pytest.fixture
def world(tmp_path, mock, gh, monkeypatch):
    monkeypatch.setenv("FAKE_GH_TOKEN", TOKEN)
    w = World(Path(os.path.realpath(tmp_path)), mock, gh)
    yield w
    for d in w.daemons:
        d.journal.close()


# --------------------------------------------------------------------------- C01
async def test_c01_person_and_agent_results_land_in_one_existing_pr_head(world):
    w = world
    cp = await w.checkpoint()
    run_id, agent_sha, wt = await w.agent_result(cp)
    clone = str(Path(wt).parent.parent)
    clone_before = (git(clone, "for-each-ref"), git(clone, "config", "--local", "--list"))
    other = w.third_party_push("y.txt", "moved on\n")  # the PR moved past both sources' base: both need a merge
    human_before = tree_digest(w.human)
    reflog_before = w.reflog()
    refs_before = git(w.remote, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
    w.mock.invokes.clear()

    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]},
                           {"kind": "checkpoint_run", "id": run_id}], expected=other)
    assert doc["ready"], doc["blocking"]
    assert [s["predicted"] for s in doc["sources"]] == ["merge", "merge"]
    assert doc["target"]["push_access"] == "ok" and doc["target"]["head_sha"] == other
    assert [c["sha"] for c in doc["sources"][0]["commits"]] == [w.H]
    assert [c["origin"] for c in doc["sources"][1]["commits"]] == ["from_seq_1", "own"]  # H came with the agent

    op = await w.apply(doc)
    assert op["status"] == "succeeded", op
    r = op["result"]
    new = w.remote_head()
    assert new == r["new_head"] == w.gh.pulls[1]["head"]["sha"] and r["old_head"] == other and r["pushed"]
    first_parents = git(w.remote, "rev-list", "--first-parent", f"{other}..{new}").splitlines()
    assert len(first_parents) == 2  # merge(merge(old, H), A)
    for sha in (w.H, agent_sha, other):
        subprocess.run(["git", "-C", str(w.remote), "merge-base", "--is-ancestor", sha, new], check=True)
    for c in first_parents:
        body = git(w.remote, "log", "-1", "--format=%an <%ae> %at%n%B", c)
        assert body.startswith(f"BAT Connector <bat-connector@noreply.invalid> {int(op['created_at'])}")
        assert "Batc-Operation: " + op["operation_id"] in body and str(w.tmp) not in body
    rec = w.receipts(op["operation_id"])
    assert [(x["status"], x["location_class"], x["method"]) for x in rec] == [
        ("delivered", "human_checkout", "merge"), ("delivered", "managed_clone", "merge")]
    assert tree_digest(w.human) == human_before and not (w.human / ".git" / "FETCH_HEAD").exists()
    assert (git(clone, "for-each-ref"), git(clone, "config", "--local", "--list")) == clone_before
    refs_after = git(w.remote, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
    assert set(refs_after) - set(refs_before) == {f"refs/heads/feature-1 {new}"}  # only the PR head moved
    assert w.reflog() == reflog_before + 1
    assert bat_writes(w.mock) == []
    assert r["pushed_via"]["credential"] == "host" and r["local_checkouts_changed"] is False
    kinds = [e["kind"] for e in w.d.journal.api_events(0, 200, resource_type="integration")["events"]]
    assert kinds.count("integration.delivered") == 1 and "integration.updated" in kinds


async def test_c01_single_source_ahead_of_the_pr_fast_forwards_without_a_connector_commit(world):
    w = world
    cp = await w.checkpoint()
    run_id, agent_sha, _ = await w.agent_result(cp)
    doc = await w.preview([{"kind": "checkpoint_run", "id": run_id}])
    src = doc["sources"][0]
    assert src["predicted"] == "fast_forward" and [c["origin"] for c in src["commits"]] == ["foreign", "own"]
    assert [x["code"] for x in src["warnings"]] == ["BRINGS_FOREIGN_COMMITS"]  # the user's H comes with it, and says so
    op = await w.apply(doc)
    assert op["status"] == "succeeded", op
    assert w.remote_head() == agent_sha == op["result"]["new_head"]
    assert [(x["method"], x["status"]) for x in w.receipts(op["operation_id"])] == [("fast_forward", "delivered")]
    # Exactly the previewed commits entered: the user's H and the agent's A, no connector commit.
    assert git(w.remote, "rev-list", f"{w.f1}..{agent_sha}").split() == [agent_sha, w.H]


async def test_c01_pick_copies_only_selected_commits_with_the_source_relation(world):
    w = world
    cp = await w.checkpoint()
    run_id, _, wt = await w.agent_result(cp, files=(("d.txt", "d1\n"), ("e.txt", "e1\n"), ("g.txt", "g1\n")))
    picks = git(wt, "rev-list", "--reverse", f"{cp['commit_sha']}..HEAD").split()
    one, two, three = picks
    doc = await w.preview([{"kind": "checkpoint_run", "id": run_id, "mode": "pick", "commits": [one, three]}])
    assert doc["ready"], doc["blocking"]
    src = doc["sources"][0]
    assert src["predicted"] == "pick" and [p["result"] for p in src["picks"]] == ["ok", "ok"]
    assert {x["code"] for x in src["warnings"]} == {"DEPENDS_ON_UNPICKED"}
    op = await w.apply(doc)
    assert op["status"] == "succeeded", op
    new = w.remote_head()
    added = git(w.remote, "rev-list", "--reverse", f"{w.f1}..{new}").split()
    assert len(added) == 2
    for orig, copy in zip((one, three), added, strict=True):
        assert f"(cherry picked from commit {orig})" in git(w.remote, "log", "-1", "--format=%B", copy)
        assert git(w.remote, "log", "-1", "--format=%an", copy) == "agent"
    files = git(w.remote, "ls-tree", "--name-only", new).split()
    assert "d.txt" in files and "g.txt" in files and "e.txt" not in files and "h.txt" not in files
    rec = w.receipts(op["operation_id"])[0]
    assert rec["method"] == "pick" and rec["picked"] == [{"source": one, "new": added[0]},
                                                        {"source": three, "new": added[1]}]


async def test_c01_area_is_bare_marked_and_inside_the_managed_root(world):
    w = world
    cp = await w.checkpoint()
    other = w.tmp / "other-operator"
    git(w.tmp, "clone", "-q", "-b", "feature-1", str(w.remote), str(other))
    (other / "a.txt").write_text("Operator edits feature-1 here\n")
    before = tree_digest(other)
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    op = await w.apply(doc)
    assert op["status"] == "succeeded", op
    area = w.area()
    assert str(area).startswith(str(w.tmp / "managed" / ".batc-integration") + "/")
    cfg = dict(line.split("=", 1) for line in git(area, "config", "--local", "--list").splitlines())
    assert cfg["core.bare"] == "true" and cfg["batc.role"] == "integration" and cfg["batc.managed-clone"] == "true"
    assert cfg["batc.repository"] == "o/r" and cfg["batc.host"] == "h1" and cfg["batc.remote-url"] == str(w.remote)
    assert not [k for k in cfg if k.startswith(("remote.", "url.", "core.hookspath"))]
    assert all(r.startswith("refs/batc/") for r in git(area, "for-each-ref", "--format=%(refname)").split())
    assert tree_digest(other) == before  # a checkout of the PR branch is never updated


async def test_c01_one_operation_path_for_http_mcp_cli(world):
    w = world
    cp = await w.checkpoint()
    sources = [{"kind": "checkpoint", "id": cp["checkpoint_id"]}]
    doc = await w.preview(sources, key="pv-1")
    target = {"host": "h1", "repository": "o/r", "pull_number": 1}
    params = {"action": "integration.apply", "target": target, "params": {"preview_id": doc["preview_id"]},
              "preconditions": {"expected_head_sha": doc["target"]["head_sha"], "preview_digest": doc["digest"]},
              "idempotency_key": "integrate." + doc["preview_id"]}
    assert integration.apply_request(doc) == params  # what `batc integrate apply` and the Dashboard send
    assert cli.integrate_sources(["checkpoint:" + cp["checkpoint_id"], "branch:x"], ["2=" + w.f1]) == [
        sources[0], {"kind": "branch", "id": "x", "mode": "pick", "commits": [w.f1]}]
    first = await w.d.call_api("op_submit", {**params, "entry": "cli"}, OPERATOR)
    again = await w.d.call_api("op_submit", {**params, "entry": "mcp"}, OPERATOR)
    assert first["created"] and not again["created"]
    assert again["operation"]["operation_id"] == first["operation"]["operation_id"]
    op = await w.settle(first["operation"]["operation_id"])
    assert op["status"] == "succeeded", op
    rpc = await w.d.call_api("integration_get", {"operation_id": op["operation_id"]}, OPERATOR)
    assert rpc["receipts"] == integration.integration_get(w.d.ops, op["operation_id"])["receipts"]
    assert rpc["preview"]["preview_id"] == doc["preview_id"]
    listed = await w.d.call_api("integrations_list", {"repository": "o/r", "pull_number": 1}, OPERATOR)
    assert [x["operation_id"] for x in listed["integrations"]] == [op["operation_id"]]
    caps = await w.d.call_api("api_capabilities", {}, OPERATOR)
    assert caps["features"]["integration"] == [{"repository": "o/r", "hosts": ["h1"]}]
    card = await w.d.call_api("github_pr_preview", {"repository": "o/r", "pull_number": 1}, OPERATOR)
    assert card["pull_request"]["integration"]["allowed"] and card["pull_request"]["integration"]["last_delivered"]
    cands = await w.d.call_api("integration_candidates", {"host": "h1"}, OPERATOR)
    assert cands["checkpoints"][0]["delivered_to"][0]["operation_id"] == op["operation_id"]


# --------------------------------------------------------------------------- C02
async def test_c02_pr_head_moved_after_preview_is_stale_and_nothing_runs(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    x = w.third_party_push()
    op = await w.apply(doc)
    assert op["status"] == "failed" and op["error_code"] == "TARGET_HEAD_CHANGED", op
    assert not [s for s in op["steps"] if s["name"].startswith(("compose", "push"))]
    assert w.remote_head() == x
    assert [r["effective_status"] for r in w.receipts(op["operation_id"])] == ["not_delivered"]
    # GitHub may lag behind git: the host's own read of the remote catches the move before anything runs.
    stale = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}], key="again")
    w.gh.pr_remotes.pop(1)  # the API keeps answering the old head
    y = w.third_party_push("z.txt", "even newer\n")
    op = await w.apply(stale)
    assert op["status"] == "failed" and op["error_code"] == "TARGET_HEAD_CHANGED", op
    assert not [s for s in op["steps"] if s["name"].startswith(("compose", "push"))] and w.remote_head() == y
    with pytest.raises(AssertionError, match="TARGET_HEAD_CHANGED"):  # a preview against a head you did not see
        await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}], key="stale-pv", expected="e" * 40)


async def test_c02_source_moved_after_preview_is_source_changed(world):
    w = world
    cp = await w.checkpoint()
    run_id, _, wt = await w.agent_result(cp)
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}, {"kind": "checkpoint_run", "id": run_id}])
    commit(wt, "late.txt", "late\n", "agent kept working")
    op = await w.apply(doc)
    assert op["status"] == "failed" and op["error_code"] == "SOURCE_CHANGED" and "source 2" in op["status_reason"]
    assert w.remote_head() == w.f1 and w.runner.ran["push"] == 0


async def test_c02_apply_cannot_change_sources_or_target(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    target = {"host": "h1", "repository": "o/r", "pull_number": 1}
    pre = {"expected_head_sha": doc["target"]["head_sha"], "preview_digest": doc["digest"]}
    cases = [
        (target, {"preview_id": doc["preview_id"], "sources": []}, pre, "INVALID_PARAMS"),
        (target, {"preview_id": doc["preview_id"]}, {**pre, "preview_digest": "0" * 64}, "PREVIEW_MISMATCH"),
        (target, {"preview_id": doc["preview_id"]}, {**pre, "expected_head_sha": "e" * 40}, "PREVIEW_MISMATCH"),
        ({**target, "pull_number": 2}, {"preview_id": doc["preview_id"]}, pre, "PREVIEW_MISMATCH"),
        (target, {"preview_id": doc["preview_id"]}, {"preview_digest": doc["digest"]}, "PRECONDITION_REQUIRED"),
        (target, {"preview_id": "ipv_" + "0" * 32}, pre, "PREVIEW_NOT_FOUND"),
    ]
    before = len(w.d.ops.list()["operations"])
    for n, (t, p, c, code) in enumerate(cases):
        with pytest.raises(OperationError) as e:
            w.d.ops.create(OPERATOR, action="integration.apply", target=t, params=p, preconditions=c,
                           idempotency_key=f"bad-{n}")
        assert e.value.code == code, (n, e.value)
    w.d.journal.db.execute("UPDATE integration_previews SET expires_at=0")
    with pytest.raises(OperationError) as e:
        w.d.ops.create(OPERATOR, action="integration.apply", target=target, params={"preview_id": doc["preview_id"]},
                       preconditions=pre, idempotency_key="bad-expired")
    assert e.value.code == "PREVIEW_EXPIRED"
    # A source already in the PR: the preview is blocked, and apply refuses it.
    same = await w.checkpoint(commit=w.f1)
    blocked = await w.preview([{"kind": "checkpoint", "id": same["checkpoint_id"]}])
    assert [b["code"] for b in blocked["blocking"]] == ["NOTHING_TO_INTEGRATE"] and not blocked["ready"]
    with pytest.raises(OperationError) as e:
        w.d.ops.create(OPERATOR, action="integration.apply", target=target, params={"preview_id": blocked["preview_id"]},
                       preconditions={"expected_head_sha": blocked["target"]["head_sha"],
                                      "preview_digest": blocked["digest"]}, idempotency_key="bad-blocked")
    assert e.value.code == "PREVIEW_BLOCKED"
    assert len(w.d.ops.list()["operations"]) == before + 2  # only the two checkpoint/preview operations


def race_commit(remote: Path, base: str) -> str:
    """Shell for the injected race: a third party's commit on top of ``base``, written straight to the remote."""
    return (f'n=$(git --git-dir={remote} commit-tree {base}^{{tree}} -p {base} -m race); '
            f'git --git-dir={remote} update-ref refs/heads/feature-1 "$n"')


async def test_c02_remote_moves_just_before_push_is_needs_attention_and_never_overwritten(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    w.runner.inject["push"] = race_commit(w.remote, w.f1)  # lands between the script's check and its push
    op = await w.apply(doc)
    assert op["status"] == "needs_attention" and op["error_code"] == "REMOTE_MOVED", op
    theirs = w.remote_head()
    assert theirs != w.f1 and git(w.remote, "log", "-1", "--format=%s", theirs) == "race"
    w.runner.inject.clear()
    w.d.ops.resume(OPERATOR, op["operation_id"])  # the remote is not back at base: still moved, still not pushed
    again = await w.settle(op["operation_id"])
    assert again["error_code"] == "REMOTE_MOVED" and w.remote_head() == theirs
    assert git(w.remote, "log", "-1", "--format=%s", w.remote_head()) == "race"
    w.d.ops.cancel(OPERATOR, op["operation_id"])
    assert [r["effective_status"] for r in w.receipts(op["operation_id"])] == ["not_delivered"]
    for script in w.runner.of("push"):
        pushes = [line for line in script.splitlines() if re.search(r"\bpush\b.*--porcelain", line)]
        tail = [line for line in script.splitlines() if "--no-verify" in line]
        assert len(pushes) == 1 and len(tail) == 1
        assert re.search(r"push --porcelain --no-verify \"\$url\" '?[0-9a-f]{40}:refs/heads/feature-1'?", tail[0])
        assert not re.search(r"--force|--mirror|--all|--tags|--delete|--prune|\s\+", tail[0])


async def test_c02_rewind_just_before_push_is_flagged_and_not_reverted(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    w.runner.inject["push"] = f"git --git-dir={w.remote} update-ref refs/heads/feature-1 {w.main}"
    op = await w.apply(doc)
    assert op["status"] == "needs_attention" and op["error_code"] == "REMOTE_REWOUND_BEFORE_PUSH", op
    assert op["external_refs"]["push_old_sha"] == w.main
    assert [r["status"] for r in w.receipts(op["operation_id"])] == ["delivered"]
    w.runner.inject.clear()
    w.d.ops.resume(OPERATOR, op["operation_id"])
    done = await w.settle(op["operation_id"])
    assert done["status"] == "succeeded" and "PUSHED_ON_OTHER_BASE" in done["result"]["warnings"]
    assert w.runner.ran["push"] == 1


async def test_c02_check_refuses_commits_the_preview_did_not_list(world):
    w = world
    cp = await w.checkpoint()
    await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])  # creates the area and fetches H
    area_path = str(w.area().parent)
    extra = git(w.area(), "commit-tree", f"{w.H}^{{tree}}", "-p", w.H, "-m", "smuggled")
    repo = w.d.ops.context["github_config"].repos["o/r"]
    area = integration.Area(w.d.ops, repo, "h1")
    assert area.path == area_path
    out = await w.runner.run("h1", integration.check_script(area, "0" * 12, "feature-1", w.f1, extra,
                                                            [(w.f1, w.H)], [], [(1, w.H)]))
    assert out.startswith("fail unexpected " + extra)
    # And a tampered result ref is never taken as the connector's own commit.
    op_like = "1" * 12
    git(w.area(), "update-ref", f"refs/batc/ops/{op_like}/after/1", extra)
    out = await w.runner.run("h1", integration.compose_script(
        area, op_like, "feature-1", 1, w.f1, {"pin": w.H, "mode": "merge"}, "m", "@0 +0000"))
    assert out.startswith("error COMPOSE_NOT_DETERMINISTIC"), out


async def test_c02_one_update_per_pr_and_merge_exclusion(world):
    w = world
    cp = await w.checkpoint()
    r1, _, _ = await w.agent_result(cp, files=(("a.txt", "one\ntwo\nmine\n"),))
    r2, _, _ = await w.agent_result(cp, files=(("a.txt", "one\ntwo\nyours\n"),))
    doc = await w.preview([{"kind": "checkpoint_run", "id": r1}, {"kind": "checkpoint_run", "id": r2}])
    op = await w.apply(doc)
    assert op["status"] == "needs_attention" and op["error_code"] == "INTEGRATION_CONFLICT"
    clean = await w.preview([{"kind": "checkpoint_run", "id": r1}])
    pre = {"expected_head_sha": clean["target"]["head_sha"], "preview_digest": clean["digest"]}
    target = {"host": "h1", "repository": "o/r", "pull_number": 1}
    assert [b["code"] for b in clean["blocking"]] == ["INTEGRATION_IN_PROGRESS"]
    with pytest.raises(OperationError) as e:
        w.d.ops.create(OPERATOR, action="github.pr.merge", target={"repository": "o/r", "pull_number": 1},
                       params={"method": "squash"}, preconditions={"expected_head_sha": w.f1}, idempotency_key="m")
    assert e.value.code == "INTEGRATION_IN_PROGRESS" and op["operation_id"] in e.value.message
    w.d.ops.cancel(OPERATOR, op["operation_id"])
    clean = await w.preview([{"kind": "checkpoint_run", "id": r1}])
    assert clean["ready"], clean["blocking"]
    w.gh.merge_mode = "enqueue"
    merge_doc = (await delivery.pr_preview(w.d.ops, "o/r", 1))["merge_preview"]
    envelope = pr_delivery.merge_envelope(merge_doc)
    merge = await w.run(envelope["action"], envelope["target"], envelope["params"], envelope["preconditions"])
    assert merge["status"] not in {"succeeded", "failed", "cancelled"}, merge
    with pytest.raises(OperationError) as e:
        w.d.ops.create(OPERATOR, action="integration.apply", target=target, params={"preview_id": clean["preview_id"]},
                       preconditions={"expected_head_sha": clean["target"]["head_sha"],
                                      "preview_digest": clean["digest"]}, idempotency_key="after-merge")
    assert e.value.code == "MERGE_IN_PROGRESS"
    assert pre  # the first preview was refused for its open integration, not reused


# --------------------------------------------------------------------------- C03
async def test_c03_second_source_conflict_keeps_the_first_receipt_and_pushes_nothing(world):
    w = world
    cp = await w.checkpoint()
    r1, a1, _ = await w.agent_result(cp, files=(("a.txt", "one\ntwo\nmine\n"),))
    r2, _, _ = await w.agent_result(cp, files=(("a.txt", "one\ntwo\nyours\n"),))
    human = tree_digest(w.human)
    doc = await w.preview([{"kind": "checkpoint_run", "id": r1}, {"kind": "checkpoint_run", "id": r2}])
    assert [s["predicted"] for s in doc["sources"]] == ["fast_forward", "conflict"] and doc["ready"]
    assert doc["sources"][1]["conflict_files"] == ["a.txt"] and doc["predicted_tree"] is None
    op = await w.apply(doc)
    assert op["status"] == "needs_attention" and op["error_code"] == "INTEGRATION_CONFLICT", op
    rec = w.receipts(op["operation_id"])
    assert (rec[0]["status"], rec[0]["integrated_sha"]) == ("composed", a1)
    assert (rec[1]["status"], rec[1]["conflict_files"]) == ("conflict", ["a.txt"])
    assert op["external_refs"]["conflict"] == {"seq": 2, "base": a1, "files": ["a.txt"]}
    assert w.remote_head() == w.f1 and w.runner.ran["push"] == 0 and tree_digest(w.human) == human
    w.d.ops.resume(OPERATOR, op["operation_id"])  # a replayed conflict stays a conflict; source 1 is not composed again
    again = await w.settle(op["operation_id"])
    assert again["error_code"] == "INTEGRATION_CONFLICT" and w.runner.ran["compose"] == 2


@pytest.mark.parametrize("legacy_receipt", [False, True])
async def test_c03_lost_push_reply_after_landing_reads_the_remote_and_never_pushes_twice(world, legacy_receipt):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    reflog = w.reflog()
    w.runner.lose_after.add("push")
    if legacy_receipt:
        def before_upgrade():
            row = w.d.journal.db.execute("SELECT operation_id,external_refs FROM operations WHERE action='integration.apply'").fetchone()
            refs = json.loads(row["external_refs"])
            refs["admission_binding"].pop("controller_authority")
            w.d.journal.db.execute("UPDATE operations SET external_refs=? WHERE operation_id=?",
                                  (json.dumps(refs), row["operation_id"]))
        w.runner.after["push"] = before_upgrade
    op = await w.apply(doc)
    assert op["status"] == "succeeded", op
    assert w.runner.ran["push"] == 1 and w.runner.ran["readback"] >= 1 and w.reflog() == reflog + 1
    push_steps = [s for s in op["steps"] if s["name"].startswith("push.")]
    response = w.d.journal.db.execute("SELECT response FROM operation_steps WHERE operation_id=? AND name=?",
                                      (op["operation_id"], push_steps[0]["name"])).fetchone()["response"]
    assert len(push_steps) == 1 and json.loads(response)["reconciled"]  # settled by reading the remote back
    assert [r["status"] for r in w.receipts(op["operation_id"])] == ["delivered"]
    events = w.d.journal.api_events(0, 200, resource_type="integration")["events"]
    assert [e["kind"] for e in events].count("integration.delivered") == 1


async def test_c03_lost_push_reply_before_running_pushes_exactly_once(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    w.runner.lose_before.add("push")
    op = await w.apply(doc)
    assert op["status"] == "succeeded", op
    assert w.runner.ran["push"] == 1 and w.remote_head() == op["result"]["new_head"]


async def test_c03_lost_reply_then_someone_pushed_on_top_counts_as_delivered(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])

    def on_top() -> None:
        head = git(w.remote, "rev-parse", "refs/heads/feature-1")
        child = git(w.remote, "commit-tree", f"{head}^{{tree}}", "-p", head, "-m", "on top")
        git(w.remote, "update-ref", "refs/heads/feature-1", child)

    w.runner.after["push"] = on_top
    w.runner.lose_after.add("push")
    op = await w.apply(doc)
    assert op["status"] == "succeeded", op
    assert "REMOTE_MOVED_AFTER" in op["result"]["warnings"]
    assert [r["status"] for r in w.receipts(op["operation_id"])] == ["delivered"] and w.runner.ran["push"] == 1


async def test_c03_unreadable_remote_keeps_the_push_uncertain(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    away = w.tmp / "remote-away.git"
    w.runner.after["push"] = lambda: w.remote.rename(away)  # landed, then nothing can read the remote
    w.runner.lose_after.add("push")
    op = await w.apply(doc)
    assert op["status"] == "needs_attention" and op["error_code"] == "UNCERTAIN_UNRESOLVED", op
    assert w.runner.ran["push"] == 1  # never sent again while unproven
    away.rename(w.remote)
    w.d.ops.resume(OPERATOR, op["operation_id"])
    done = await w.settle(op["operation_id"])
    assert done["status"] == "succeeded" and w.runner.ran["push"] == 1


async def test_c03_restart_mid_compose_recomposes_identically(world):
    w = world
    cp = await w.checkpoint()
    r1, _, _ = await w.agent_result(cp, files=(("d.txt", "d\n"),))
    other = w.third_party_push("y.txt", "moved on\n")
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}, {"kind": "checkpoint_run", "id": r1}],
                          expected=other)
    w.runner.lose_nth["compose"] = 2  # source 2 is composed, then the reply is lost
    op, _ = w.d.ops.create(OPERATOR, action="integration.apply", target={"host": "h1", "repository": "o/r",
                                                                     "pull_number": 1},
                           params={"preview_id": doc["preview_id"]},
                           preconditions={"expected_head_sha": other, "preview_digest": doc["digest"]},
                           idempotency_key="restart")
    await settle_operations(w.d.ops)
    assert w.d.ops.get(op["operation_id"])["status"] == "uncertain"
    composed = git(w.area(), "rev-parse", f"refs/batc/ops/{op['operation_id'][3:15]}/after/2")
    w.d.journal.close()
    w.d = w.daemon()  # the daemon restarts on the same journal
    done = await w.settle(op["operation_id"])
    assert done["status"] == "succeeded", done
    assert w.runner.ran["compose"] == 3 and w.runner.ran["push"] == 1  # seq 1 once, seq 2 twice, one push
    assert done["result"]["new_head"] == composed  # the re-run composed exactly the same commit


async def test_c03_human_source_is_only_read_and_never_cleaned(world):
    w = world
    cp = await w.checkpoint()
    r1, _, _ = await w.agent_result(cp, files=(("a.txt", "one\ntwo\nmine\n"),))
    human = tree_digest(w.human)
    w.runner.scripts.clear()
    conflict = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}, {"kind": "checkpoint_run", "id": r1},
                                {"kind": "branch", "id": "main"}])
    assert conflict["ready"], conflict["blocking"]
    op = await w.apply(conflict)
    assert op["status"] == "succeeded", op
    path = str(w.human)
    for script in w.runner.scripts:
        assert not re.search(r"branch -D|update-ref -d|gc --prune|worktree remove", script)
        for line in script.splitlines():
            if path in line:
                assert re.search(r"(fetch -q --no-tags --no-write-fetch-head|ls-remote --exit-code) '?" + re.escape(path),
                                 line), line
    assert tree_digest(w.human) == human


# --------------------------------------------------------------------------- safety
def test_push_refspec_cannot_force_or_delete():
    for sha, ref in [("", "feature-1"), ("x" * 40, "feature-1"), ("a" * 40, "+feature-1"), ("a" * 40, "-x"),
                     ("a" * 40, "a..b"), ("a" * 40, "a@{1}"), ("+" + "a" * 39, "f")]:
        with pytest.raises(Exception):  # noqa: B017 - ResourceReadOnly for every malformed input
            resource_policy.push_refspec(sha, ref)
    assert resource_policy.push_refspec("a" * 40, "feature-1") == "a" * 40 + ":refs/heads/feature-1"


async def test_an_emptied_head_never_reaches_git_push(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    w.runner.inject["push"] = "head="  # what an empty variable would do: `push <url> :refs/heads/x` deletes
    op = await w.apply(doc)
    assert op["status"] == "failed" and op["error_code"] == "GIT_FAILED", op
    assert w.remote_head() == w.f1


def test_parse_push_outcomes():
    head, base, other = "a" * 40, "b" * 40, "c" * 40
    p = integration.parse_push
    line = f"{head}:refs/heads/f"
    assert p(["rc 0", "To u", f" \t{line}\t{base}..{head}", "Done"], head, "f", base)["outcome"] == "pushed"
    assert p(["rc 0", f" \t{line}\t{other}..{head}"], head, "f", base)["outcome"] == "pushed_on_other_base"
    assert p(["rc 0", f"=\t{line}\t[up to date]"], head, "f", base)["outcome"] == "pushed"
    assert p(["rc 0", f"*\t{line}\t[new branch]"], head, "f", base)["outcome"] == "pushed_recreated"
    assert p(["rc 1", f"!\t{line}\t[rejected] (non-fast-forward)"], head, "f", base)["outcome"] == "remote_moved"
    assert p(["rc 1", f"!\t{line}\t[rejected] (fetch first)"], head, "f", base)["outcome"] == "remote_moved"
    r = p(["rc 1", f"!\t{line}\t[remote rejected] (protected branch hook declined)"], head, "f", base)
    assert r["outcome"] == "remote_rejected" and "protected" in r["reason"]
    assert p(["rc 128", "git@github.com: Permission denied (publickey)."], head, "f", base)["outcome"] == "auth_failed"
    assert p(["rc 0", f"+\t{line}\t{base}...{head} (forced update)"], head, "f", base)["outcome"] == "push_shape"
    assert p(["rc 0", "-\t:refs/heads/f\t[deleted]"], head, "f", base)["outcome"] == "push_shape"
    assert p([f"moved {other}"], head, "f", base) == {"outcome": "remote_moved", "seen": other}
    with pytest.raises(AmbiguousOutcome):
        p(["rc 1", "something else"], head, "f", base)


def test_only_integration_py_runs_git_push():
    src = Path(integration.__file__).parent
    for f in src.glob("*.py"):
        text = f.read_text()
        hits = re.findall(r"\bpush\s+--(?:porcelain\s+--no-verify|dry-run)[^\n]*", text)
        if f.name == "integration.py":
            assert len(hits) == 2 and sum("--dry-run" in h for h in hits) == 1, hits
        else:
            assert not hits and not re.search(r"['\"]push['\"]\s*,", text), f.name
        assert not re.search(r"push[^\n]*(--force|--mirror|--delete|--prune|--all\b|--tags)", text), f.name


@pytest.mark.parametrize("tamper", ["insteadof", "hookspath", "include", "alternates", "replace", "grafts"])
async def test_tampered_area_blocks_every_action(world, tamper):
    w = world
    cp = await w.checkpoint()
    await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}], key="first")
    area = w.area()
    if tamper == "insteadof":
        git(area, "config", "url./tmp/elsewhere.insteadOf", str(w.remote))
    elif tamper == "hookspath":
        git(area, "config", "core.hooksPath", "/tmp")
    elif tamper == "include":
        git(area, "config", "include.path", "/tmp/x")
    elif tamper == "alternates":
        (area / "objects" / "info").mkdir(parents=True, exist_ok=True)
        (area / "objects" / "info" / "alternates").write_text("/tmp\n")
    elif tamper == "replace":
        git(area, "update-ref", f"refs/replace/{w.f1}", w.H)
    else:
        (area / "info").mkdir(exist_ok=True)
        (area / "info" / "grafts").write_text(w.f1 + "\n")
    op = await w.run("integration.preview", {"host": "h1", "repository": "o/r", "pull_number": 1},
                     {"sources": [{"kind": "checkpoint", "id": cp["checkpoint_id"]}]}, key="second")
    assert op["status"] == "failed" and op["error_code"] == "CLONE_CONFIG_TAMPERED", op
    assert w.remote_head() == w.f1


async def test_linked_managed_root_or_area_dir_is_refused_before_any_write(world, tmp_path):
    w = world
    cp = await w.checkpoint()
    outside = w.tmp / "persons-folder"
    outside.mkdir()
    (w.tmp / "managed" / ".batc-integration").symlink_to(outside)
    op = await w.run("integration.preview", {"host": "h1", "repository": "o/r", "pull_number": 1},
                     {"sources": [{"kind": "checkpoint", "id": cp["checkpoint_id"]}]})
    assert op["status"] == "failed" and op["error_code"] == "DESTINATION_MANUAL", op
    assert list(outside.iterdir()) == []


async def test_hooks_and_global_signing_never_run_and_lfs_is_blocked(world, hermetic_git):
    w = world
    cp = await w.checkpoint()
    r1, _, _ = await w.agent_result(cp, files=(("d.txt", "d\n"),))
    w.third_party_push("y.txt", "moved on\n")
    r2, _, _ = await w.agent_result(cp, files=((".gitattributes", "*.bin filter=lfs diff=lfs merge=lfs -text\n"),))
    lfs = await w.preview([{"kind": "checkpoint_run", "id": r2}], key="lfs")
    assert "LFS_UNSUPPORTED" in [b["code"] for b in lfs["blocking"]]
    sentinel = w.tmp / "hook-ran"
    with hermetic_git.open("a") as f:  # the host user signs commits with a program that always fails
        f.write("[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /bin/false\n")
    await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}], key="warm")
    hooks = w.area() / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("pre-push", "reference-transaction", "post-checkout"):
        (hooks / name).write_text(f"#!/bin/sh\ntouch {sentinel}\n")
        (hooks / name).chmod(0o755)
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}, {"kind": "checkpoint_run", "id": r1}],
                          expected=w.remote_head())
    op = await w.apply(doc)
    assert op["status"] == "succeeded", op
    assert not sentinel.exists()


async def test_forbidden_targets_and_admission(world, mock):
    w = world
    cp = await w.checkpoint()
    src = [{"kind": "checkpoint", "id": cp["checkpoint_id"]}]
    w.gh.add_pr(3, w.main, head_ref="main")
    w.gh.add_pr(4, w.f1, head_ref="feature-1", fork=True)
    w.gh.add_pr(5, w.f1, head_ref="feature-1", state="closed")
    w.gh.add_pr(6, w.f1, head_ref="release/1.0")
    for number, code in [(3, "TARGET_REF_FORBIDDEN"), (4, "PR_HEAD_IN_FORK"), (5, "PR_CLOSED"),
                         (6, "TARGET_REF_FORBIDDEN")]:
        op = await w.run("integration.preview", {"host": "h1", "repository": "o/r", "pull_number": number},
                         {"sources": src})
        assert op["status"] == "succeeded" and [b["code"] for b in op["result"]["blocking"]] == [code], op["result"]
        assert not op["result"]["ready"] and not [s for s in op["steps"] if s["name"] == "prepare"]
    target = {"host": "h1", "repository": "o/r", "pull_number": 1}
    merge_only = api_auth.Principal("bot", frozenset({"observe", "merge"}))
    with pytest.raises(OperationError) as e:
        w.d.ops.create(merge_only, action="integration.preview", target=target, params={"sources": src},
                       idempotency_key="x")
    assert e.value.code == "FORBIDDEN"
    for bad_target, code in [({**target, "host": "nope"}, "UNKNOWN_HOST"), ({**target, "pull_number": 0},
                                                                            "INVALID_TARGET"),
                             ({**target, "repository": "o/other"}, "REPO_NOT_CONFIGURED")]:
        with pytest.raises(OperationError) as e:
            w.d.ops.create(OPERATOR, action="integration.preview", target=bad_target, params={"sources": src},
                           idempotency_key=f"t-{code}")
        assert e.value.code == code
    for sources, code in [([], "INVALID_PARAMS"), ([src[0], src[0]], "INVALID_PARAMS"),
                          ([{"kind": "checkpoint", "id": "cp_" + "0" * 32}], "SOURCE_NOT_FOUND"),
                          ([{"kind": "branch", "id": "a..b"}], "INVALID_PARAMS"),
                          ([{**src[0], "commits": [w.H]}], "INVALID_PARAMS"),
                          ([{**src[0], "mode": "pick", "commits": ["x"]}], "INVALID_PARAMS")]:
        with pytest.raises(OperationError) as e:
            w.d.ops.create(OPERATOR, action="integration.preview", target=target, params={"sources": sources},
                           idempotency_key=f"s-{json.dumps(sources)}")
        assert e.value.code == code, (sources, e.value)
    cfg = w.config()
    plain = parse_config({"hosts": {"h1": {"url": mock.url, "fingerprint": mock.fingerprint,
                                           "token_ref": "env:BATC_TEST_TOKEN", "writes": True, "orchestrate": False,
                                           "managed_roots": [str(w.tmp / "managed")]}},
                          "github": {"token_ref": "env:FAKE_GH_TOKEN", "api_url": w.gh.url,
                                     "repos": [{"repository": "o/r", "integrate": {
                                         "hosts": ["h1"], "remote_url": str(w.remote)}}]}})
    # A09: finish the first owner before evaluating a different daemon configuration.
    await w.d.fleet.close()
    w.d.journal.close()
    off = TaskDaemon(plain, w.tmp / "off.db")
    off.ops.context["git_runner"] = w.runner
    w.daemons.append(off)
    with pytest.raises(OperationError) as e:
        off.ops.create(OPERATOR, action="integration.preview", target=target, params={"sources": src}, idempotency_key="o")
    assert e.value.code == "TIER_DISABLED" and cfg.github.repos["o/r"].integrate


async def test_remote_identity_mismatch_blocks_the_preview(world):
    w = world
    cp = await w.checkpoint()
    git(w.remote, "update-ref", "refs/pull/1/head", w.main)  # the host's remote disagrees with GitHub
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}], sync=False)
    assert [b["code"] for b in doc["blocking"]] == ["REMOTE_IDENTITY_MISMATCH"]


def test_integrate_config_validation(mock):
    base = {"url": mock.url, "fingerprint": mock.fingerprint, "token_ref": "env:BATC_TEST_TOKEN"}
    for integrate in [{"hosts": ["h1"], "remote_url": "https://x-access-token:tok@github.com/o/r.git"},
                      {"hosts": ["h1"], "remote_url": "git@github.com:o/other.git"},
                      {"hosts": ["h1"], "remote_url": "/srv/r.git"},  # a path only with a loopback test GitHub
                      {"hosts": ["nope"], "remote_url": "git@github.com:o/r.git"},
                      {"hosts": ["h1"], "remote_url": "git@github.com:o/r.git", "fetch_timeout_s": 5},
                      {"hosts": ["h1"], "remote_url": "https://g\u0131thub.com/o/r.git"},  # dotless i folds to i
                      {"hosts": ["h1"], "remote_url": "git@github.com:o/\u017fecrets.git"}]:
        with pytest.raises(ConfigError):
            parse_config({"hosts": {"h1": base}, "github": {"repos": [{"repository": "o/r",
                                                                        "integrate": integrate}]}})


def test_integration_mutations_are_listed():
    rows = {m.action: m for m in resource_policy.MUTATIONS}
    assert rows["integration.area"].via == rows["integration.push"].via == "ssh-git"
    assert rows["integration.push"].scope == "remote" and "integration.apply" in rows["integration.push"].entry_points


async def test_a_rewound_remote_before_the_push_script_is_never_pushed_onto(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    w.runner.before["push"] = lambda: git(w.remote, "update-ref", "refs/heads/feature-1", w.main)
    op = await w.apply(doc)
    assert op["status"] == "needs_attention" and op["error_code"] == "REMOTE_MOVED", op
    assert w.remote_head() == w.main  # git alone would have fast-forwarded main..head; the script's check stops it
    assert [r["status"] for r in w.receipts(op["operation_id"])] == ["composed"]


async def test_a_result_that_differs_from_the_prediction_is_never_pushed(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    w.d.journal.db.execute("UPDATE integration_previews SET predicted_tree=?", ("f" * 40,))
    op = await w.apply(doc)
    assert op["status"] == "failed" and op["error_code"] == "TREE_MISMATCH", op
    assert w.remote_head() == w.f1 and w.runner.ran["push"] == 0


async def test_a_managed_root_that_links_elsewhere_is_refused_before_any_write(world):
    w = world
    cp = await w.checkpoint()
    outside = w.tmp / "persons-other-folder"
    outside.mkdir()
    link = w.tmp / "managed-link"
    link.symlink_to(outside)
    w.d.journal.close()
    w.d = w.daemon(managed=link)
    op = await w.run("integration.preview", {"host": "h1", "repository": "o/r", "pull_number": 1},
                     {"sources": [{"kind": "checkpoint", "id": cp["checkpoint_id"]}]})
    assert op["status"] == "failed" and op["error_code"] == "DESTINATION_MANUAL", op
    assert list(outside.iterdir()) == []


# --------------------------------------------------------------------------- C03: handoff
async def conflicted(w) -> tuple[dict, dict, str, str]:
    """Two agent results that change the same line: apply stops at source 2."""
    cp = await w.checkpoint()
    r1, a1, _ = await w.agent_result(cp, files=(("a.txt", "one\ntwo\nmine\n"),))
    r2, a2, _ = await w.agent_result(cp, files=(("a.txt", "one\ntwo\nyours\n"),))
    doc = await w.preview([{"kind": "checkpoint_run", "id": r1}, {"kind": "checkpoint_run", "id": r2}])
    op = await w.apply(doc)
    assert op["status"] == "needs_attention" and op["error_code"] == "INTEGRATION_CONFLICT", op
    return doc, op, a1, a2


def resolve_in(wt: str, text: str = "one\ntwo\nmine\nyours\n", *, commit_it: bool = True) -> str | None:
    Path(wt, "a.txt").write_text(text)
    git(wt, "add", "a.txt")
    if commit_it:
        git(wt, "commit", "-q", "--no-edit")
        return git(wt, "rev-parse", "HEAD")
    return None


async def test_c03_handoff_resolution_resumes_without_recomposing_or_receiving_twice(world):
    w = world
    doc, op, a1, a2 = await conflicted(w)
    w.mock.invokes.clear()
    h = await w.run("integration.handoff", {"operation_id": op["operation_id"]},
                    {"agent": "claude", "instructions": "Keep both lines"})
    assert h["status"] == "succeeded", h
    r = h["result"]
    starts = [i for i in w.mock.invokes if i["channel"] == "claude:start-session"]
    assert len(starts) == 1 and starts[0]["params"]["options"]["cwd"] == r["worktree_path"]
    assert re.search(r"/\.batc-integration/[^/]+/wt/batc-fix-[0-9a-f]{12}$", r["worktree_path"])
    assert starts[0]["params"]["options"]["permissionMode"] == "default"  # confined like checkpoint work
    prompt = next(i for i in w.mock.invokes if i["channel"] == "claude:send-message")["params"]["prompt"]
    assert "a.txt" in prompt and "Do not push" in prompt and "Keep both lines" in prompt
    hc = w.d.fleet.config.host("h1")
    entries = resource_policy._entries("h1")
    assert resource_policy.classify_row_for_read(hc, r["session_id"], has_tab=False,
                                                 entries=entries)["api_access"] == "managed"
    assert git(r["worktree_path"], "rev-parse", "HEAD") == a1  # the PR side, with the source merged in
    resolution = resolve_in(r["worktree_path"])
    w.d.ops.resume(OPERATOR, op["operation_id"])
    done = await w.settle(op["operation_id"])
    assert done["status"] == "succeeded", done
    assert w.runner.ran["compose"] == 2 and w.runner.ran["push"] == 1  # nothing composed again
    assert [s["name"] for s in done["steps"] if s["name"].startswith("resolve.")] == [f"resolve.2.{resolution[:12]}"]
    rec = w.receipts(op["operation_id"])
    assert [(x["status"], x["method"]) for x in rec] == [("delivered", "fast_forward"), ("delivered", "merge")]
    assert rec[1]["resolution_sha"] == resolution and rec[1]["resolver_session_id"] == r["session_id"]
    assert w.remote_head() == resolution
    reflog = w.reflog()
    again = await w.preview([{"kind": "checkpoint_run", "id": doc["sources"][0]["id"]},
                             {"kind": "checkpoint_run", "id": doc["sources"][1]["id"]}], key="after")
    assert [s["predicted"] for s in again["sources"]] == ["already_included", "already_included"]
    assert [b["code"] for b in again["blocking"]] == ["NOTHING_TO_INTEGRATE"] and w.reflog() == reflog
    kinds = [e["kind"] for e in w.d.journal.api_events(0, 500, resource_type="integration")["events"]]
    assert kinds.count("integration.conflict") == 1 and kinds.count("integration.resolved") == 1


@pytest.mark.parametrize("observed", ["different", "missing", "permissions"])
async def test_handoff_start_readback_refuses_identity_and_terminal_mismatch(world, monkeypatch, observed):
    """A10/C03: repair uses the same reserved-start checks as checkpoint continuation."""
    from bat_agent_connector.errors import InvokeTimeout

    w = world
    _, conflict, _, _ = await conflicted(w)
    w.mock.invokes.clear()
    client = w.d.fleet.client("h1")
    invoke = client.invoke

    async def lost_ack(channel, params=None, **kwargs):
        result = await invoke(channel, params, **kwargs)
        if channel == "claude:start-session":
            raise InvokeTimeout("fixture lost repair start acknowledgement")
        return result

    monkeypatch.setattr(client, "invoke", lost_ack)
    op, _ = w.d.ops.create(OPERATOR, action="integration.handoff", target={"operation_id": conflict["operation_id"]},
                          params={}, idempotency_key="repair-readback")
    await w.d.ops.drain(timeout=60)
    op = w.d.ops.get(op["operation_id"])
    assert op["status"] == "uncertain"
    sid = next(i["params"]["sessionId"] for i in w.mock.invokes if i["channel"] == "claude:start-session")
    row = registry.get("h1", sid)
    if observed == "missing":
        del w.mock.metas[sid]["cwd"]
        code = "CONFINEMENT_START_UNSETTLED"
    elif observed == "different":
        w.mock.metas[sid]["cwd"] = "/srv/another-checkout"
        code = "START_SESSION_MISMATCH"
    else:
        w.mock.metas[sid]["permissionMode"] = "bypassPermissions"
        code = "CONFINEMENT_MISMATCH"
    monkeypatch.setattr(client, "invoke", invoke)
    refused = await w.settle(op["operation_id"])
    assert refused["status"] == "needs_attention" and refused["error_code"] == code
    assert registry.get("h1", sid)["status"] == row["status"] != "active"
    assert w.mock.channels().count("claude:start-session") == 1
    assert "claude:send-message" not in w.mock.channels()
    if observed != "missing":
        record = registry.get("h1", sid)["confinement"]
        w.mock.metas[sid]["cwd"] = row["cwd"]
        w.mock.metas[sid].update(record["options"])
        reads = w.mock.channels().count("claude:get-session-meta")
        w.d.ops.resume(OPERATOR, op["operation_id"])
        still_refused = await w.settle(op["operation_id"])
        assert still_refused["status"] == "needs_attention" and still_refused["error_code"] == code
        assert w.mock.channels().count("claude:get-session-meta") == reads
        assert registry.get("h1", sid)["confinement"] == record
        assert "claude:send-message" not in w.mock.channels()


@pytest.mark.parametrize("failure", ["timeout", "disconnect"])
async def test_handoff_accepted_send_survives_evidence_read_failure(world, monkeypatch, failure):
    """A10/C03: repair handoff succeeds after its accepted prompt even if evidence is unreadable."""
    from bat_agent_connector.errors import ConnectionLost, InvokeTimeout

    w = world
    _, conflict, _, _ = await conflicted(w)
    w.mock.invokes.clear()
    client = w.d.fleet.client("h1")
    invoke = client.invoke
    accepted = False
    evidence_failures = []

    async def fail_after_send(channel, params=None, **kwargs):
        nonlocal accepted
        if accepted and channel == "claude:get-session-meta":
            evidence_failures.append(params["sessionId"])
            raise InvokeTimeout("fixture evidence timeout") if failure == "timeout" else ConnectionLost("fixture disconnect")
        result = await invoke(channel, params, **kwargs)
        if channel == "claude:send-message":
            assert result["accepted"]
            accepted = True
        return result

    monkeypatch.setattr(client, "invoke", fail_after_send)
    target = {"operation_id": conflict["operation_id"]}
    op = await w.run("integration.handoff", target, key="accepted-repair-evidence")
    assert op["status"] == "succeeded", op
    result = op["result"]
    assert result["session_id"] in evidence_failures
    assert result["current_verification"]["status"] == "unknown"
    assert result["current_verification"]["reason"] == "readback_failed"
    assert result["confinement"] == registry.get("h1", result["session_id"])["confinement"]
    assert result["confinement"]["verification"]["status"] == "options_confirmed"
    assert next(s for s in op["steps"] if s["name"] == "send")["status"] == "succeeded"
    again = await w.run("integration.handoff", target, key="accepted-repair-evidence")
    assert again == op
    assert w.mock.channels().count("claude:start-session") == 1 and w.mock.channels().count("claude:send-message") == 1


async def test_c03_resume_waits_while_the_resolver_streams(world):
    w = world
    _, op, _, _ = await conflicted(w)
    h = await w.run("integration.handoff", {"operation_id": op["operation_id"]})
    sid, wt = h["result"]["session_id"], h["result"]["worktree_path"]
    w.mock.metas[sid]["isStreaming"] = True
    w.d.ops.resume(OPERATOR, op["operation_id"])
    waiting = await w.settle(op["operation_id"], rounds=2)
    assert waiting["status"] == "waiting_external", waiting
    assert not [s for s in waiting["steps"] if s["name"].startswith("resolve.")]
    resolve_in(wt)
    w.mock.metas[sid]["isStreaming"] = False
    done = await w.settle(op["operation_id"])
    assert done["status"] == "succeeded", done


async def test_c03_invalid_or_missing_resolution_is_refused(world):
    w = world
    _, op, a1, _ = await conflicted(w)
    h = await w.run("integration.handoff", {"operation_id": op["operation_id"]})
    wt = h["result"]["worktree_path"]

    async def resume() -> dict:
        w.d.ops.resume(OPERATOR, op["operation_id"])
        return await w.settle(op["operation_id"])

    assert (await resume())["error_code"] == "RESOLUTION_INCOMPLETE"  # nothing committed yet
    marked = "one\ntwo\n<<<<<<< HEAD\nmine\n=======\nyours\n>>>>>>> theirs\n"
    resolve_in(wt, marked)
    assert (await resume())["error_code"] == "RESOLUTION_INVALID"  # conflict markers left in
    git(wt, "reset", "-q", "--hard", a1)
    subprocess.run(["git", "-C", wt, "merge", "-q", "--no-ff", "--no-commit", h["result"]["source_sha"]],
                   capture_output=True)
    resolve_in(wt)
    commit(wt, "extra.txt", "x\n", "a second commit")
    out = await resume()
    assert out["error_code"] == "RESOLUTION_INVALID" and "more than one commit" in out["status_reason"]
    git(wt, "reset", "-q", "--hard", "HEAD~1")
    Path(wt, "c.txt").write_text("dirty\n")
    out = await resume()
    assert out["error_code"] == "RESOLUTION_INVALID" and "uncommitted" in out["status_reason"]
    assert w.remote_head() == w.f1 and w.runner.ran["push"] == 0
    git(wt, "checkout", "--", "c.txt")
    assert (await resume())["status"] == "succeeded"


async def test_handoff_admission(world):
    w = world
    _, op, _, _ = await conflicted(w)
    no_start = api_auth.Principal("bot", frozenset({"observe", "integrate", "operate"}))
    with pytest.raises(OperationError) as e:
        w.d.ops.create(no_start, action="integration.handoff", target={"operation_id": op["operation_id"]},
                       idempotency_key="h0")
    assert e.value.code == "FORBIDDEN"
    cp_op = w.d.ops.list(action="checkpoint.create")["operations"][0]["operation_id"]
    with pytest.raises(OperationError) as e:
        w.d.ops.create(OPERATOR, action="integration.handoff", target={"operation_id": cp_op}, idempotency_key="h1")
    assert e.value.code == "NOT_AN_INTEGRATION"
    first = await w.run("integration.handoff", {"operation_id": op["operation_id"]}, key="h2")
    assert first["status"] == "succeeded"
    with pytest.raises(OperationError) as e:
        w.d.ops.create(OPERATOR, action="integration.handoff", target={"operation_id": op["operation_id"]},
                       idempotency_key="h3")
    assert e.value.code == "HANDOFF_EXISTS"
    resolve_in(first["result"]["worktree_path"])
    w.d.ops.resume(OPERATOR, op["operation_id"])
    assert (await w.settle(op["operation_id"]))["status"] == "succeeded"
    with pytest.raises(OperationError) as e:
        w.d.ops.create(OPERATOR, action="integration.handoff", target={"operation_id": op["operation_id"]},
                       idempotency_key="h4")
    assert e.value.code == "NOT_IN_CONFLICT"


async def test_c03_a_pinned_resolution_is_never_read_again(world):
    w = world
    _, op, _, _ = await conflicted(w)
    h = await w.run("integration.handoff", {"operation_id": op["operation_id"]})
    wt = h["result"]["worktree_path"]
    resolution = resolve_in(wt)
    w.runner.lose_before.add("push")  # stops right after the resolution was pinned
    w.d.ops.resume(OPERATOR, op["operation_id"])
    await settle_operations(w.d.ops)
    assert w.d.ops.get(op["operation_id"])["status"] == "uncertain"
    commit(wt, "late.txt", "the agent kept going\n", "after the pin")  # never picked up
    done = await w.settle(op["operation_id"])
    assert done["status"] == "succeeded", done
    assert w.remote_head() == resolution


async def test_c03_a_resolution_that_is_not_a_merge_of_both_sides_is_refused(world):
    w = world
    _, op, a1, _ = await conflicted(w)
    h = await w.run("integration.handoff", {"operation_id": op["operation_id"]})
    wt = h["result"]["worktree_path"]
    git(wt, "merge", "--abort")
    commit(wt, "a.txt", "one\ntwo\nmine\nyours\n", "just my side, rewritten")  # parents: (a1) only
    w.d.ops.resume(OPERATOR, op["operation_id"])
    out = await w.settle(op["operation_id"])
    assert out["error_code"] == "RESOLUTION_INVALID" and w.remote_head() == w.f1


# --------------------------------------------------------------------------- review findings
async def test_a_cancel_while_the_push_is_unproven_never_sends_it(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    w.runner.lose_before.add("push")  # the reply is lost before git push ran
    op, _ = w.d.ops.create(OPERATOR, action="integration.apply", target={"host": "h1", "repository": "o/r",
                                                                     "pull_number": 1},
                           params={"preview_id": doc["preview_id"]},
                           preconditions={"expected_head_sha": doc["target"]["head_sha"],
                                          "preview_digest": doc["digest"]}, idempotency_key="cancel-me")
    await settle_operations(w.d.ops)
    assert w.d.ops.get(op["operation_id"])["status"] == "uncertain"
    w.d.ops.cancel(OPERATOR, op["operation_id"])
    done = await w.settle(op["operation_id"])
    assert done["status"] == "cancelled", done
    assert w.runner.ran["push"] == 0 and w.remote_head() == w.f1
    assert [r["effective_status"] for r in w.receipts(op["operation_id"])] == ["not_delivered"]  # proven not sent


async def test_a_push_that_landed_and_was_set_back_is_never_sent_again(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    w.runner.after["push"] = lambda: git(w.remote, "update-ref", "refs/heads/feature-1", w.f1)  # the owner reverts
    w.runner.lose_after.add("push")
    op = await w.apply(doc)
    assert op["status"] == "needs_attention" and op["error_code"] == "PUSH_UNPROVEN", op
    assert w.runner.ran["push"] == 1 and w.remote_head() == w.f1


async def test_a_push_still_running_on_the_host_is_waited_for(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    w.runner.lose_before.add("push")
    op, _ = w.d.ops.create(OPERATOR, action="integration.apply", target={"host": "h1", "repository": "o/r",
                                                                     "pull_number": 1},
                           params={"preview_id": doc["preview_id"]},
                           preconditions={"expected_head_sha": doc["target"]["head_sha"],
                                          "preview_digest": doc["digest"]}, idempotency_key="in-flight")
    await settle_operations(w.d.ops)
    tag = op["operation_id"][3:15]
    busy = subprocess.Popen(["sh", "-c", f"# batc-int:push {tag}\nsleep 60"])  # the push the dropped ssh left behind
    try:
        (w.area() / f"batc-push-{tag}").write_text(f"{busy.pid}\n")
        w.d.journal.db.execute("UPDATE operations SET next_run_at=0 WHERE operation_id=?", (op["operation_id"],))
        await settle_operations(w.d.ops)
        assert w.d.ops.get(op["operation_id"])["status"] == "uncertain" and w.runner.ran["push"] == 0
    finally:
        busy.kill()
        busy.wait()
    done = await w.settle(op["operation_id"])
    assert done["status"] == "succeeded" and w.runner.ran["push"] == 1


async def test_an_unproven_push_is_read_back_even_after_the_pr_closed(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    w.runner.after["push"] = lambda: w.gh.merge(1)  # landed; auto-merge closes the PR before the read-back
    w.runner.lose_after.add("push")
    op = await w.apply(doc)
    assert op["status"] == "succeeded", op
    assert [r["status"] for r in w.receipts(op["operation_id"])] == ["delivered"]


async def test_a_cancelled_apply_with_an_unproven_push_reports_unknown(world):
    w = world
    cp = await w.checkpoint()
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    away = w.tmp / "remote-away.git"
    w.runner.after["push"] = lambda: w.remote.rename(away)
    w.runner.lose_after.add("push")
    op = await w.apply(doc)
    assert op["error_code"] == "UNCERTAIN_UNRESOLVED", op
    w.d.ops.cancel(OPERATOR, op["operation_id"])
    away.rename(w.remote)
    assert [r["effective_status"] for r in w.receipts(op["operation_id"])] == ["unknown"]


async def test_a_branch_whose_name_ends_like_the_pr_head_is_not_mistaken_for_it(world):
    w = world
    cp = await w.checkpoint()
    git(w.remote, "update-ref", "refs/heads/a/refs/heads/feature-1", w.main)
    doc = await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}])
    assert doc["ready"], doc["blocking"]
    op = await w.apply(doc)
    assert op["status"] == "succeeded", op


async def test_a_link_inside_the_area_repository_is_refused(world):
    w = world
    cp = await w.checkpoint()
    await w.preview([{"kind": "checkpoint", "id": cp["checkpoint_id"]}], key="first")
    (w.area() / "objects" / "info").mkdir(parents=True, exist_ok=True)
    (w.area() / "objects" / "info" / "packs-elsewhere").symlink_to(w.human / ".git" / "objects")
    op = await w.run("integration.preview", {"host": "h1", "repository": "o/r", "pull_number": 1},
                     {"sources": [{"kind": "checkpoint", "id": cp["checkpoint_id"]}]}, key="second")
    assert op["status"] == "failed" and op["error_code"] == "CLONE_CONFIG_TAMPERED", op


async def test_preview_runs_nothing_the_agent_configured_in_its_folder(world):
    w = world
    cp = await w.checkpoint()
    sentinel = w.tmp / "filter-ran"
    run_id, _, wt = await w.agent_result(cp, files=((".gitattributes", "* filter=evil\n"),))
    clone = str(Path(wt).parent.parent)
    git(clone, "config", "filter.evil.clean", f"sh -c 'touch {sentinel}; cat'")
    (Path(wt) / "a.txt").touch()  # stale stat data: a status would run the clean filter
    os.utime(Path(wt) / "a.txt", (1, 1))
    await w.preview([{"kind": "checkpoint_run", "id": run_id}])
    assert not sentinel.exists()


async def test_only_hosts_that_can_integrate_are_offered(world):
    w = world

    class NoAlias(RaceRunner):
        def available(self, host: str) -> bool:
            return False

    card = await w.d.call_api("github_pr_preview", {"repository": "o/r", "pull_number": 1}, OPERATOR)
    assert card["pull_request"]["integration"]["hosts"] == ["h1"]
    w.d.ops.context["git_runner"] = NoAlias()
    card = await w.d.call_api("github_pr_preview", {"repository": "o/r", "pull_number": 1}, OPERATOR)
    caps = await w.d.call_api("api_capabilities", {}, OPERATOR)
    assert card["pull_request"]["integration"]["hosts"] == [] and caps["features"]["integration"][0]["hosts"] == []


@pytest.mark.parametrize('stage', ['before', 'after'])
async def test_repair_preframe_cancellation_restarts_unsent_reserved_session_once(world, monkeypatch, stage):
    """A10/C03: repair restart uses unsent proof; after-frame cancellation is read back, never restarted."""
    import asyncio
    from dataclasses import replace

    from bat_agent_connector import orchestrate
    from tests.test_confinement import ACCOUNT, AccountRunner
    from tests.test_confinement_recovery import PauseAtFrame

    w = world
    _, conflict, _, _ = await conflicted(w)
    fleet = w.d.fleet
    fleet.config.hosts['h1'] = replace(fleet.config.host('h1'), confinement=ACCOUNT)
    pause = PauseAtFrame()
    fleet.confinement_runner = pause if stage == 'before' else AccountRunner()
    ready = pause.entered if stage == 'before' else asyncio.Event()
    invoke = fleet.client('h1').invoke
    propagated = []
    start_session = orchestrate.session_start

    async def trace_start(*args, **kwargs):
        try:
            return await start_session(*args, **kwargs)
        except asyncio.CancelledError as exc:
            propagated.append(exc)
            raise

    monkeypatch.setattr(orchestrate, 'session_start', trace_start)

    async def after_frame(channel, params=None, **kwargs):
        result = await invoke(channel, params, **kwargs)
        if channel == 'claude:start-session':
            ready.set()
            await asyncio.Event().wait()
        return result

    if stage == 'after':
        monkeypatch.setattr(fleet.client('h1'), 'invoke', after_frame)
    starts_before = w.mock.channels().count('claude:start-session')
    sends_before = w.mock.channels().count('claude:send-message')
    op, _ = w.d.ops.create(OPERATOR, action='integration.handoff', target={'operation_id': conflict['operation_id']},
                           params={}, idempotency_key='preframe-repair-cancel')
    await w.d.ops.run_due()
    pending = w.d.ops._active[op['operation_id']]
    try:
        await asyncio.wait_for(ready.wait(), 60)
        row = registry.list_entries('h1')[-1]
        sid, worktree = row['session_id'], row['cwd']
        pending.cancel('fixture repair cancellation')
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert len(propagated) == 1 and propagated[0].args == ('fixture repair cancellation',)
        if stage == 'before':
            assert propagated[0] is pause.cancelled
        row = registry.get('h1', sid)
        assert row['start_sent'] is (stage == 'after')
        assert row['status'] == ('failed' if stage == 'before' else 'uncertain')
        assert w.mock.channels().count('claude:start-session') - starts_before == (stage == 'after')
        assert 'worktree:remove' not in w.mock.channels()
        pause.release.set()
        monkeypatch.setattr(fleet.client('h1'), 'invoke', invoke)
        await w.d.ops.drain(timeout=60)
        result = w.d.ops.get(op['operation_id'])
        assert result['status'] == 'succeeded', result
        assert result['result']['session_id'] == sid
        assert registry.get('h1', sid)['cwd'] == worktree
        assert w.mock.channels().count('claude:start-session') - starts_before == 1
        assert w.mock.channels().count('claude:send-message') - sends_before == 1
    finally:
        if not pending.done():
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
