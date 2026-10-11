"""Integration (W06): put managed and human results into an existing PR's head branch with one normal push.

``integration.preview`` pins the PR head and every selected source by SHA in a connector-owned bare repository on
the BAT host (``<first managed root>/.batc-integration/<name>-<8 hex>/repo.git``), lists every commit and file that
would enter the PR, and predicts the result with ``git merge-tree``. ``integration.apply`` composes exactly that
(fast-forward, a merge commit, or picked commits) with git plumbing only, so no hook, filter or worktree runs, checks
that nothing else entered, and pushes one exact SHA to ``refs/heads/<head ref>`` with a normal push. A lost push
reply is settled by reading the remote back, never by pushing again. The person's folders are only ever the source
of ``git fetch`` or ``git ls-remote``. Design: docs/design/integration.md (plan §14, C01-C03).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
import time

from . import checkpoints, execution_sources, resource_policy
from .api_auth import Principal
from .checkpoints import _read_fleet, start_in_worktree
from .config import GitHubRepo
from .delivery import _check_wait, _gh, _has_step, _read, open_operations, pr_preview
from .errors import ResourceReadOnly
from .github import GitHubAmbiguous
from .operations import (
    RERUN,
    ActionDef,
    AmbiguousOutcome,
    NeedsAttention,
    OpContext,
    OperationError,
    OperationService,
    StepFailed,
    Wait,
    _canonical,
)

SHA = re.compile(r"[0-9a-f]{40}")
PREVIEW_ID = re.compile(r"ipv_[0-9a-f]{32}")
OP_ID = re.compile(r"op_[0-9a-f]{32}")
KINDS = ("checkpoint", "checkpoint_run", "execution", "task_command", "branch")
PREVIEW_TTL_S = 3600.0
MAX_SOURCES = 10
MAX_PICKS = 50
COMMIT_CAP = 200
FILE_CAP = 500
GIT_MIN = (2, 38)  # merge-tree --write-tree
PICK_GIT_MIN = (2, 40)  # merge-tree --merge-base
IDENTITY = ("BAT Connector", "bat-connector@noreply.invalid")  # .invalid can never be a GitHub account
# user.name/user.email: a resolving agent may set them in its worktree; composition sets its own identity anyway.
CONFIG_ALLOW = (r"core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks)"
                r"|batc\.(managed-clone|role|repository|remote-url|host)|user\.(name|email)")
AUTH_FAILURE = re.compile(r"Permission denied|Authentication failed|could not read Username|Repository not found|"
                          r"\b403\b|denied to|SAML", re.IGNORECASE)
_area_locks: dict[tuple[str, str], asyncio.Lock] = {}


# --------------------------------------------------------------------------- configuration and admission
def _integrate_repo(ops: OperationService, repository) -> GitHubRepo:
    _gh(ops)
    repo = ops.context["github_config"].repos.get(str(repository or "").lower())
    if repo is None:
        raise OperationError("REPO_NOT_CONFIGURED", f"{repository!r} has no [[github.repos]] entry", 403)
    if repo.integrate is None:
        raise OperationError("INTEGRATION_DISABLED", f"{repo.repository} has no integrate block", 403)
    return repo


def _admit_target(ops: OperationService, target: dict) -> tuple[GitHubRepo, str, int]:
    repo = _integrate_repo(ops, target.get("repository"))
    fleet = ops.context["fleet"]
    host = target.get("host")
    if host not in fleet.config.hosts:
        raise OperationError("UNKNOWN_HOST", f"unknown host {host!r}", 404)
    if host not in repo.integrate.hosts:
        raise OperationError("INTEGRATION_DISABLED", f"host {host} may not push to {repo.repository} "
                             "(integrate.hosts)", 403)
    if not (fleet.writes_enabled(host) and fleet.orchestrate_enabled(host)):
        raise OperationError("TIER_DISABLED", f"writes and orchestrate must be on for host {host}", 403)
    if not fleet.config.host(host).managed_roots:
        raise OperationError("NO_MANAGED_ROOT", f"host {host} has no managed_roots for the integration area", 409)
    runner = ops.context.get("git_runner")
    if runner is None or not runner.available(host):
        raise OperationError("GIT_RUNNER_UNAVAILABLE",
                             f"no SSH alias for host {host}; add it to [verification] ssh_hosts", 409)
    number = target.get("pull_number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise OperationError("INVALID_TARGET", "target.pull_number must be a positive integer", 422)
    return repo, host, number


def integration_in_progress(ops: OperationService, repository: str, number: int) -> str | None:
    found = open_operations(ops, ("integration.apply",), repository, number)
    return found[0] if found else None


def _merge_in_progress(ops: OperationService, repository: str, number: int) -> str | None:
    found = open_operations(ops, ("github.pr.merge", "delivery.merge_and_deploy"), repository, number)
    return found[0] if found else None


def resolve_sources(ops: OperationService, host: str, raw) -> list[dict]:
    """The selected sources, in order, with their recorded locations (journal only, never shown)."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SOURCES:
        raise OperationError("INVALID_PARAMS", f"params.sources must list 1-{MAX_SOURCES} sources", 422)
    hc = ops.context["fleet"].config.host(host)
    out, seen = [], set()
    for seq, s in enumerate(raw, 1):
        if not isinstance(s, dict) or s.get("kind") not in KINDS or not isinstance(s.get("id"), str):
            raise OperationError("INVALID_PARAMS", f"source {seq} needs kind ({', '.join(KINDS)}) and id", 422)
        kind, sid = s["kind"], s["id"]
        if (kind, sid) in seen:
            raise OperationError("INVALID_PARAMS", f"source {seq} is listed twice", 422)
        seen.add((kind, sid))
        mode = s.get("mode", "merge")
        commits = s.get("commits")
        if mode == "merge":
            if commits is not None:
                raise OperationError("INVALID_PARAMS", f"source {seq}: commits are only for mode pick", 422)
        elif mode == "pick":
            if (not isinstance(commits, list) or not 1 <= len(commits) <= MAX_PICKS or len(set(commits)) != len(commits)
                    or not all(isinstance(c, str) and SHA.fullmatch(c) for c in commits)):
                raise OperationError("INVALID_PARAMS", f"source {seq}: pick needs 1-{MAX_PICKS} distinct full SHAs",
                                     422)
        else:
            raise OperationError("INVALID_PARAMS", f"source {seq}: mode must be merge or pick", 422)
        item = {"seq": seq, "kind": kind, "id": sid, "mode": mode, "commits": commits if mode == "pick" else None}
        if kind == "checkpoint":
            if not checkpoints.CHECKPOINT_ID.fullmatch(sid):
                raise OperationError("INVALID_PARAMS", f"source {seq}: malformed checkpoint id", 422)
            row = ops.db.execute("SELECT * FROM checkpoints WHERE checkpoint_id=?", (sid,)).fetchone()
            if row is None:
                raise OperationError("SOURCE_NOT_FOUND", f"source {seq}: checkpoint {sid} not found", 404)
            item.update(host=row["host"], location=row["repo_root"], ref=None, pin=row["commit_sha"], start=None,
                        label=f"{row['branch'] or '?'} @ {row['commit_sha'][:12]}", dirty=row["dirty"])
        elif kind == "checkpoint_run":
            if not OP_ID.fullmatch(sid):
                raise OperationError("INVALID_PARAMS", f"source {seq}: malformed checkpoint run id", 422)
            row = ops.db.execute("""SELECT r.*, c.commit_sha FROM checkpoint_runs r JOIN checkpoints c
                USING(checkpoint_id) WHERE r.operation_id=?""", (sid,)).fetchone()
            if row is None:
                raise OperationError("SOURCE_NOT_FOUND", f"source {seq}: checkpoint run {sid} not found", 404)
            item.update(host=row["host"], location=row["clone_path"], ref=row["branch"], pin=None,
                        start=row["commit_sha"], label=row["branch"], worktree=row["worktree_path"],
                        session_id=row["session_id"])
        elif kind in execution_sources.KINDS:
            if not sid or len(sid) > 256 or (kind == "execution" and not OP_ID.fullmatch(sid)):
                raise OperationError("INVALID_PARAMS", f"source {seq}: malformed execution id", 422)
            item.update(execution_sources.resolve(ops, kind, sid))
        else:
            if not resource_policy.HEAD_REF.fullmatch(sid):
                raise OperationError("INVALID_PARAMS", f"source {seq}: {sid!r} is not a branch name", 422)
            item.update(host=host, location=None, ref=sid, pin=None, start=None, label=sid)
        if item["host"] != host:
            raise OperationError("SOURCE_ON_OTHER_HOST", f"source {seq} lives on {item['host']}, not {host}", 422)
        try:
            if kind not in execution_sources.KINDS:
                item["location_class"], _ = resource_policy.classify_integration_source(hc, kind, item["location"])
        except ResourceReadOnly as e:
            raise OperationError(e.code, f"source {seq}: {e}", 409) from None
        out.append(item)
    return out


def _admit_preview(ops: OperationService, principal: Principal, target: dict, params: dict, pre: dict) -> None:
    _admit_target(ops, target)
    resolve_sources(ops, target["host"], params.get("sources"))
    if set(params) - {"sources"}:
        raise OperationError("INVALID_PARAMS", "integration.preview takes only params.sources", 422)
    exp = pre.get("expected_head_sha")
    if exp is not None and not SHA.fullmatch(str(exp)):
        raise OperationError("INVALID_PARAMS", "preconditions.expected_head_sha must be a full SHA", 422)


def get_preview(db, preview_id: str) -> dict:
    row = db.execute("SELECT * FROM integration_previews WHERE preview_id=?", (preview_id,)).fetchone()
    if row is None:
        raise OperationError("PREVIEW_NOT_FOUND", f"preview {preview_id} not found", 404)
    return dict(row)


def _admit_apply(ops: OperationService, principal: Principal, target: dict, params: dict, pre: dict) -> None:
    repo, host, number = _admit_target(ops, target)
    if set(params) != {"preview_id"}:
        raise OperationError("INVALID_PARAMS", "integration.apply takes only params.preview_id; sources come from "
                             "the preview", 422)
    if not PREVIEW_ID.fullmatch(str(params["preview_id"])):
        raise OperationError("INVALID_PARAMS", "params.preview_id is malformed", 422)
    exp, digest = pre.get("expected_head_sha"), pre.get("preview_digest")
    if not SHA.fullmatch(str(exp or "")) or not isinstance(digest, str) or not digest:
        raise OperationError("PRECONDITION_REQUIRED", "preconditions.expected_head_sha and preview_digest are "
                             "required (from the preview you reviewed)", 422)
    pv = get_preview(ops.db, params["preview_id"])
    if (pv["host"] != host or pv["repository"].lower() != repo.repository.lower() or pv["pull_number"] != number
            or pv["head_sha"] != exp or pv["digest"] != digest):
        raise OperationError("PREVIEW_MISMATCH", "the preview is for another target, head or content; preview "
                             "again", 409)
    if time.time() > pv["expires_at"]:
        raise OperationError("PREVIEW_EXPIRED", "the preview is older than an hour; preview again", 409)
    blocking = json.loads(pv["blocking"])
    if blocking:
        raise OperationError("PREVIEW_BLOCKED", "the preview is blocked: " + ", ".join(b["code"] for b in blocking),
                             409)
    running = integration_in_progress(ops, repo.repository, number)
    if running:
        raise OperationError("INTEGRATION_IN_PROGRESS", f"{running} is already updating this PR", 409)
    merging = _merge_in_progress(ops, repo.repository, number)
    if merging:
        raise OperationError("MERGE_IN_PROGRESS", f"{merging} is merging this PR", 409)


# --------------------------------------------------------------------------- host scripts
def _q(value: str) -> str:
    return shlex.quote(value)


def _sha(value) -> str:
    """Every SHA put into a script is a literal Python already checked."""
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise StepFailed("GIT_FAILED", f"not a full SHA: {str(value)[:50]!r}")
    return value


