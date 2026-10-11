"""Create one generated branch from a pinned managed execution; never replace a ref."""
from __future__ import annotations

import asyncio
import re

from . import integration
from .operations import AmbiguousOutcome, StepFailed


def branch(operation_id):
    if not isinstance(operation_id, str) or not re.fullmatch(r"op_[0-9a-f]{32}", operation_id):
        raise ValueError("Publication requires a central operation ID")
    return "batc/pr-" + operation_id[3:]


def _prefix(area, kind, operation_id, digest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Publication requires an intent digest")
    return (area.prelude(kind, operation_id, branch(operation_id)) +
            f'intent={digest}; receipt="$repo/batc-pr-create-$op"\n')


def prepare(area, operation_id, intent, digest):
    src, candidate, base = intent["source"], intent["candidate_sha"], intent["base_sha"]
    return (_prefix(area, "publication-prepare", operation_id, digest) + area.create() +
        "check_source() {\n" + integration._live_tip(src) +
        f'[ "$live" = {integration._sha(candidate)} ] || {{ echo "error PR_SOURCE_MOVED"; return 0; }}\n' +
        integration._fetch_block(1, "$live", integration._from(src)) +
        'echo "source_ready $live"\n}\ncheck_source\n' +
        f'base={integration._sha(base)}; head={integration._sha(candidate)}\n' +
        f'l=$(gn ls-remote "$url" {integration._q("refs/heads/" + intent["base_ref"])} 2>/dev/null) '
        '|| { echo unreadable; exit 0; }\n' +
        f'[ "$(printf "%s\\n" "$l" | exact {integration._q("refs/heads/" + intent["base_ref"])})" = "$base" ] '
        '|| { echo "error PR_BASE_MOVED"; exit 0; }\n' +
        'gn fetch -q --no-tags --no-write-fetch-head "$url" "$base" 2>/dev/null || { echo unreadable; exit 0; }\n' +
        f'gi merge-base --is-ancestor {integration._sha(src["start"])} "$head" '
        '|| { echo "error PR_SOURCE_HISTORY_CHANGED"; exit 0; }\n' +
        'gi merge-base --is-ancestor "$base" "$head" || { echo "error PR_BASE_NOT_ANCESTOR"; exit 0; }\n'
        '[ "$base" != "$head" ] || { echo "error PR_EMPTY_CHANGE"; exit 0; }\n'
        '[ "$(gi rev-list --count "$base..$head")" -le 200 ] || { echo "error PR_CHANGE_LIMIT"; exit 0; }\n'
        '[ "$(gi -c core.quotePath=false diff --name-only --no-renames "$base" "$head" | head -n 501 | wc -l)" -le 500 ] '
        '|| { echo "error PR_CHANGE_LIMIT"; exit 0; }\n'
        'cas "refs/batc/pr/$op/candidate" "$head"\n'
        'cas "refs/batc/pr/$op/base" "$base"\n'
        'echo "prepared $head $(gi rev-parse "$head^{tree}")"\n'
        # Git quotes tabs/newlines/control bytes; one physical line per path.
        'gi -c core.quotePath=false diff --name-only --no-renames "$base" "$head" | head -c 128001 | sed "s/^/file /"\n'
        'echo files_end\n')


def push(area, operation_id, intent, digest):
    ref = "refs/heads/" + branch(operation_id)
    head = integration._sha(intent["candidate_sha"])
    # The expected old OID is literally empty. Neither the lease nor the refspec
    # accepts caller input; a foreign ref, including an ancestor, cannot be moved.
    return (_prefix(area, "publication-push", operation_id, digest) + "ident\n" +
        f'head={head}\n' +
        '[ "$(gi rev-parse --verify "refs/batc/pr/$op/candidate")" = "$head" ] '
        '|| { echo "error PR_PIN_CHANGED"; exit 0; }\n'
        '[ ! -e "$receipt" ] && [ ! -L "$receipt" ] || { echo "error PR_RECEIPT_EXISTS"; exit 0; }\n'
        'seen=$(remote_head); [ "$seen" = unreadable ] && { echo unreadable; exit 0; }\n'
        '[ "$seen" = - ] || { echo "error PR_BRANCH_EXISTS"; exit 0; }\n'
        f'l=$(gn ls-remote "$url" {integration._q("refs/heads/" + intent["base_ref"])} 2>/dev/null) '
        '|| { echo unreadable; exit 0; }\n'
        f'[ "$(printf "%s\\n" "$l" | exact {integration._q("refs/heads/" + intent["base_ref"])})" = {integration._sha(intent["base_sha"])} ] '
        '|| { echo "error PR_BASE_MOVED"; exit 0; }\n'
        '# batc-int:before-push\n'
        'set +e\n'
        'out=$(gn -c core.abbrev=40 -c push.default=nothing -c push.followTags=false -c push.gpgSign=false '
        '-c push.negotiate=false -c push.recurseSubmodules=no push --porcelain --no-verify '
        f'{integration._q("--force-with-lease=" + ref + ":")} "$url" {integration._q(head + ":" + ref)} 2>&1); rc=$?\n'
        'set -e\n'
        f'created=$(printf "%s\\n" "$out" | awk -F "\\t" -v r={integration._q(head + ":" + ref)} '
        "'$1 == \"*\" && $2 == r && $3 == \"[new branch]\" { n++ } END { print n+0 }')\n"
        'if [ "$rc" != 0 ] || [ "$created" != 1 ]; then\n'
        f'  refused=$(printf "%s\\n" "$out" | awk -F "\\t" -v r={integration._q(head + ":" + ref)} '
        "'$1 == \"!\" && $2 == r { n++ } END { print n+0 }')\n"
        '  [ "$refused" = 1 ] && { echo "error PR_BRANCH_CREATE_REFUSED"; exit 0; }\n'
        '  echo unreadable; exit 0\nfi\n'
        # noclobber + no caller-selected path. Receipt absence after a lost reply
        # is unresolved even if the remote happens to contain this same SHA.
        '(set -C; printf "created %s %s %s\\n" "$intent" "$head" "$ref" > "$receipt") '
        '|| { echo unreadable; exit 0; }\n'
        'seen=$(remote_head); [ "$seen" = "$head" ] || { echo unreadable; exit 0; }\n'
        'cat "$receipt"\n')


def readback(area, operation_id, digest):
    return (_prefix(area, "publication-readback", operation_id, digest) + "ident\n" +
        '[ -f "$receipt" ] && [ ! -L "$receipt" ] || { echo unresolved; exit 0; }\n'
        '[ "$(wc -c < "$receipt")" -le 256 ] || { echo unresolved; exit 0; }\n'
        'cat "$receipt"\necho "seen $(remote_head)"\n')


async def run(area, script, *, guard=None, timeout=None):
    """Reuse the integration mutex with our final authority check inside it."""
    lock = integration._area_locks.setdefault((area.host, area.path), asyncio.Lock())
    async with lock:
        if guard is not None:
            guard()
            from .pm_source_lineage import require_frame
            require_frame()
        limit = area.repo.integrate.fetch_timeout_s if timeout is None else timeout()
        output = await area.ops.context["git_runner"].run(area.host, script, timeout_s=limit)
    lines = output.splitlines()
    for line in lines:
        if line.startswith("error "):
            raise StepFailed(line.split()[1], "The fixed publication Git step was refused")
    if "unreadable" in lines:
        raise AmbiguousOutcome("Publication Git receipt is unavailable")
    return lines