class Area:
    """The integration area of one (host, repository, remote URL), and the prelude every script starts with."""

    def __init__(self, ops: OperationService, repo: GitHubRepo, host: str) -> None:
        self.ops, self.repo, self.host = ops, repo, host
        self.hc = ops.context["fleet"].config.host(host)
        self.url = repo.integrate.remote_url
        self.path = resource_policy.integration_area_path(self.hc, host, repo.repository, self.url)
        resource_policy.check_integration_area(self.hc, self.path)  # before any script names it
        self.root = resource_policy.norm(self.hc.managed_roots[0]) or ""

    def prelude(self, kind: str, tag: str, ref: str) -> str:
        return f"""# batc-int:{kind} {tag}
set -eu
export LC_ALL=C GIT_TERMINAL_PROMPT=0 SSH_ASKPASS_REQUIRE=never GIT_OPTIONAL_LOCKS=0 GIT_NO_REPLACE_OBJECTS=1
root={_q(self.root)}; area={_q(self.path)}; repo="$area/repo.git"
url={_q(self.url)}; ref={_q(ref)}; op={_q(tag)}
# composition and local reads: isolated from the host user's git config (signing, merge drivers, hooks)
gi() {{ env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git --git-dir="$repo" -c core.hooksPath=/dev/null \\
  -c gc.auto=0 -c maintenance.auto=false -c commit.gpgSign=false -c core.fsmonitor=false "$@"; }}
# network: the host user's config, so the host's git credentials work
gn() {{ git --git-dir="$repo" -c core.hooksPath=/dev/null -c gc.auto=0 -c maintenance.auto=false \\
  -c protocol.ext.allow=never -c fetch.recurseSubmodules=no -c core.fsmonitor=false "$@"; }}
sha_ok() {{ printf '%s' "$1" | grep -Eqx '[0-9a-f]{{40}}'; }}
# ls-remote matches ref patterns by their tail (refs/heads/a/refs/heads/x matches refs/heads/x): keep the exact one
exact() {{ awk -F '\t' -v r="$1" '$2 == r {{ print $1 }}'; }}
remote_head() {{ set +e; l=$(gn ls-remote --exit-code "$url" "refs/heads/$ref" 2>/dev/null); r=$?; set -e
  case $r in 0) v=$(printf '%s\n' "$l" | exact "refs/heads/$ref"); printf '%s' "${{v:--}}";; 2) printf -- '-';;
  *) printf unreadable;; esac; }}
cas() {{ gi update-ref "$1" "$2" "" 2>/dev/null || [ "$(gi rev-parse --verify -q "$1" || true)" = "$2" ] \\
  || {{ echo "error COMPOSE_NOT_DETERMINISTIC $1"; exit 0; }}; }}
ident() {{
  [ -d "$area" ] && [ "$(cd "$area" && pwd -P)" = "$area" ] || {{ echo "error DESTINATION_MANUAL area"; exit 0; }}
  [ -d "$repo" ] && [ "$(cd "$repo" && pwd -P)" = "$repo" ] || {{ echo "error DESTINATION_MANUAL repo"; exit 0; }}
  [ "$(gi config --get batc.managed-clone || true)" = true ] && [ "$(gi config --get batc.role || true)" = integration ] \\
   && [ "$(gi config --get batc.repository || true)" = {_q(self.repo.repository)} ] \\
   && [ "$(gi config --get batc.host || true)" = {_q(self.host)} ] \\
   && [ "$(gi config --get batc.remote-url || true)" = "$url" ] || {{ echo "error CLONE_NOT_OURS"; exit 0; }}
  [ ! -e "$repo/info/grafts" ] && [ ! -e "$repo/shallow" ] && [ ! -e "$repo/objects/info/alternates" ] \\
   && [ ! -e "$repo/objects/info/http-alternates" ] && [ ! -e "$repo/commondir" ] \\
   && [ -z "$(gi for-each-ref refs/replace)" ] || {{ echo "error CLONE_CONFIG_TAMPERED integrity"; exit 0; }}
  # a link inside the repository (objects, refs, packed-refs, config) would send its writes somewhere else
  [ -z "$(find "$repo" -type l -print 2>/dev/null | head -n 1)" ] || {{ echo "error CLONE_CONFIG_TAMPERED links"; exit 0; }}
  bad=$(gi config --local --list --name-only | grep -vxE {_q(CONFIG_ALLOW)} || true)
  [ -z "$bad" ] || {{ echo "error CLONE_CONFIG_TAMPERED config"; exit 0; }}
}}
"""

    def create(self) -> str:
        """Make the area if missing. The root and every directory are checked to be real before anything is
        written, so a managed root or .batc-integration that is a link into a person's folder is refused."""
        return f"""real() {{ [ -d "$1" ] && [ ! -L "$1" ] && [ "$(cd "$1" && pwd -P)" = "$1" ]; }}
real "$root" || {{ echo "error DESTINATION_MANUAL root"; exit 0; }}
d="$root/{resource_policy.INTEGRATION_DIR}"
if [ -e "$d" ] || [ -L "$d" ]; then real "$d" || {{ echo "error DESTINATION_MANUAL dir"; exit 0; }}; else mkdir "$d"; fi
if [ -e "$area" ] || [ -L "$area" ]; then real "$area" || {{ echo "error DESTINATION_MANUAL area"; exit 0; }}
else mkdir "$area"; fi
if [ ! -e "$repo" ] && [ ! -L "$repo" ]; then
  tmp="$repo.batc-tmp-$op"; rm -rf "$tmp"
  env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git init -q --bare --template= "$tmp"
  for kv in managed-clone=true role=integration repository={_q(self.repo.repository)} host={_q(self.host)}; do
    git --git-dir="$tmp" config "batc.${{kv%%=*}}" "${{kv#*=}}"; done
  git --git-dir="$tmp" config batc.remote-url "$url"
  if [ -e "$repo" ]; then rm -rf "$tmp"; else mv "$tmp" "$repo"; fi
fi
ident
set -- $(git version | sed -n 's/^git version \\([0-9]*\\)\\.\\([0-9]*\\).*/\\1 \\2/p')
[ "$1" -gt {GIT_MIN[0]} ] || [ "$2" -ge {GIT_MIN[1]} ] || {{ echo "error GIT_TOO_OLD $1.$2"; exit 0; }}
echo "gitver $1.$2"
"""

    async def run(self, script: str, *, long: bool = False, mutation: bool = False) -> list[str]:
        runner = self.ops.context["git_runner"]
        lock = _area_locks.setdefault((self.host, self.path), asyncio.Lock())
        async with lock:
            if mutation:
                from .pm_source_lineage import require_frame
                require_frame()
            # This is the shared repository integration service. Its existing
            # action authorization also permits receipt recovery after upgrade.
            # New session carriers are controller-bound at handoff preparation.
            if long:
                out = await runner.run(self.host, script, timeout_s=self.repo.integrate.fetch_timeout_s)
            else:
                out = await runner.run(self.host, script)
        lines = out.splitlines()
        for line in lines:
            if line.startswith("error "):
                code, _, detail = line[6:].partition(" ")
                raise StepFailed(code, f"{code} {detail}".strip())
        if "unreadable" in lines:
            raise AmbiguousOutcome("the remote or a source could not be read")
        return lines


def _fetch_block(i: int, live: str, frm: str) -> str:
    """Fetch one commit by SHA when missing. ``frm`` is only ever the repository argument of git fetch."""
    return (f'gi cat-file -e "{live}^{{commit}}" 2>/dev/null || gn fetch -q --no-tags --no-write-fetch-head '
            f'{frm} "{live}" 2>/dev/null || {{ echo "source {i} unavailable"; return 0; }}\n')


def _live_tip(src: dict) -> str:
    """Shell that sets $live to the source's current tip, or prints why not (and returns)."""
    i = src["seq"]
    if src["kind"] == "checkpoint":
        return f"live={_sha(src['pin'])}\n"
    where = '"$url"' if src["kind"] == "branch" else _q(src["location"])
    ref = _q("refs/heads/" + src["ref"])
    binding = ""
    if src["kind"] in execution_sources.KINDS:
        read = f'env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C {where} -c core.fsmonitor=false'
        binding = (f'[ "$(cd {where} 2>/dev/null && pwd -P)" = {where} ] && '
                   f'[ "$({read} rev-parse --show-toplevel 2>/dev/null)" = {where} ] && '
                   f'[ "$({read} symbolic-ref -q HEAD 2>/dev/null)" = {ref} ] || '
                   f'{{ echo "source {i} binding_changed"; return 0; }}\n')
    return (binding + f'set +e; l=$(gn ls-remote --exit-code {where} {ref} 2>/dev/null); r=$?; '
            f'set -e\ncase $r in 0) live=$(printf "%s\\n" "$l" | exact {ref});; 2) echo "source {i} missing_ref"; '
            f'return 0;; *) echo unreadable; exit 0;; esac\n'
            f'sha_ok "$live" || {{ echo "source {i} missing_ref"; return 0; }}\n')


def _from(src: dict) -> str:
    return '"$url"' if src["kind"] == "branch" else _q(src["location"])


def preview_prepare_script(area: Area, tag: str, head_ref: str, number: int, sources: list[dict]) -> str:
    parts = [area.prelude("preview-prepare", tag, head_ref), area.create(),
             'h=$(remote_head); [ "$h" = unreadable ] && { echo unreadable; exit 0; }; echo "remote_head $h"\n',
             f'set +e; p=$(gn ls-remote "$url" {_q(f"refs/pull/{number}/head")} 2>/dev/null | exact '
             f'{_q(f"refs/pull/{number}/head")}); set -e\n'
             'echo "pull_head ${p:--}"\n']
    for src in sources:
        i = src["seq"]
        parts.append(f"src_{i}() {{\n{_live_tip(src)}{_fetch_block(i, '$live', _from(src))}"
                     f'[ "$(gi cat-file -t "$live")" = commit ] || {{ echo "source {i} unavailable"; return 0; }}\n'
                     f'cas "refs/batc/pv/$op/src/{i}" "$live"; echo "source {i} $live"\n}}\nsrc_{i}\n')
    parts.append("""sha_ok "$h" || { echo "target missing"; exit 0; }
gi cat-file -e "$h^{commit}" 2>/dev/null || gn fetch -q --no-tags --no-write-fetch-head "$url" "$h" 2>/dev/null \\
  || { echo "target unavailable"; exit 0; }
cas "refs/batc/pv/$op/target" "$h"; echo "target $h"
sha_ok "$h"
set +e; out=$(gn -c core.abbrev=40 push --dry-run --porcelain --no-verify "$url" "$h:refs/heads/$ref" 2>&1); rc=$?
set -e
printf 'probe %s\\n' "$rc"; printf '%s\\n' "$out" | tail -n 5 | sed 's/^/probe_out /'
""")
    return "".join(parts)


def _sim() -> str:
    # A subshell, because gi is a shell function that env(1) cannot run.
    return ("sim() { ( export GIT_AUTHOR_NAME=sim GIT_AUTHOR_EMAIL=sim@sim.invalid GIT_COMMITTER_NAME=sim "
            "GIT_COMMITTER_EMAIL=sim@sim.invalid GIT_AUTHOR_DATE='@0 +0000' GIT_COMMITTER_DATE='@0 +0000'; \"$@\" ); }\n")


def analyze_script(area: Area, tag: str, head_ref: str, target: str, sources: list[dict]) -> str:
    """Read-only (plus unreferenced simulation objects): commits, files, overlaps and the predicted result."""
    t = _sha(target)
    parts = [area.prelude("analyze", tag, head_ref), "ident\n", _sim(),
             f'T={t}; prev=$T; stop=0\n'
             'if gi show "$T:.gitattributes" 2>/dev/null | grep -q "filter=lfs"; then echo "lfs 0"; fi\n'
             'set -- $(git version | sed -n \'s/^git version \\([0-9]*\\)\\.\\([0-9]*\\).*/\\1 \\2/p\'); '
             'echo "gitver $1.$2"\n']
    for src in sources:
        i, s = src["seq"], _sha(src["pin"])
        parts.append(f"""s={s}
mb=$(gi merge-base "$T" "$s" 2>/dev/null || true); echo "mb {i} ${{mb:--}}"
if gi show "$s:.gitattributes" 2>/dev/null | grep -q "filter=lfs"; then echo "lfs {i}"; fi
""")
        if src["start"]:
            parts.append(f'gi rev-list -n 1000 {_sha(src["start"])}..$s 2>/dev/null | sed "s/^/own {i} /" || true\n')
        if src["mode"] == "merge":
            parts.append(f"""if [ -n "$mb" ]; then
  echo "total {i} $(gi rev-list --count "$T..$s")"
  gi log --reverse --topo-order -n {COMMIT_CAP} --format='c {i} %H%x09%P%x09%an%x09%s' "$T..$s"
  gi diff --name-only "$mb" "$s" | head -n {FILE_CAP} | sed "s/^/f {i} /"
  echo "ftotal {i} $(gi diff --name-only "$mb" "$s" | wc -l)"
  gi diff --name-only "$mb" "$T" | head -n {FILE_CAP} | sed "s/^/pf {i} /"
fi
if [ $stop = 1 ]; then echo "pred {i} not_predicted"
elif [ -z "$mb" ]; then stop=1; echo "pred {i} unrelated"
elif gi merge-base --is-ancestor "$s" "$prev"; then echo "pred {i} already_included"
elif gi merge-base --is-ancestor "$prev" "$s"; then prev=$s; echo "pred {i} fast_forward"
else
  set +e; out=$(gi merge-tree --write-tree --name-only --no-messages "$prev" "$s"); rc=$?; set -e
  if [ $rc -eq 0 ]; then
    prev=$(printf 'sim\\n' | sim gi commit-tree "$(printf '%s\\n' "$out" | head -n1)" -p "$prev" -p "$s")
    echo "pred {i} merge"
  elif [ $rc -eq 1 ]; then stop=1; echo "pred {i} conflict"
    printf '%s\\n' "$out" | sed -n '2,201p' | grep -v '^$' | sed "s/^/cf {i} /" || true
  else echo "error GIT_FAILED merge-tree $rc"; exit 0; fi
fi
""")
        else:
            picks = [_sha(c) for c in src["commits"]]
            parts.append(f'gi log --no-walk=unsorted --format=\'c {i} %H%x09%P%x09%an%x09%s\' {" ".join(picks)} '
                         f'2>/dev/null || true\necho "total {i} {len(picks)}"\n'
                         f'for c in {" ".join(picks)}; do gi diff --name-only "$c^" "$c" 2>/dev/null; done | sort -u | '
                         f'head -n {FILE_CAP} | sed "s/^/f {i} /"\n')
            for n, c in enumerate(picks):
                earlier = " ".join(picks[:n])
                parts.append(f"""c={c}
if [ $stop = 1 ]; then echo "pick {i} $c not_predicted"
elif ! gi merge-base --is-ancestor "$c" "$s" 2>/dev/null; then stop=1; echo "pickbad {i} $c not_in_source"
elif [ "$(gi rev-list --parents -n1 "$c" | wc -w)" -ne 2 ]; then stop=1; echo "pickbad {i} $c merge_commit"
elif gi merge-base --is-ancestor "$c" "$prev"; then echo "pick {i} $c already_included"
else
  p=$(gi rev-parse "$c^"); case " {earlier} " in *" $p "*) ;; *)
    gi merge-base --is-ancestor "$p" "$T" || echo "pickdep {i} $c"; esac
  set +e; out=$(gi merge-tree --write-tree --name-only --no-messages --merge-base="$c^" "$prev" "$c"); rc=$?; set -e
  if [ $rc -eq 1 ]; then stop=1; echo "pick {i} $c conflict"
  elif [ $rc -ne 0 ]; then echo "error GIT_FAILED merge-tree $rc"; exit 0
  elif [ "$(printf '%s\\n' "$out" | head -n1)" = "$(gi rev-parse "$prev^{{tree}}")" ]; then
    echo "pick {i} $c empty"
  else prev=$(printf 'sim\\n' | sim gi commit-tree "$(printf '%s\\n' "$out" | head -n1)" -p "$prev"); \
echo "pick {i} $c ok"; fi
fi
""")
    for a in sources:
        for b in sources:
            if a is not b:
                parts.append(f'gi merge-base --is-ancestor {_sha(a["pin"])} {_sha(b["pin"])} 2>/dev/null && ! gi '
                             f'merge-base --is-ancestor {_sha(a["pin"])} "$T" && echo "dep {b["seq"]} {a["seq"]}" '
                             f'|| true\n')
    parts.append('if [ $stop = 0 ]; then echo "tree $(gi rev-parse "$prev^{tree}")"; '
                 'echo "added $(gi rev-list --count "$T..$prev")"; fi\n')
    return "".join(parts)


def _commit_env(name: str, email: str, date: str, *, committer_only: bool = False) -> str:
    out = f"GIT_COMMITTER_NAME={_q(IDENTITY[0])} GIT_COMMITTER_EMAIL={_q(IDENTITY[1])} GIT_COMMITTER_DATE={_q(date)}"
    if not committer_only:
        out += f" GIT_AUTHOR_NAME={_q(name)} GIT_AUTHOR_EMAIL={_q(email)} GIT_AUTHOR_DATE={_q(date)}"
    return out


def apply_prepare_script(area: Area, tag: str, head_ref: str, base: str, sources: list[dict]) -> str:
    parts = [area.prelude("apply-prepare", tag, head_ref), area.create(), f"base={_sha(base)}\n",
             'h=$(remote_head); [ "$h" = unreadable ] && { echo unreadable; exit 0; }\n'
             '[ "$h" = "$base" ] || { echo "target_moved $h"; exit 0; }\n']
    for src in sources:
        i, pin = src["seq"], _sha(src["pin"])
        check = "" if src["kind"] == "checkpoint" else (
            _live_tip(src) + f'[ "$live" = {pin} ] || {{ echo "source_changed {i} $live"; return 0; }}\n')
        parts.append(f"src_{i}() {{\n{check}{_fetch_block(i, pin, _from(src))}"
                     f'[ "$(gi cat-file -t {pin})" = commit ] || {{ echo "source {i} unavailable"; return 0; }}\n'
                     f'cas "refs/batc/ops/$op/src/{i}" {pin}\n}}\nsrc_{i}\n')
    parts.append('gi cat-file -e "$base^{commit}" 2>/dev/null || gn fetch -q --no-tags --no-write-fetch-head "$url" '
                 '"$base" 2>/dev/null || { echo "target unavailable"; exit 0; }\n'
                 'cas "refs/batc/ops/$op/base" "$base"; echo ok\n')
    return "".join(parts)


def compose_script(area: Area, tag: str, head_ref: str, seq: int, prev: str, src: dict, message: str,
                   date: str) -> str:
    """Compose one source onto ``prev``. Deterministic (fixed identity and date), so a re-run always computes
    the same commit; the result ref is set with compare-and-swap, which refuses any other value already there."""
    head = (area.prelude("compose", tag, head_ref) + "ident\n" +
            f'prev={_sha(prev)}; src={_sha(src["pin"])}; r="refs/batc/ops/$op/after/{seq}"\n')
    if src["mode"] == "merge":
        env = _commit_env(*IDENTITY, date)
        return head + f"""if gi merge-base --is-ancestor "$src" "$prev"; then echo "already_included $prev"; exit 0; fi
if gi merge-base --is-ancestor "$prev" "$src"; then cas "$r" "$src"; echo "fast_forward $src"; exit 0; fi
set +e; out=$(gi merge-tree --write-tree --name-only --no-messages "$prev" "$src"); rc=$?; set -e
if [ $rc -eq 1 ]; then echo conflict; printf '%s\\n' "$out" | sed -n '2,201p' | grep -v '^$' | sed 's/^/file /' || true
  exit 0; fi
[ $rc -eq 0 ] || {{ echo "error GIT_FAILED merge-tree $rc"; exit 0; }}
tree=$(printf '%s\\n' "$out" | head -n1)
c=$(printf '%s' {_q(message)} | (export {env}; gi commit-tree "$tree" -p "$prev" -p "$src"))
sha_ok "$c" || {{ echo "error GIT_FAILED commit-tree"; exit 0; }}
cas "$r" "$c"; echo "merged $c $tree"
"""
    body = [head, "cur=$prev; any=0\n"]
    for c in src["commits"]:
        c = _sha(c)
        trailer = f"\n\n(cherry picked from commit {c})\nBatc-Operation: {message.rsplit(' ', 1)[-1]}"
        body.append(f"""c={c}
if gi merge-base --is-ancestor "$c" "$cur"; then echo "pick_skip $c ancestor"
else
  set +e; out=$(gi merge-tree --write-tree --name-only --no-messages --merge-base="$c^" "$cur" "$c"); rc=$?; set -e
  [ $rc -eq 1 ] && {{ echo "error PICK_CONFLICT $c"; exit 0; }}
  [ $rc -eq 0 ] || {{ echo "error GIT_FAILED merge-tree $rc"; exit 0; }}
  tree=$(printf '%s\\n' "$out" | head -n1)
  if [ "$tree" = "$(gi rev-parse "$cur^{{tree}}")" ]; then echo "pick_skip $c empty"
  else
    an=$(gi log -1 --format=%an "$c"); ae=$(gi log -1 --format=%ae "$c"); ad=$(gi log -1 --format='%ad' --date=raw "$c")
    new=$({{ gi log -1 --format=%B "$c" | sed -e :a -e '/^\\n*$/{{$d;N;ba' -e '}}'; printf '%s' {_q(trailer)}; }} \\
      | (export GIT_AUTHOR_NAME="$an" GIT_AUTHOR_EMAIL="$ae" GIT_AUTHOR_DATE="$ad" \\
        {_commit_env(*IDENTITY, date, committer_only=True)}; gi commit-tree "$tree" -p "$cur"))
    sha_ok "$new" || {{ echo "error GIT_FAILED commit-tree"; exit 0; }}
    echo "picked $c $new"; cur=$new; any=1
  fi
fi
""")
    body.append('if [ "$cur" = "$prev" ]; then echo "already_included $prev"; exit 0; fi\n'
                'cas "$r" "$cur"; echo "picked_result $cur $(gi rev-parse "$cur^{tree}")"\n')
    return "".join(body)


def check_script(area: Area, tag: str, head_ref: str, base: str, head: str, ranges: list[tuple[str, str]],
                 extra_allowed: list[str], must_contain: list[tuple[int, str]]) -> str:
    """Nothing but the previewed commits entered: base..head is exactly the composed range, with no markers."""
    allowed = " ".join(_sha(x) for x in extra_allowed)
    lines = [area.prelude("check", tag, head_ref), "ident\n", f"base={_sha(base)}; head={_sha(head)}\n",
             'gi merge-base --is-ancestor "$base" "$head" || { echo "fail base"; exit 0; }\n']
    for seq, pin in must_contain:
        lines.append(f'gi merge-base --is-ancestor {_sha(pin)} "$head" || {{ echo "fail missing {seq}"; exit 0; }}\n')
    lines.append('t="$repo/batc-check-$op"; rm -rf "$t"; mkdir "$t"\n'
                 'gi rev-list "$base..$head" | sort > "$t/all"\n'
                 '{ :\n' + (f"  printf '%s\\n' {allowed}\n" if allowed else ""))
    for a, b in ranges:
        lines.append(f'  gi rev-list {_sha(a)}..{_sha(b)}\n')
    lines.append('} | sort -u > "$t/allowed"\n'
                 'extra=$(comm -23 "$t/all" "$t/allowed" | head -n 20 | tr "\\n" " ")\n'
                 'n=$(gi diff --check "$base" "$head" | grep -c "leftover conflict marker" || true)\n'
                 'rm -rf "$t"\n'
                 '[ -z "$extra" ] || { echo "fail unexpected $extra"; exit 0; }\n'
                 '[ "$n" = 0 ] || { echo "fail markers $n"; exit 0; }\n'
                 'echo "tree $(gi rev-parse "$head^{tree}")"; echo "added $(gi rev-list --count "$base..$head")"\n')
    return "".join(lines)


def push_script(area: Area, tag: str, head_ref: str, base: str, head: str) -> str:
    """The only code that emits `git push`: one exact commit to one branch, never forced, never a deletion."""
    refspec = resource_policy.push_refspec(head, head_ref)
    return (area.prelude("push", tag, head_ref) + "ident\n" + f"head={_sha(head)}; base={_sha(base)}\n" +
            """sha_ok "$head" && sha_ok "$base" || { echo "error GIT_FAILED bad sha"; exit 0; }
[ "$(gi cat-file -t "$head")" = commit ] || { echo "error GIT_FAILED missing object"; exit 0; }
seen=$(remote_head); [ "$seen" = unreadable ] && { echo unreadable; exit 0; }
[ "$seen" = "$head" ] && { echo "already $seen"; exit 0; }
[ "$seen" = "$base" ] || { echo "moved $seen"; exit 0; }
# batc-int:before-push
sha_ok "$head" || { echo "error GIT_FAILED bad sha"; exit 0; }
echo $$ > "$repo/batc-push-$op"  # a read-back after a dropped connection can see this push is still running
set +e
out=$(gn -c core.abbrev=40 -c push.default=nothing -c push.followTags=false -c push.gpgSign=false \\
  -c push.negotiate=false -c push.recurseSubmodules=no push --porcelain --no-verify "$url" """ + _q(refspec) +
            """ 2>&1); rc=$?
set -e
rm -f "$repo/batc-push-$op"
printf 'rc %s\\n%s\\n' "$rc" "$out" | tail -c 4000
""")


def readback_script(area: Area, tag: str, head_ref: str, base: str, head: str) -> str:
    return (area.prelude("readback", tag, head_ref) + "ident\n" + f"head={_sha(head)}; base={_sha(base)}\n" +
            """pf="$repo/batc-push-$op"
if [ -f "$pf" ] && ps -o args= -p "$(cat "$pf")" 2>/dev/null | grep -q "batc-int:push $op"; then echo running; exit 0; fi
seen=$(remote_head); echo "seen $seen"
case $seen in unreadable|-) exit 0;; esac
gi cat-file -e "$seen^{commit}" 2>/dev/null || gn fetch -q --no-tags --no-write-fetch-head "$url" "$seen" 2>/dev/null \\
  || { echo unreadable; exit 0; }
if gi merge-base --is-ancestor "$head" "$seen"; then echo contains_head; fi
if gi merge-base --is-ancestor "$base" "$seen"; then echo contains_base; fi
""")


def _gw(wt: str) -> str:
    return (f'gw() {{ env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C {_q(wt)} -c core.hooksPath=/dev/null '
            '-c core.fsmonitor=false -c commit.gpgSign=false "$@"; }\n')


def repair_script(area: Area, tag: str, head_ref: str, wt: str, branch: str, prev: str, src: str) -> str:
    """A worktree of the area at ``prev`` with ``src`` merged in and its conflicts left for a session to resolve.
    Re-run safe: an existing worktree is reported as it is (the session may already be working in it)."""
    env = _commit_env(*IDENTITY, "@0 +0000")
    return (area.prelude("repair", tag, head_ref) + "ident\n" + _gw(wt) +
            f"wt={_q(wt)}; br={_q(branch)}; prev={_sha(prev)}; src={_sha(src)}\n" +
            f"""real() {{ [ -d "$1" ] && [ ! -L "$1" ] && [ "$(cd "$1" && pwd -P)" = "$1" ]; }}
d="$area/wt"
if [ -e "$d" ] || [ -L "$d" ]; then real "$d" || {{ echo "error DESTINATION_MANUAL wt"; exit 0; }}; else mkdir "$d"; fi
if ! gi worktree list --porcelain | grep -Fxq "worktree $wt"; then
  if [ -e "$wt" ] || [ -L "$wt" ]; then rm -rf "$wt"; fi
  gi worktree prune
  if gi show-ref --verify -q "refs/heads/$br"; then gi worktree add -q "$wt" "$br"
  else gi worktree add -q -b "$br" "$wt" "$prev"; fi
  set +e; (export {env}; gw merge --no-ff --no-commit "$src") >/dev/null 2>&1; set -e
fi
echo "head $(gw rev-parse HEAD)"; echo "merge_head $(gw rev-parse -q --verify MERGE_HEAD || echo -)"
echo "first_parent $(gw rev-parse -q --verify HEAD^1 || echo -)"
gw diff --name-only --diff-filter=U | sed 's/^/conflict /'
""")


def inspect_script(area: Area, tag: str, head_ref: str, wt: str, prev: str) -> str:
    """Read-only: what the resolving session left in its worktree."""
    return (area.prelude("repair-inspect", tag, head_ref) + "ident\n" + _gw(wt) + f"prev={_sha(prev)}\n" +
            f"""[ -d {_q(wt)} ] || {{ echo missing; exit 0; }}
echo "head $(gw rev-parse HEAD)"; echo "merge_head $(gw rev-parse -q --verify MERGE_HEAD || echo -)"
echo "dirty $(gw status --porcelain --untracked-files=no | wc -l)"
echo "parents $(gw rev-list --parents -n1 HEAD)"
echo "first_parents $(gw rev-list --count --first-parent "$prev..HEAD")"
echo "markers $(gw diff --check "$prev" HEAD | grep -c 'leftover conflict marker' || true)"
""")


def resolve_script(area: Area, tag: str, head_ref: str, seq: int, prev: str, src: str, resolution: str) -> str:
    """Pin a validated resolution as source ``seq``'s result: exactly one merge commit of (prev, src), no markers."""
    return (area.prelude("resolve", tag, head_ref) + "ident\n" +
            f'prev={_sha(prev)}; src={_sha(src)}; R={_sha(resolution)}; r="refs/batc/ops/$op/after/{seq}"\n' +
            """[ "$(gi cat-file -t "$R" 2>/dev/null)" = commit ] || { echo "invalid not a commit"; exit 0; }
[ "$(gi rev-list --parents -n1 "$R")" = "$R $prev $src" ] || { echo "invalid parents"; exit 0; }
n=$(gi diff --check "$prev" "$R" | grep -c 'leftover conflict marker' || true)
[ "$n" = 0 ] || { echo "invalid conflict markers"; exit 0; }
cas "$r" "$R"
echo "stat $(gi show --remerge-diff --stat --format= "$R" | tail -n1)"; echo ok
""")


# --------------------------------------------------------------------------- parsing (pure)
def parse_push(lines: list[str], head: str, head_ref: str, base: str) -> dict:
    """Map `git push --porcelain` output to an outcome. '+' (forced) or '-' (deleted) cannot happen by construction
    and are reported as such; anything unrecognised is ambiguous and read back."""
    if lines and lines[0].startswith("already "):
        return {"outcome": "pushed", "via": "already"}
    if lines and lines[0].startswith("moved "):
        return {"outcome": "remote_moved", "seen": lines[0].split(" ", 1)[1]}
    rc = next((int(x[3:]) for x in lines if re.fullmatch(r"rc \d+", x)), None)
    dest = f"refs/heads/{head_ref}"
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0] or parts[1].split(":", 1)[-1] != dest:
            continue
        flag, summary = parts[0], parts[2]
        if flag in {"+", "-"}:
            return {"outcome": "push_shape", "flag": flag, "summary": summary}
        if flag == "=":
            return {"outcome": "pushed", "via": "up_to_date"}
        if flag == "*":
            return {"outcome": "pushed_recreated"}
        if flag == " ":
            old, _, new = summary.partition("..")
            if new != head:
                return {"outcome": "push_shape", "flag": flag, "summary": summary}
            return {"outcome": "pushed", "old": old} if old == base else {"outcome": "pushed_on_other_base",
                                                                          "old": old}
        if flag == "!":
            reason = summary + (" " + parts[3] if len(parts) > 3 else "")
            if "remote rejected" in reason:
                return {"outcome": "remote_rejected", "reason": reason[:300]}
            return {"outcome": "remote_moved", "reason": reason[:300]}
    text = "\n".join(lines)
    if rc not in (None, 0) and AUTH_FAILURE.search(text):
        return {"outcome": "auth_failed", "message": text[-300:]}
    raise AmbiguousOutcome("the push answer could not be read")


def _probe(lines: list[str], head_ref: str) -> str:
    rc = next((x.split(" ", 1)[1] for x in lines if x.startswith("probe ")), None)
    out = [x[len("probe_out "):] for x in lines if x.startswith("probe_out ")]
    if rc == "0" and any(f":refs/heads/{head_ref}\t" in o for o in out):
        return "ok"
    if rc not in (None, "0") and AUTH_FAILURE.search("\n".join(out)):
        return "denied"
    return "unknown"


def parse_analyze(lines: list[str]) -> dict:
    per: dict[int, dict] = {}

    def src(i: str) -> dict:
        return per.setdefault(int(i), {"commits": [], "files": [], "pr_files": [], "own": set(), "conflict_files": [],
                                       "picks": [], "pick_bad": [], "pick_deps": [], "lfs": False, "dirty": None,
                                       "predicted": None, "total": 0, "files_total": 0, "merge_base": None,
                                       "contains": []})
    out: dict = {"sources": per, "tree": None, "added": None, "lfs_target": False, "git_version": None}
    for line in lines:
        tag, _, rest = line.partition(" ")
        if tag == "c":
            i, _, body = rest.partition(" ")
            sha, parents, author, subject = (body.split("\t", 3) + ["", "", ""])[:4]
            src(i)["commits"].append({"sha": sha, "parents": parents.split(), "author": author, "subject": subject})
        elif tag in {"f", "pf", "cf", "own"}:
            i, _, value = rest.partition(" ")
            key = {"f": "files", "pf": "pr_files", "cf": "conflict_files"}.get(tag)
            if key:
                src(i)[key].append(value)
            else:
                src(i)["own"].add(value)
        elif tag == "mb":
            i, _, v = rest.partition(" ")
            src(i)["merge_base"] = None if v == "-" else v
        elif tag in {"total", "ftotal", "dirty"}:
            i, _, v = rest.partition(" ")
            src(i)[{"total": "total", "ftotal": "files_total", "dirty": "dirty"}[tag]] = int(v.strip() or 0)
        elif tag == "pred":
            i, _, v = rest.partition(" ")
            src(i)["predicted"] = v
        elif tag == "pick":
            i, c, v = rest.split(" ", 2)
            src(i)["picks"].append({"sha": c, "result": v})
        elif tag == "pickbad":
            i, c, v = rest.split(" ", 2)
            src(i)["pick_bad"].append({"sha": c, "why": v})
        elif tag == "pickdep":
            i, c = rest.split(" ", 1)
            src(i)["pick_deps"].append(c)
        elif tag == "lfs":
            if rest == "0":
                out["lfs_target"] = True
            else:
                src(rest)["lfs"] = True
        elif tag == "dep":
            j, i = rest.split(" ", 1)
            src(j)["contains"].append(int(i))
        elif tag == "tree":
            out["tree"] = rest
        elif tag == "added":
            out["added"] = int(rest)
        elif tag == "gitver":
            out["git_version"] = rest
    return out


# --------------------------------------------------------------------------- preview
def _pr_facts(pr: dict) -> dict:
    head, base = pr.get("head") or {}, pr.get("base") or {}
    return {"number": pr.get("number"), "state": pr.get("state"), "merged": bool(pr.get("merged")),
            "draft": bool(pr.get("draft")), "html_url": pr.get("html_url"),
            "head": {"sha": head.get("sha"), "ref": head.get("ref"),
                     "repo": {"id": (head.get("repo") or {}).get("id")} if head.get("repo") else None},
            "base": {"sha": base.get("sha"), "ref": base.get("ref"),
                     "repo": {"id": (base.get("repo") or {}).get("id"),
                              "default_branch": (base.get("repo") or {}).get("default_branch")}}}


def _block(code: str, text: str, **extra) -> dict:
    return {"code": code, "text": text, **extra}


def _digest(doc: dict, sources: list[dict]) -> str:
    return hashlib.sha256(_canonical({
        **({"provider_origin": doc["provider_origin"]} if "provider_origin" in doc else {}),
        "host": doc["host"], "repository": doc["repository"].lower(), "repository_id": doc["repository_id"],
        "pull_number": doc["pull_number"], "head_ref": doc["target"]["head_ref"], "head_sha": doc["target"]["head_sha"],
        "remote_url": doc["remote_url"], "predicted_tree": doc["predicted_tree"],
        "sources": [{"kind": s["kind"], "id": s["id"], "mode": s["mode"], "commits": s["commits"],
                     "pinned_sha": s["pin"], **({"lineage": s["lineage"], "registry_identity": s["registry_identity"], "worktree_id": s.get("worktree_id")}
                                              if s["kind"] in execution_sources.KINDS else {})} for s in sources]}).encode()).hexdigest()


async def _run_preview(ctx: OpContext) -> dict:
    ops = ctx.service
    repo, host, number = _admit_target(ops, ctx.target)
    gh = _gh(ops)
    refs = ctx.op.get("external_refs") or {}
    sources = refs.get("source_bindings") or resolve_sources(ops, host, ctx.params["sources"])
    if "source_bindings" not in refs:
        ctx.set_refs(source_bindings=sources)
    pr = refs.get("pr")
    if pr is None:  # read once; replays use the same facts
        pr = _pr_facts(await _read(gh.pull(repo.repository, number), "read the pull request"))
        from .pm_delivery_lineage import require_target
        require_target(ctx, provider=gh.cfg.api_url, repository_id=(pr["base"]["repo"] or {}).get("id"))
        exp = ctx.preconditions.get("expected_head_sha")
        if exp and exp != pr["head"]["sha"]:
            raise OperationError("TARGET_HEAD_CHANGED", f"PR #{number} head is now {str(pr['head']['sha'])[:12]}, "
                                 f"not {exp[:12]}; reload the PR", 409)
        ctx.set_refs(pr=pr)
    from .pm_delivery_lineage import require_target
    require_target(ctx, provider=gh.cfg.api_url, repository_id=(pr["base"]["repo"] or {}).get("id"))
    area = Area(ops, repo, host)
    tag = ctx.operation_id[3:15]
    pv_id = "ipv_" + ctx.operation_id[3:]
    head_ref = str(pr["head"]["ref"] or "")
    blocking, warnings = [], []
    try:
        resource_policy.check_push_target(repo.integrate.protected_refs, pr)
    except ResourceReadOnly as e:
        blocking.append(_block(e.code, str(e).split("] ", 1)[-1]))
    for code, other in (("INTEGRATION_IN_PROGRESS", integration_in_progress(ops, repo.repository, number)),
                        ("MERGE_IN_PROGRESS", _merge_in_progress(ops, repo.repository, number))):
        if other:
            blocking.append(_block(code, f"{other} is working on this PR", operation_id=other))
    doc = {"preview_id": pv_id, "host": host, "repository": repo.repository, "provider_origin": gh.cfg.api_url,
           "repository_id": (pr["base"]["repo"] or {}).get("id") or 0, "pull_number": number,
           "html_url": pr["html_url"], "remote_url": area.url,
           "target": {"head_ref": head_ref, "head_sha": pr["head"]["sha"], "base_ref": pr["base"]["ref"],
                      "base_sha": pr["base"]["sha"], "default_branch": pr["base"]["repo"]["default_branch"],
                      "pull_ref_sha": None, "push_access": "unknown"},
           "git_version": None, "sources": [], "overlaps": [], "dependencies": [], "predicted_tree": None,
           "added_commits": None,
           "plan": {"order": [s["seq"] for s in sources],
                    "push": f"one normal push of one commit to refs/heads/{head_ref}; never forced",
                    "never": ["force", "other refs", "your local folders"]},
           "keeps": ["managed integration refs stay in the connector's area; cleanup is a later action",
                     "your folders and sessions are only read, never cleaned"]}
    pins: dict[int, str] = {}
    analysis = None
    if not blocking:
        req = {"area": area.path, "head_ref": head_ref,
               "sources": [{"seq": s["seq"], "kind": s["kind"], "id": s["id"]} for s in sources]}

        async def prepare() -> dict:
            execution_sources.revalidate(ops, sources)
            lines = await area.run(preview_prepare_script(area, tag, head_ref, number, sources), long=True, mutation=True)
            return {"lines": lines}

        async def rerun(_request: dict) -> dict:
            return RERUN  # idempotent: pins use compare-and-swap, fetches only add objects

        lines = (await ctx.step("prepare", prepare, request=req, reconcile=rerun))["lines"]
        facts = dict(x.split(" ", 1) for x in lines if x.split(" ", 1)[0] in {"remote_head", "pull_head", "target",
                                                                              "gitver"} and " " in x)
        doc["git_version"] = facts.get("gitver")
        doc["target"]["pull_ref_sha"] = None if facts.get("pull_head") in {None, "-"} else facts["pull_head"]
        doc["target"]["push_access"] = _probe(lines, head_ref)
        if facts.get("remote_head") == "-" or not SHA.fullmatch(facts.get("target", "")):
            blocking.append(_block("REMOTE_REF_MISSING", f"{head_ref} is not on the remote"))
        elif facts["remote_head"] != pr["head"]["sha"] or doc["target"]["pull_ref_sha"] not in {None,
                                                                                               pr["head"]["sha"]}:
            blocking.append(_block("REMOTE_IDENTITY_MISMATCH", "the host's remote and GitHub disagree on the PR head "
                                   "(integrate.remote_url is another repository, or GitHub is lagging); preview again"))
        if doc["target"]["push_access"] == "denied":
            blocking.append(_block("PUSH_ACCESS_DENIED", f"this host's git credentials cannot push to "
                                   f"{repo.repository}; set a deploy key or credential on {host}"))
        elif doc["target"]["push_access"] == "unknown":
            warnings.append(_block("PUSH_ACCESS_UNPROVEN", "a dry-run push did not prove push access"))
        for x in lines:
            if x.startswith("source "):
                _, i, v = x.split(" ", 2)
                if SHA.fullmatch(v):
                    pins[int(i)] = v
                else:
                    code = "SOURCE_MISSING_REF" if v == "missing_ref" else "SOURCE_UNAVAILABLE"
                    blocking.append(_block(code, f"source {i} cannot be read ({v})", seq=int(i)))
        for s in sources:
            s["pin"] = pins.get(s["seq"])
        if not blocking:
            async def analyze() -> dict:
                return {"lines": await area.run(analyze_script(area, tag, head_ref, pr["head"]["sha"], sources), mutation=True)}

            alines = (await ctx.step("analyze", analyze, request={"target": pr["head"]["sha"], "pins": pins},
                                     reconcile=rerun))["lines"]
            analysis = parse_analyze(alines)
    _describe(ops, doc, sources, analysis, pr, blocking, warnings)
    doc["blocking"], doc["warnings"] = blocking, warnings
    doc["ready"] = not blocking
    doc["observed_at"] = ctx.op["created_at"]
    doc["expires_at"] = ctx.op["created_at"] + PREVIEW_TTL_S
    doc["digest"] = _digest(doc, sources)
    journal = ops.journal
    stored = [{k: s.get(k) for k in ("seq", "kind", "id", "host", "location", "location_class", "ref", "pin", "mode",
                                     "commits", "start", "label", "session_id", "workspace", "worktree_id", "lineage", "registry_identity")} for s in sources]
    with journal.tx():
        cur = ops.db.execute("""INSERT OR IGNORE INTO integration_previews(preview_id,operation_id,actor,host,
            repository,repository_id,pull_number,head_ref,head_sha,base_ref,base_sha,remote_url,area_path,sources,
            document,predicted_tree,digest,ready,blocking,created_at,expires_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (pv_id, ctx.operation_id, ctx.actor, host, repo.repository, doc["repository_id"], number,
                              head_ref, pr["head"]["sha"], pr["base"]["ref"] or "", pr["base"]["sha"] or "",
                              area.url, area.path, json.dumps(stored), json.dumps(doc), doc["predicted_tree"],
                              doc["digest"], int(doc["ready"]), json.dumps(blocking), doc["observed_at"],
                              doc["expires_at"]))
        if cur.rowcount:
            journal.api_event("integration", pv_id, "integration.previewed",
                              {"repository": repo.repository, "pull_number": number, "ready": doc["ready"],
                               "operation_id": ctx.operation_id}, actor=ctx.actor)
    return doc


def _source_key(s: dict) -> str:
    if s["mode"] == "pick":
        return "pick:" + hashlib.sha256(",".join(sorted(s["commits"])).encode()).hexdigest()[:16]
    return str(s.get("pin") or "")


def _describe(ops, doc: dict, sources: list[dict], analysis: dict | None, pr: dict, blocking: list,
              warnings: list) -> None:
    """Turn the analysis into the preview document: per-source commits and files, overlaps, warnings, blocking."""
    per = (analysis or {}).get("sources") or {}
    own_by_seq = {s["seq"]: (per.get(s["seq"], {}).get("own") if s["start"] else
                             set() if s["kind"] in execution_sources.KINDS else
                             {c["sha"] for c in per.get(s["seq"], {}).get("commits", [])}) for s in sources}
    git_version = tuple(int(x) for x in str((analysis or {}).get("git_version") or "0.0").split(".")[:2])
    if any(s["mode"] == "pick" for s in sources) and analysis and git_version < PICK_GIT_MIN:
        blocking.append(_block("PICK_NEEDS_GIT_2_40", "picking commits needs git 2.40 or later on the host"))
    if analysis and analysis["lfs_target"]:
        blocking.append(_block("LFS_UNSUPPORTED", "the PR uses Git LFS, which integration does not upload"))
    if pr.get("draft"):
        warnings.append(_block("PR_DRAFT", "the PR is a draft"))
    all_predicted = []
    for s in sources:
        a = per.get(s["seq"], {})
        item = {"seq": s["seq"], "kind": s["kind"], "id": s["id"], "label": s["label"],
                "location_class": s["location_class"], "ref": s["ref"], "pinned_sha": s.get("pin"),
                "session_id": s.get("session_id"), "lineage": s.get("lineage"),
                "mode": s["mode"], "picked_commits": s["commits"], "commits": [], "commits_total": a.get("total", 0),
                "files": a.get("files", []), "files_total": a.get("files_total", len(a.get("files", []))),
                "pr_side_files": a.get("pr_files", []), "predicted": a.get("predicted"),
                "conflict_files": a.get("conflict_files", []), "warnings": []}
        foreign = 0
        for c in a.get("commits", []):
            if c["sha"] in own_by_seq[s["seq"]]:
                origin = "own"
            else:
                origin = next((f"from_seq_{o['seq']}" for o in sources if o is not s
                               and c["sha"] in own_by_seq[o["seq"]]), "foreign")
                foreign += origin == "foreign"
            item["commits"].append({**c, "origin": origin})
        if foreign:
            item["warnings"].append(_block("BRINGS_FOREIGN_COMMITS", f"{foreign} commit(s) that are not this "
                                           "source's own work come with it (listed)", count=foreign))
        if s["kind"] == "checkpoint" and (s.get("dirty") or 0) > 0:
            item["warnings"].append(_block("UNCOMMITTED_NOT_INCLUDED", f"{s['dirty']} uncommitted change(s) in the "
                                           "person's folder are not included"))
        if s.get("session_id"):
            inv = ops.context.get("inventory")
            row = inv.get_session(doc["host"], s["session_id"]) if inv is not None else None
            if row and row.get("streaming") is True and not row.get("stale") and not row.get("fields_stale"):
                item["warnings"].append(_block("SESSION_STILL_WORKING", f"the session was observed streaming; only commits "
                                               f"up to {str(s.get('pin'))[:12]} are included"))
        delivered = ops.db.execute("""SELECT operation_id, pull_number FROM integration_receipts WHERE repository=?
            AND head_ref=? AND source_key=? AND status='delivered' ORDER BY delivered_at DESC LIMIT 1""",
                                   (doc["repository"], doc["target"]["head_ref"], _source_key(s))).fetchone()
        if delivered:
            item["warnings"].append(_block("DELIVERED_EARLIER", f"already delivered to #{delivered['pull_number']} "
                                           f"by {delivered['operation_id']}"))
        if s["mode"] == "pick":
            item["picks"] = a.get("picks", [])
            for bad in a.get("pick_bad", []):
                code = "PICK_NOT_IN_SOURCE" if bad["why"] == "not_in_source" else "PICK_MERGE_COMMIT"
                blocking.append(_block(code, f"source {s['seq']}: {bad['sha'][:12]} cannot be picked ({bad['why']})",
                                       seq=s["seq"]))
            if any(p["result"] == "conflict" for p in a.get("picks", [])):
                blocking.append(_block("PICK_CONFLICT", f"source {s['seq']}: a picked commit conflicts",
                                       seq=s["seq"]))
            for c in a.get("pick_deps", []):
                item["warnings"].append(_block("DEPENDS_ON_UNPICKED", f"{c[:12]} builds on a commit that is not "
                                               "picked and not in the PR"))
            picks = a.get("picks", [])
            item["predicted"] = ("already_included" if picks and all(p["result"] in {"already_included", "empty"}
                                                                      for p in picks)
                                 else "not_predicted" if any(p["result"] == "not_predicted" for p in picks)
                                 else "pick")
        elif a.get("predicted") == "unrelated":
            blocking.append(_block("SOURCE_UNRELATED", f"source {s['seq']} shares no history with the PR",
                                   seq=s["seq"]))
        if a.get("lfs"):
            blocking.append(_block("LFS_UNSUPPORTED", f"source {s['seq']} uses Git LFS", seq=s["seq"]))
        all_predicted.append(item["predicted"])
        doc["sources"].append(item)
    files: dict[str, dict] = {}
    for item in doc["sources"]:
        for f in item["files"]:
            files.setdefault(f, {"path": f, "seqs": [], "also_changed_on_pr": False})["seqs"].append(item["seq"])
        for f in item["pr_side_files"]:
            if f in files:
                files[f]["also_changed_on_pr"] = True
    doc["overlaps"] = [v for v in files.values() if len(v["seqs"]) > 1]
    for item in doc["sources"]:
        for i in per.get(item["seq"], {}).get("contains", []):
            doc["dependencies"].append({"seq": item["seq"], "contains": i})
            item["warnings"].append(_block("REDUNDANT_SOURCE", f"source {item['seq']} already contains source {i}"))
    if analysis:
        doc["predicted_tree"] = analysis["tree"]
        doc["added_commits"] = analysis["added"]
        if all_predicted and all(p == "already_included" for p in all_predicted):
            blocking.append(_block("NOTHING_TO_INTEGRATE", "every selected result is already in the PR"))


# --------------------------------------------------------------------------- apply
def _compose_outcome(out: list[str], src: dict, prev: str) -> tuple[str, str, list[dict], list[str]]:
    """(method, resulting commit, picked [{source, new}], conflict files) from a compose script's answer."""
    picked = [{"source": a, "new": b} for a, b in (x.split(" ")[1:3] for x in out if x.startswith("picked "))]
    for line in out:
        word, _, rest = line.partition(" ")
        sha = rest.split(" ")[0] if rest else ""
        if word == "conflict":
            return "conflict", prev, [], [x[5:] for x in out if x.startswith("file ")]
        if word == "already_included":
            return "already_included", prev, picked, []
        if word == "fast_forward":
            return "fast_forward", sha, [], []
        if word == "merged":
            return "merge", sha, [], []
        if word == "picked_result":
            return "pick", sha, picked, []
    raise StepFailed("GIT_FAILED", f"unexpected compose answer: {(out or [''])[0][:100]}")


def _receipt_update(ctx: OpContext, seq: int, statuses: tuple[str, ...], kind: str | None, **fields) -> None:
    ops = ctx.service
    sets = ", ".join(f"{k}=?" for k in fields)
    marks = ",".join("?" * len(statuses))
    with ops.journal.tx():
        cur = ops.db.execute(f"""UPDATE integration_receipts SET {sets}, updated_at=? WHERE operation_id=? AND seq=?
            AND status IN ({marks})""",  # noqa: S608 - fixed local column names
                             (*fields.values(), time.time(), ctx.operation_id, seq, *statuses))
        if cur.rowcount and kind:
            pv = ctx.op["external_refs"]
            ops.journal.api_event("integration", ctx.operation_id, kind,
                                  {"repository": pv.get("repository"), "pull_number": pv.get("pull_number"),
                                   "seq": seq, **{k: v for k, v in fields.items() if k in {"status", "method"}}},
                                  actor=ctx.actor)


def _push_attempted(ops: OperationService, operation_id: str) -> bool:
    """A push step exists that did not fail: from then on only reading the remote back can say what happened."""
    return ops.db.execute("""SELECT 1 FROM operation_steps WHERE operation_id=? AND name LIKE 'push.%'
        AND status != 'failed'""", (operation_id,)).fetchone() is not None


async def _run_apply(ctx: OpContext) -> dict:
    ops = ctx.service
    attempted = _push_attempted(ops, ctx.operation_id)
    try:
        repo, host, number = _admit_target(ops, ctx.target)
        pv = get_preview(ops.db, ctx.params["preview_id"])
        area = Area(ops, repo, host)
        if resource_policy.norm(pv["area_path"]) != area.path or pv["remote_url"] != area.url:
            raise OperationError("PREVIEW_MISMATCH", "the integration area or remote changed since the preview", 409)
    except (OperationError, ResourceReadOnly) as e:
        if not attempted:
            raise
        # Never "failed, nothing pushed" while a push may have landed: keep it resumable once fixed.
        raise NeedsAttention(getattr(e, "code", "CONFIG_CHANGED"), f"{e}; a push may already have landed, so this "
                             "stays open until the configuration is back and the remote can be read") from None
    gh = _gh(ops)
    sources = json.loads(pv["sources"])
    base = pv["head_sha"]
    head_ref = pv["head_ref"]
    tag = ctx.operation_id[3:15]
    t0 = int(ctx.op["created_at"])
    date = f"@{t0} +0000"
    if not (ctx.op.get("external_refs") or {}).get("preview_id"):  # first run: persist the per-source intent
        now = time.time()
        with ops.journal.tx():
            for s in sources:
                ops.db.execute("""INSERT OR IGNORE INTO integration_receipts(operation_id,seq,preview_id,repository,
                    pull_number,head_ref,source_kind,source_id,source_host,location_class,pinned_sha,mode,commits,
                    source_key,status,actor,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                               (ctx.operation_id, s["seq"], pv["preview_id"], repo.repository, number, head_ref,
                                s["kind"], s["id"], s["host"], s["location_class"], s["pin"], s["mode"],
                                json.dumps(s["commits"]) if s["commits"] else None, _source_key(s), "pending",
                                ctx.actor, now, now))
        ctx.set_refs(preview_id=pv["preview_id"], repository=repo.repository, pull_number=number, base_sha=base,
                     head_ref=head_ref)
        ctx.op["external_refs"] = {**(ctx.op.get("external_refs") or {}), "preview_id": pv["preview_id"],
                                   "repository": repo.repository, "pull_number": number}
    if not attempted:  # once a push may have happened, only the read-back decides; PR state no longer stops it
        pr = _pr_facts(await _read(gh.pull(repo.repository, number), "read the pull request"))
        if pr["state"] != "open" or pr["merged"]:
            raise OperationError("PR_CLOSED", f"PR #{number} is {pr['state']}; nothing was pushed")
        if not pr["head"]["repo"] or pr["head"]["repo"]["id"] != pv["repository_id"]:
            raise OperationError("PR_HEAD_IN_FORK", "the PR's head is not in the previewed repository")
        if pr["head"]["ref"] != head_ref:
            raise OperationError("TARGET_REF_CHANGED", f"the PR's head branch is now {pr['head']['ref']!r}")
        if not _has_step(ctx, "prepare") and pr["head"]["sha"] != base:
            raise OperationError("TARGET_HEAD_CHANGED", f"PR #{number} head moved to {str(pr['head']['sha'])[:12]} "
                                 f"after the preview ({base[:12]}); nothing ran. Preview again", 409)
        resource_policy.check_push_target(repo.integrate.protected_refs, pr)

    async def rerun(_request: dict) -> dict:
        return RERUN  # deterministic and compare-and-swap: a re-run gives the same result

    async def prepare() -> dict:
        execution_sources.revalidate(ops, sources)
        return {"lines": await area.run(apply_prepare_script(area, tag, head_ref, base, sources), long=True, mutation=True)}

    lines = (await ctx.step("prepare", prepare, request={"base": base, "pins": {s["seq"]: s["pin"] for s in sources}},
                            reconcile=rerun))["lines"]
    for x in lines:
        if x.startswith("target_moved "):
            raise OperationError("TARGET_HEAD_CHANGED", f"the remote head moved to {x.split(' ', 1)[1][:12]} after the "
                                 "preview; nothing ran. Preview again", 409)
        if x.startswith("source_changed "):
            _, i, now_sha = x.split(" ", 2)
            pin = next(s["pin"] for s in sources if s["seq"] == int(i))
            raise OperationError("SOURCE_CHANGED", f"source {i} moved from {pin[:12]} to {now_sha[:12]} after the "
                                 "preview; nothing ran. Preview again", 409)
        if x.startswith("source ") and x.endswith(" unavailable") or x == "target unavailable":
            raise OperationError("SOURCE_UNAVAILABLE", f"{x}; nothing ran", 409)
    prev = base
    allowed, ranges, must = [], [], []
    resolved = False
    for s in sources:
        seq = s["seq"]
        msg = (f"Integrate {s['kind']} {s['id'][:15]} into {head_ref}\n\n"
               f"Batc-Source: {s['kind']}:{s['id']} {s['pin']}\nBatc-Operation: {ctx.operation_id}")

        async def compose(s=s, prev=prev, msg=msg, seq=seq) -> dict:
            out = await area.run(compose_script(area, tag, head_ref, seq, prev, s, msg, date), mutation=True)
            return {"lines": out}

        out = (await ctx.step(f"compose.{seq}", compose, request={"seq": seq, "prev": prev, "src": s["pin"],
                                                                  "mode": s["mode"]}, reconcile=rerun))["lines"]
        method, after, picked, files = _compose_outcome(out, s, prev)
        if method == "conflict":
            after = await _resolution(ctx, area, tag, head_ref, s, prev, files)
            method = "resolved"
            resolved = True
            ranges.append((prev, s["pin"]))
            must.append((seq, s["pin"]))
            allowed.append(after)
            prev = after
            continue
        if not SHA.fullmatch(after):
            raise StepFailed("GIT_FAILED", f"compose for source {seq} gave no commit")
        _receipt_update(ctx, seq, ("pending",), "integration.composed",
                        status="already_included" if method == "already_included" else "composed", method=method,
                        base_sha=prev, integrated_sha=after, picked=json.dumps(picked) if picked else None)
        if method in {"merge", "fast_forward"}:
            ranges.append((prev, s["pin"]))
            must.append((seq, s["pin"]))
            if method == "merge":
                allowed.append(after)
        elif method == "pick":
            allowed.extend(p["new"] for p in picked)
        prev = after
    head = prev
    if head == base:
        return _finish(ctx, pv, sources, base, head, pushed=False, warnings=["NOTHING_NEW"])
    h12 = head[:12]

    async def check() -> dict:
        return {"lines": await area.run(check_script(area, tag, head_ref, base, head, ranges, allowed, must), mutation=True)}

    clines = (await ctx.step(f"check.{h12}", check, request={"base": base, "head": head}, reconcile=rerun))["lines"]
    for x in clines:
        if x.startswith("fail unexpected"):
            raise OperationError("UNEXPECTED_COMMITS", f"the composed result holds commits the preview did not list: "
                                 f"{x[16:][:200]}; nothing was pushed")
        if x.startswith("fail markers"):
            raise OperationError("CONFLICT_MARKERS", "the composed result contains conflict markers; nothing was pushed")
        if x.startswith("fail"):
            raise OperationError("GIT_FAILED", f"composition check failed ({x}); nothing was pushed")
    tree = next((x.split(" ", 1)[1] for x in clines if x.startswith("tree ")), None)
    if pv["predicted_tree"] and not resolved and tree != pv["predicted_tree"]:
        raise OperationError("TREE_MISMATCH", "the composed files differ from the preview's prediction; nothing was "
                             "pushed. Preview again")
    ctx.set_refs(composed_sha=head, composed_tree=tree)
    await _push(ctx, area, tag, head_ref, base, head, sources)
    return await _confirm_and_finish(ctx, area, tag, pv, sources, base, head, tree)


async def _host_read(ctx: OpContext, area: Area, script: str) -> list[str]:
    """A read that is not a step: an unreachable host waits and retries instead of failing the operation."""
    try:
        return await area.run(script)
    except (AmbiguousOutcome, OSError):
        _check_wait(ctx, "host_read_wait_started_at")
        raise Wait("waiting_external", "the host did not answer; retrying", 30) from None


async def _resolver_streaming(ops: OperationService, host: str, sid: str | None) -> bool | None:
    if not sid:
        return False
    try:
        meta = await _read_fleet(ops).client(host).invoke("claude:get-session-meta", {"sessionId": sid},
                                                          retry_on_disconnect=False)
    except Exception:  # noqa: BLE001 - unknown: the caller waits rather than reading a half-done worktree
        return None
    return bool(isinstance(meta, dict) and (meta.get("isStreaming") or meta.get("streaming")))


async def _resolution(ctx: OpContext, area: Area, tag: str, head_ref: str, src: dict, prev: str,
                      files: list[str]) -> str:
    """Source ``seq`` conflicts. Use its pinned resolution if one was accepted; otherwise, once a handed-off session
    is idle, validate what it committed and pin it by SHA (after which the worktree is never read again)."""
    ops, seq = ctx.service, src["seq"]
    for r in ops.db.execute("""SELECT response FROM operation_steps WHERE operation_id=? AND name LIKE ?
        AND status='succeeded'""", (ctx.operation_id, f"resolve.{seq}.%")):
        return json.loads(r["response"])["resolution"]
    _receipt_update(ctx, seq, ("pending",), "integration.conflict", status="conflict", base_sha=prev,
                    conflict_files=json.dumps(files))
    ctx.set_refs(conflict={"seq": seq, "base": prev, "files": files})
    rec = ops.db.execute("SELECT * FROM integration_receipts WHERE operation_id=? AND seq=?",
                         (ctx.operation_id, seq)).fetchone()
    label = f"source {seq} ({src['kind']} {src['id'][:15]})"
    if not rec["repair_worktree"]:
        raise NeedsAttention("INTEGRATION_CONFLICT", f"{label} conflicts in {len(files)} file(s): "
                             f"{', '.join(files[:5])}; earlier sources are composed in the connector's area and "
                             "nothing was pushed. Hand it to a managed session (integration.handoff), or cancel and "
                             "preview again without it")
    streaming = await _resolver_streaming(ops, area.host, rec["resolver_session_id"])
    if streaming is not False:
        _check_wait(ctx, "resolver_wait_started_at")
        raise Wait("waiting_external", "the session resolving the conflict is still working", 30)
    facts = dict(x.split(" ", 1) for x in await _host_read(ctx, area, inspect_script(
        area, tag, head_ref, rec["repair_worktree"], prev)) if " " in x)
    head = facts.get("head", "")
    if not SHA.fullmatch(head) or head == prev or facts.get("merge_head", "-") != "-":
        raise NeedsAttention("RESOLUTION_INCOMPLETE", f"{label}: the conflict is not committed yet in "
                             f"{rec['repair_branch']}; finish it with `git commit --no-edit`, then Resume")
    why = []
    if facts.get("dirty", "0").strip() != "0":
        why.append(f"{facts['dirty'].strip()} uncommitted change(s)")
    if facts.get("parents") != f"{head} {prev} {src['pin']}":
        why.append("the commit is not one merge of the PR side and the source")
    if facts.get("first_parents", "").strip() != "1":
        why.append("more than one commit on top of the PR side")
    if facts.get("markers", "0").strip() != "0":
        why.append("conflict markers remain")
    if why:
        raise NeedsAttention("RESOLUTION_INVALID", f"{label}: {'; '.join(why)}. Fix it in {rec['repair_branch']} "
                             "(one merge commit), then Resume")

    async def pin() -> dict:
        lines = await area.run(resolve_script(area, tag, head_ref, seq, prev, src["pin"], head), mutation=True)
        bad = next((x[8:] for x in lines if x.startswith("invalid ")), None)
        stat = next((x[5:] for x in lines if x.startswith("stat ")), "")
        return {"resolution": None if bad else head, "invalid": bad, "stat": stat}

    async def rerun(_request: dict) -> dict:
        return RERUN  # validation plus compare-and-swap: a re-run gives the same answer

    done = await ctx.step(f"resolve.{seq}.{head[:12]}", pin, request={"prev": prev, "src": src["pin"], "R": head},
                          reconcile=rerun)
    if not done.get("resolution"):
        raise NeedsAttention("RESOLUTION_INVALID", f"{label}: {done.get('invalid')}; fix it, then Resume")
    _receipt_update(ctx, seq, ("conflict",), "integration.resolved", status="resolved", method="merge",
                    integrated_sha=head, resolution_sha=head, remerge_stat=done.get("stat"))
    return head


async def _push(ctx: OpContext, area: Area, tag: str, head_ref: str, base: str, head: str, sources: list) -> None:
    ops = ctx.service
    h12 = head[:12]
    done = [(r["name"], json.loads(r["response"] or "{}")) for r in ops.db.execute(
        "SELECT name, response, status FROM operation_steps WHERE operation_id=? AND name LIKE ? AND status=?",
        (ctx.operation_id, f"push.{h12}#%", "succeeded"))]
    landed = next((resp for _, resp in done if resp.get("outcome", "").startswith("pushed")), None)
    if landed is None:
        no_effect = sum(1 for _, resp in done if resp.get("outcome") in {"remote_moved", "remote_rejected",
                                                                         "auth_failed"})
        n = no_effect + 1

        async def push() -> dict:
            execution_sources.revalidate(ops, sources)
            from .pm_delivery_lineage import record
            record(ctx, head, "push_intent")
            return parse_push(await area.run(push_script(area, tag, head_ref, base, head), mutation=True), head, head_ref, base)

        async def readback(_request: dict) -> dict | None:
            out = await area.run(readback_script(area, tag, head_ref, base, head))
            if "running" in out:
                return None  # the earlier push is still running on the host (its connection dropped): wait for it
            seen = next((x.split(" ", 1)[1] for x in out if x.startswith("seen ")), "unreadable")
            if seen == "unreadable" or "unreadable" in out:
                try:  # the API is a positive-only second witness; never re-sent on API evidence alone
                    status, pr = await _gh(ops).pull(area.repo.repository, ctx.op["external_refs"]["pull_number"])
                except GitHubAmbiguous:
                    return None
                if status == 200 and (pr.get("head") or {}).get("sha") == head:
                    return {"outcome": "pushed", "via": "api"}
                return None
            if seen == head:
                return {"outcome": "pushed", "reconciled": True}
            if seen == base:
                # Not proof on its own: the push may have landed and the branch been set back since. Send it again
                # only when GitHub says the composed commit does not exist there.
                try:
                    status, _ = await _gh(ops).commit(area.repo.repository, head)
                except GitHubAmbiguous:
                    return None
                if status == 200:
                    return {"outcome": "unproven_at_base"}
                return RERUN if status in {404, 422} else None
            if seen == "-":
                return {"outcome": "remote_missing"}
            if "contains_head" in out:
                return {"outcome": "pushed", "remote_moved_after": seen}
            if "contains_base" in out:
                return {"outcome": "remote_moved", "seen": seen}
            return {"outcome": "remote_rewritten", "seen": seen}

        landed = await ctx.step(f"push.{h12}#{n}", push, request={"url": area.url, "ref": head_ref, "base": base,
                                                                   "head": head}, reconcile=readback)
    outcome = landed.get("outcome")
    if outcome in {"pushed", "pushed_on_other_base", "pushed_recreated"}:
        from .pm_delivery_lineage import record
        record(ctx, head, "push_confirmed")
        with ops.journal.tx():
            cur = ops.db.execute("""UPDATE integration_receipts SET status='delivered', delivered_sha=?, delivered_at=?,
                updated_at=? WHERE operation_id=? AND status IN ('composed', 'resolved')""",
                                 (head, time.time(), time.time(), ctx.operation_id))
            if cur.rowcount:
                refs = ctx.op["external_refs"]
                ops.journal.api_event("integration", ctx.operation_id, "integration.delivered",
                                      {"repository": refs.get("repository"), "pull_number": refs.get("pull_number"),
                                       "head": head, "count": cur.rowcount}, actor=ctx.actor)
        ctx.set_refs(pushed_sha=head, push_old_sha=landed.get("old"))
        refs = ctx.op.get("external_refs") or {}
        if outcome != "pushed" and not refs.get("rewind_seen"):
            ctx.set_refs(rewind_seen=outcome)
            code = "REMOTE_REWOUND_BEFORE_PUSH" if outcome == "pushed_on_other_base" else "REMOTE_REF_RECREATED"
            raise NeedsAttention(code, f"the commits landed, but the branch was moved just before the push (it was at "
                                 f"{str(landed.get('old') or '?')[:12]}); check the PR. Resume to finish; nothing is "
                                 "ever forced")
        return
    messages = {
        "remote_moved": ("REMOTE_MOVED", "someone pushed to the PR while it was being updated; nothing was "
                         "overwritten or pushed. Cancel and preview again"),
        "remote_rewritten": ("REMOTE_REWRITTEN", "the PR branch was rewritten; nothing was pushed. Preview again"),
        "remote_missing": ("REMOTE_REF_MISSING", "the PR branch is gone from the remote; nothing was pushed"),
        "remote_rejected": ("PUSH_REJECTED", f"GitHub refused the push: {landed.get('reason', '')}"),
        "auth_failed": ("PUSH_AUTH_FAILED", f"the host's git credentials cannot push to {area.repo.repository}; fix "
                        "them on the host, then Resume"),
        "push_shape": ("INTERNAL_PUSH_SHAPE", "the push answer had an impossible shape; stopped"),
        "unproven_at_base": ("PUSH_UNPROVEN", "the PR branch is at its old head, but the composed commit exists on "
                             "GitHub, so an earlier push may have landed and been set back; nothing is pushed again. "
                             "Check the PR, then cancel and preview again"),
    }
    code, text = messages.get(outcome, ("UNCERTAIN_UNRESOLVED", f"unknown push outcome {outcome!r}"))
    raise NeedsAttention(code, text)


async def _confirm_and_finish(ctx: OpContext, area: Area, tag: str, pv: dict, sources: list, base: str, head: str,
                              tree: str | None) -> dict:
    """The push is proven by git. GitHub's API may lag behind it; that is a warning, never a wait, so a cancel can
    no longer catch a landed update half-way and record it as cancelled."""
    ops = ctx.service
    warnings = []
    try:
        status, pr = await _gh(ops).pull(area.repo.repository, pv["pull_number"])
    except GitHubAmbiguous:
        status, pr = 0, {}
    if not (status == 200 and (pr.get("head") or {}).get("sha") == head):
        try:
            out = await area.run(readback_script(area, tag, pv["head_ref"], base, head))
        except (AmbiguousOutcome, StepFailed, OSError):
            out = ["seen unreadable"]
        seen = next((x.split(" ", 1)[1] for x in out if x.startswith("seen ")), "unreadable")
        if seen == head:
            warnings.append("GITHUB_LAGGING")
        elif seen == "unreadable" or "running" in out:
            warnings.append("NOT_CONFIRMED_ON_GITHUB")
        elif "contains_head" in out:
            warnings.append("REMOTE_MOVED_AFTER")
        else:
            raise NeedsAttention("REMOTE_REWRITTEN_AFTER_PUSH", f"the push landed, but the branch is now {seen[:12]} "
                                 "without it; the receipts stay delivered")
    return _finish(ctx, pv, sources, base, head, pushed=True, warnings=warnings, tree=tree)


def _finish(ctx: OpContext, pv: dict, sources: list, base: str, head: str, *, pushed: bool, warnings: list,
            tree: str | None = None) -> dict:
    ops = ctx.service
    rows = receipts(ops.db, ctx.operation_id)
    refs = ctx.op.get("external_refs") or {}
    result = {"repository": pv["repository"], "pull_number": pv["pull_number"], "head_ref": pv["head_ref"],
              "html_url": json.loads(pv["document"]).get("html_url"), "old_head": base, "new_head": head,
              "pushed": pushed, "composed_tree": tree, "preview_id": pv["preview_id"],
              "added_commits": json.loads(pv["document"]).get("added_commits"), "sources": rows,
              "pushed_via": {"host": pv["host"], "remote_url": pv["remote_url"], "credential": "host"},
              "push_old_sha": refs.get("push_old_sha"), "warnings": warnings + (
                  [refs["rewind_seen"].upper()] if refs.get("rewind_seen") else []),
              "local_checkouts_changed": False}
    with ops.journal.tx():
        ops.journal.api_event("integration", ctx.operation_id, "integration.updated",
                              {"repository": pv["repository"], "pull_number": pv["pull_number"], "old": base,
                               "new": head, "pushed": pushed}, actor=ctx.actor)
    return result


# --------------------------------------------------------------------------- reads
def receipts(db, operation_id: str) -> list[dict]:
    op = db.execute("SELECT status FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
    unproven = db.execute("""SELECT 1 FROM operation_steps WHERE operation_id=? AND name LIKE 'push.%'
        AND status IN ('started', 'uncertain')""", (operation_id,)).fetchone() is not None
    out = []
    for r in db.execute("SELECT * FROM integration_receipts WHERE operation_id=? ORDER BY seq", (operation_id,)):
        d = dict(r)
        for k in ("commits", "picked", "conflict_files"):
            d[k] = json.loads(d[k]) if d[k] else None
        # Computed at read time: a cancel moves needs_attention to cancelled without running the handler.
        # A push that was never proven reads as unknown, not as not delivered.
        if d["status"] == "delivered" or not op or op["status"] not in {"failed", "cancelled"}:
            d["effective_status"] = d["status"]
        else:
            d["effective_status"] = "unknown" if unproven else "not_delivered"
        out.append(d)
    return out


def preview_document(db, preview_id: str) -> dict:
    pv = get_preview(db, preview_id)
    doc = json.loads(pv["document"])
    doc["expired"] = time.time() > pv["expires_at"]
    return doc


def integration_get(ops: OperationService, operation_id: str) -> dict:
    op = ops.get(operation_id)
    if op["action"].startswith("pm."):
        raise OperationError("NOT_FOUND", "integration not found", 404)
    if op["action"] not in {"integration.apply", "integration.preview", "integration.handoff"}:
        raise OperationError("NOT_AN_INTEGRATION", f"{operation_id} is {op['action']}", 422)
    pv_id = (op["params"] or {}).get("preview_id") or ("ipv_" + operation_id[3:])
    try:
        preview = preview_document(ops.db, pv_id)
    except OperationError:
        preview = None
    return {"operation": op, "preview": preview, "receipts": receipts(ops.db, operation_id)}


def integrations_list(ops: OperationService, repository: str, number: int, limit: int = 20) -> dict:
    ids = [r["operation_id"] for r in ops.db.execute(
        "SELECT operation_id, target FROM operations WHERE action='integration.apply' ORDER BY created_at DESC")
        if (lambda t: str(t.get("repository") or "").lower() == str(repository).lower()
            and t.get("pull_number") == number)(json.loads(r["target"]))][:max(1, min(100, int(limit)))]
    return {"integrations": [{**ops.get(i, steps=False), "receipts": receipts(ops.db, i)} for i in ids]}


def candidates(ops: OperationService, host: str, limit: int = 50, *, source_kind=None, source_id=None) -> dict:
    """What can go into a PR from this host, from the journal and inventory only (no host I/O)."""
    db = ops.db
    limit = max(1, min(200, int(limit)))
    inv = ops.context.get("inventory")
    selected = None
    if source_kind is not None or source_id is not None:
        if source_kind not in execution_sources.KINDS or not isinstance(source_id, str) or not 1 <= len(source_id) <= 256:
            raise OperationError("INVALID_PARAMS", "select an exact execution or task_command source", 422)
        selected = candidate_evidence(ops, execution_sources.describe(ops, source_kind, source_id))
        if selected["host"] != host:
            raise OperationError("SOURCE_ON_OTHER_HOST", "the selected execution is on another host", 422)

    def delivered(kind: str, sid: str) -> list[dict]:
        return [dict(r) for r in db.execute("""SELECT repository, pull_number, operation_id, pinned_sha, delivered_sha, delivered_at
            FROM integration_receipts WHERE source_kind=? AND source_id=? AND status='delivered'
            ORDER BY delivered_at DESC""", (kind, sid))]
    runs = []
    for r in db.execute("""SELECT r.operation_id, r.branch, r.created_at, r.session_id, r.agent, c.checkpoint_id,
        c.commit_sha, c.branch AS source_branch FROM checkpoint_runs r JOIN checkpoints c USING(checkpoint_id)
        WHERE r.host=? ORDER BY r.created_at DESC LIMIT ?""", (host, limit)):
        row = inv.get_session(host, r["session_id"]) if inv is not None else None
        runs.append({**dict(r), "kind": "checkpoint_run", "id": r["operation_id"],
                     "streaming": row.get("streaming") if row else None,
                     "session": candidate_session(row),
                     "result": {"status": "unverified", "commit_sha": None},
                     "delivered_to": delivered("checkpoint_run", r["operation_id"])})
    for candidate in execution_sources.candidates(ops, host, limit):
        row = inv.get_session(host, candidate["session_id"]) if inv is not None and candidate["session_id"] else None
        runs.append({**candidate, "session": candidate_session(row), "streaming": row.get("streaming") if row else None,
                     "result": {"status": "unverified", "commit_sha": None},
                     "delivered_to": delivered(candidate["kind"], candidate["id"])})
    runs.sort(key=lambda row: (row["created_at"], row["id"]), reverse=True)
    cps = []
    for r in db.execute("""SELECT checkpoint_id, commit_sha, branch, dirty, captured_at, source_session_id, note
        FROM checkpoints WHERE host=? ORDER BY captured_at DESC LIMIT ?""", (host, limit)):
        d = dict(r)
        d["dirty"] = None if d["dirty"] is None or d["dirty"] < 0 else d["dirty"]
        cps.append({**d, "kind": "checkpoint", "id": r["checkpoint_id"],
                    "result": {"status": "unverified", "commit_sha": r["commit_sha"]},
                    "delivered_to": delivered("checkpoint", r["checkpoint_id"])})
    return {"host": host, "agent_results": runs[:limit], "checkpoints": cps, "selected": selected}


def candidate_session(row: dict | None) -> dict:
    """Keep observation uncertainty; inactivity and accepted sends never prove task completion."""
    keys = ("streaming", "pending", "observed_at", "gone_at", "stale", "fields_stale", "stale_reason", "state")
    out = {key: (row or {}).get(key) for key in keys}
    activity = ((row or {}).get("state") or {}).get("evidence", {}).get("activity")
    if activity is not None:
        out["observed_at"] = activity.get("observed_at")
    return out


def candidate_evidence(ops, item):
    inv = ops.context.get("inventory")
    row = inv.get_session(item["host"], item["session_id"]) if inv is not None and item.get("host") in ops.context["fleet"].config.hosts and item.get("session_id") else None
    delivered = [dict(r) for r in ops.db.execute("""SELECT repository,pull_number,operation_id,pinned_sha,
        delivered_sha,delivered_at FROM integration_receipts WHERE source_kind=? AND source_id=?
        AND status='delivered' ORDER BY delivered_at DESC""", (item["kind"], item["id"]))]
    return {**item, "session": candidate_session(row), "result": {"status": "unverified", "commit_sha": None},
            "delivered_to": delivered}


async def pr_card(ops: OperationService, repository: str, number: int, method: str | None = None,
                  *, from_event: bool = False) -> dict:
    """The Delivery view's PR card: delivery's facts plus whether and how results can be integrated."""
    card = await pr_preview(ops, repository, number, method, from_event=from_event)
    card["integration"] = pr_integration(ops, card["repository"], number)
    return card


def usable_hosts(ops: OperationService, repo: GitHubRepo) -> list[str]:
    """integrate.hosts that can run integrations now: an SSH alias, a managed root, writes and orchestrate."""
    if not repo.integrate:
        return []
    fleet, runner = ops.context["fleet"], ops.context.get("git_runner")
    return [h for h in repo.integrate.hosts if h in fleet.config.hosts and runner and runner.available(h)
            and fleet.writes_enabled(h) and fleet.orchestrate_enabled(h) and fleet.config.host(h).managed_roots]


def pr_integration(ops: OperationService, repository: str, number: int) -> dict:
    repo = ops.context["github_config"].repos.get(repository.lower())
    allowed = bool(repo and repo.integrate)
    last = [dict(r) for r in ops.db.execute("""SELECT operation_id, seq, source_kind, source_id, delivered_sha,
        delivered_at FROM integration_receipts WHERE lower(repository)=? AND pull_number=? AND status='delivered'
        ORDER BY delivered_at DESC LIMIT 10""", (repository.lower(), number))]
    return {"allowed": allowed, "hosts": usable_hosts(ops, repo) if allowed else [],
            "in_progress_operation_id": integration_in_progress(ops, repository, number), "last_delivered": last}


# --------------------------------------------------------------------------- handoff
RESUMABLE_CONFLICTS = {"INTEGRATION_CONFLICT", "RESOLUTION_INCOMPLETE", "RESOLUTION_INVALID"}
MAX_HANDOFF_INSTRUCTIONS = 4000


def _conflict_of(ops: OperationService, apply_id: str) -> tuple[dict, dict, dict, dict]:
    """(apply operation, preview, conflicting source, its receipt) for an apply stopped at a conflict."""
    op = ops.get(apply_id, steps=False)
    if op["action"] != "integration.apply":
        raise OperationError("NOT_AN_INTEGRATION", f"{apply_id} is {op['action']}", 422)
    conflict = (op.get("external_refs") or {}).get("conflict")
    if op["status"] != "needs_attention" or op["error_code"] not in RESUMABLE_CONFLICTS or not conflict:
        raise OperationError("NOT_IN_CONFLICT", f"{apply_id} is {op['status']} ({op['error_code']}), not stopped at "
                             "a conflict", 409)
    pv = get_preview(ops.db, op["params"]["preview_id"])
    src = next(s for s in json.loads(pv["sources"]) if s["seq"] == conflict["seq"])
    rec = ops.db.execute("SELECT * FROM integration_receipts WHERE operation_id=? AND seq=?",
                         (apply_id, conflict["seq"])).fetchone()
    return op, pv, src, dict(rec)


def _handoff_workspace(ops: OperationService, repo: GitHubRepo, src: dict) -> str | None:
    """The BAT workspace for the resolving session: the conflicting source's own, else integrate.workspace."""
    if src["kind"] in execution_sources.KINDS:
        return src.get("workspace") or repo.integrate.workspace
    row = None
    if src["kind"] == "checkpoint":
        row = ops.db.execute("SELECT workspace_id, workspace_name FROM checkpoints WHERE checkpoint_id=?",
                             (src["id"],)).fetchone()
    elif src["kind"] == "checkpoint_run":
        row = ops.db.execute("""SELECT c.workspace_id, c.workspace_name FROM checkpoint_runs r JOIN checkpoints c
            USING(checkpoint_id) WHERE r.operation_id=?""", (src["id"],)).fetchone()
    return (row and (row["workspace_id"] or row["workspace_name"])) or repo.integrate.workspace


def _admit_handoff(ops: OperationService, principal: Principal, target: dict, params: dict, pre: dict) -> None:
    if not principal.allows("start"):
        raise OperationError("FORBIDDEN", "integration.handoff starts an agent session and also needs the 'start' "
                             "scope", 403)
    if not OP_ID.fullmatch(target["operation_id"]):
        raise OperationError("INVALID_TARGET", "operation_id is malformed", 422)
    op, pv, src, rec = _conflict_of(ops, target["operation_id"])
    repo, _, _ = _admit_target(ops, op["target"])
    if params.get("agent", "claude") not in {"claude", "codex"}:
        raise OperationError("INVALID_PARAMS", "agent must be claude or codex", 422)
    text = params.get("instructions", "")
    if not isinstance(text, str) or len(text) > MAX_HANDOFF_INSTRUCTIONS:
        raise OperationError("INVALID_PARAMS", f"instructions must be at most {MAX_HANDOFF_INSTRUCTIONS} characters",
                             422)
    if rec.get("resolver_operation_id"):
        other = ops.get(rec["resolver_operation_id"], steps=False)
        if other["status"] not in {"failed", "cancelled"}:
            raise OperationError("HANDOFF_EXISTS", f"{other['operation_id']} already hands this conflict to a "
                                 "session", 409)
    running = [r["operation_id"] for r in ops.db.execute(
        "SELECT operation_id, target, status FROM operations WHERE action='integration.handoff'")
        if json.loads(r["target"]).get("operation_id") == op["operation_id"]
        and r["status"] not in {"succeeded", "failed", "cancelled"}]
    if running:
        raise OperationError("HANDOFF_EXISTS", f"{running[0]} is already handing this conflict to a session", 409)
    if not _handoff_workspace(ops, repo, src):
        raise OperationError("RESOLVE_UNAVAILABLE", "no BAT workspace to start the session in; set "
                             "integrate.workspace", 409)


def repair_prompt(*, marker: str, worktree: str, branch: str, head_ref: str, prev: str, src: dict,
                  files: list[str], instructions: str) -> str:
    lines = [marker,
             "You are resolving a merge conflict for a pull request. Work only in this folder and on this branch.",
             "", f"Folder: {worktree}", f"Branch: {branch} (the PR's head branch is {head_ref})",
             f"Merging {src['kind']} {src['id']} at {src['pin']} onto {prev}.",
             f"Conflicted files: {', '.join(files) or '(see git status)'}", "",
             "Resolve the conflicts so both sides' intent is kept, run the project's tests if there are any, then "
             "finish the merge with `git commit --no-edit`. Make exactly one commit. Do not push, rebase, reset, "
             "amend, change git config, or touch other branches or folders."]
    if instructions.strip():
        lines += ["", "From the person:", instructions.strip()]
    return "\n".join(lines)


async def _run_handoff(ctx: OpContext) -> dict:
    ops = ctx.service
    op, pv, src, rec = _conflict_of(ops, ctx.target["operation_id"])
    repo, host, _ = _admit_target(ops, op["target"])
    conflict = op["external_refs"]["conflict"]
    prev, seq = conflict["base"], conflict["seq"]
    area = Area(ops, repo, host)
    r12 = ctx.operation_id[3:15]
    wt = f"{area.path}/wt/batc-fix-{r12}"
    branch = f"batc/fix-{r12}"
    resource_policy.check_repair_worktree(area.hc, area.path, wt, branch)  # before any host write
    ctx.set_refs(apply_operation_id=op["operation_id"], seq=seq, host=host, worktree_path=wt, branch=branch)

    async def prepare() -> dict:
        ctx.require_controller()
        return {"lines": await area.run(repair_script(area, r12, pv["head_ref"], wt, branch, prev, src["pin"]), mutation=True)}

    async def rerun(_request: dict) -> dict:
        return RERUN  # finds the worktree it made and reports it

    lines = (await ctx.step("repair.prepare", prepare, request={"worktree": wt, "branch": branch, "prev": prev,
                                                                "src": src["pin"]}, reconcile=rerun))["lines"]
    facts = dict(x.split(" ", 1) for x in lines if x.split(" ", 1)[0] in {"head", "merge_head", "first_parent"})
    files = [x[9:] for x in lines if x.startswith("conflict ")] or conflict["files"]
    fresh = facts.get("head") == prev and facts.get("merge_head") == src["pin"]
    if not (fresh or facts.get("first_parent") == prev):
        raise OperationError("REPAIR_STATE_MISMATCH", f"{wt} is not at the conflict being resolved; nothing was "
                             "started")
    marker = f"[batc integration {op['operation_id']} · source {seq} · {ctx.operation_id}]"
    text = repair_prompt(marker=marker, worktree=wt, branch=branch, head_ref=pv["head_ref"], prev=prev, src=src,
                         files=files, instructions=ctx.params.get("instructions", ""))
    started = await start_in_worktree(
        ctx, host=host, workspace=_handoff_workspace(ops, repo, src), agent=ctx.params.get("agent", "claude"),
        worktree=wt, branch=branch, head=prev, title=f"resolve {op['operation_id'][3:11]}", text=text, marker=marker,
        registry_fields={"integration_operation_id": op["operation_id"], "integration_seq": seq})
    with ops.journal.tx():
        cur = ops.db.execute("""UPDATE integration_receipts SET resolver_operation_id=?, resolver_session_id=?,
            repair_worktree=?, repair_branch=?, updated_at=? WHERE operation_id=? AND seq=? AND status='conflict'""",
                             (ctx.operation_id, started["session_id"], wt, branch, time.time(), op["operation_id"],
                              seq))
        if cur.rowcount:
            ops.journal.api_event("integration", op["operation_id"], "integration.handoff_started",
                                  {"repository": pv["repository"], "pull_number": pv["pull_number"], "seq": seq,
                                   "operation_id": ctx.operation_id, "session_id": started["session_id"]},
                                  actor=ctx.actor)
    return {"apply_operation_id": op["operation_id"], "seq": seq, "host": host, "session_id": started["session_id"],
            "worktree_path": wt, "branch": branch, "base_sha": prev, "source_sha": src["pin"],
            "conflict_files": files, "message_id": started["message_id"], "write_scope": "confined",
            "confinement": started["confinement"], "current_verification": started["current_verification"]}


def apply_request(doc: dict) -> dict:
    """The one integration.apply request for a preview document (CLI and MCP docs; the Dashboard sends the same):
    sources come only from the preview, bound by its head and digest, and the key makes a double click one push."""
    return {"action": "integration.apply",
            "target": {"host": doc["host"], "repository": doc["repository"], "pull_number": doc["pull_number"]},
            "params": {"preview_id": doc["preview_id"]},
            "preconditions": {"expected_head_sha": doc["target"]["head_sha"], "preview_digest": doc["digest"]},
            "idempotency_key": "integrate." + doc["preview_id"]}


ACTIONS = [
    ActionDef("integration.preview", "integrate", "Pin a PR head and chosen results, list what would enter the PR",
              _run_preview, _admit_preview, ("host", "repository")),
    ActionDef("integration.apply", "integrate", "Compose a reviewed preview into the PR head with one normal push",
              _run_apply, _admit_apply, ("host", "repository")),
    ActionDef("integration.handoff", "integrate", "Start a managed session that resolves an integration conflict",
              _run_handoff, _admit_handoff, ("operation_id",)),
]
