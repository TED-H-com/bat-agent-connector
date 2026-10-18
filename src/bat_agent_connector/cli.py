"""`batc` command-line interface (mirrors the MCP tools)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__, api_auth, lifecycle, orchestrate, resource_policy, service, triage
from .config import DEFAULT_BAT_PROFILES_DIR, default_config_path, load_config
from .errors import BatError, WriteRefused
from .fleet import Fleet
from .importer import read_bat_profiles, render_hosts_toml
from .operations import OperationError
from .redact import redact


def _print(obj: Any, as_json: bool, render=None) -> None:
    if as_json or render is None:
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    else:
        render(obj)


def _table(rows: list[list[Any]], headers: list[str]) -> None:
    cells = [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [len(h) for h in headers]
    for r in cells:
        for i, v in enumerate(r):
            widths[i] = min(max(widths[i], len(v)), 48)
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    for r in cells:
        print(fmt.format(*[v if len(v) <= 48 else v[:47] + "…" for v in r]))


def r_hosts(o):
    _table(
        [
            [
                h["name"],
                h["url"],
                h.get("server_version"),
                h.get("ping_ms"),
                "rw" if h["writes_enabled"] else "ro",
                "ok" if h.get("reachable") else (h.get("error") or "-"),
            ]
            for h in o["hosts"]
        ],
        ["host", "url", "version", "ping_ms", "mode", "status"],
    )


def r_status(o):
    lat = o["latency"]
    print(f"{o['host']}: bat-server {o['server_version']} ({o['protocol']})")
    print(
        f"  latency: connect={lat['connect_ms']}ms auth={lat['auth_ms']}ms ping={lat['ping_ms']} median={lat['ping_median_ms']}ms"
    )
    print(
        f"  workspaces={o['workspaces']} terminals={o['terminals']} agent_sessions={o['agent_sessions']} "
        f"{o['agent_sessions_by_kind']} loaded={o['loaded']} streaming={o['streaming']}"
    )
    print(f"  writes: {'enabled' if o['writes_enabled'] else 'disabled'}")


def r_workspaces(o):
    _table(
        [[w["host"], w["name"], w["agent_sessions"], w["terminals"], w["folder"]] for w in o["workspaces"]],
        ["host", "workspace", "agents", "terms", "folder"],
    )
    for h, e in o["errors"].items():
        print(f"! {h}: {e}", file=sys.stderr)


def r_sessions(o):
    rows = []
    for s in o["sessions"]:
        state = "streaming" if s["streaming"] else ("loaded" if s["loaded"] else "unloaded")
        pend = (s["pending"] or {}).get("kind") or ""
        rows.append(
            [
                s["host"],
                s["session_id"][:8],
                s["workspace"],
                s["agent_kind"],
                state,
                pend,
                s.get("provenance", "?"),
                s["last_activity_age"],
                s["last_activity"],
                s["title"],
            ]
        )
    _table(
        rows,
        ["host", "session", "workspace", "agent", "state", "pending", "source", "age", "last_activity", "title"],
    )
    print(f"({o['count']} of {o['total_matched']})")
    for h, e in o["errors"].items():
        print(f"! {h}: {e}", file=sys.stderr)


def r_policy(o):
    print(f"{o['host']}: managed_roots={o['managed_roots'] or '-'} "
          f"shared_clone_worktrees={o['shared_clone_worktrees']}")
    if "mutations" in o:
        _table([[m["action"], m["via"], ", ".join(m["channels"]) or "-", m["rule"]] for m in o["mutations"]],
               ["action", "via", "channels", "rule"])
        return
    print(f"{o['session_id']}: {o['provenance']} ({o['api_access']}) folder={o['workdir']} "
          f"owner={o['workdir_owner']} isolation={o['isolation'] or '-'}")
    for e in o["evidence"]:
        print(f"  evidence: {e}")
    _table([[a, "yes" if v["allowed"] else "no", v.get("code") or "", v.get("reason") or ""]
            for a, v in o["actions"].items()], ["action", "allowed", "code", "reason"])


def r_read(o):
    print(
        f"# {o['host']} {o['session_id']} [{o['workspace']}] {o['agent_kind']} "
        f"loaded={o['loaded']} streaming={o['streaming']}"
    )
    if o.get("after"):
        print(f"# after {o['after']['iso']}: turn_started={o.get('turn_started')} turn_done={o.get('turn_done')}")
        if not o["messages"]:
            print(f"# {o.get('note')}")
    for m in o["messages"]:
        tag = m["role"] if m["role"] != "tool" else f"tool:{m.get('tool')}"
        print(f"\n--- {m.get('ts')} {tag}\n{m.get('text')}")
    if o.get("pending"):
        print("\n*** PENDING:", json.dumps(o["pending"], ensure_ascii=False))
    if o.get("next_offset") is not None:
        print(f"\n(older: --offset {o['next_offset']})")


def r_triage(o):
    rows = [
        [
            s["host"],
            s["session_id"][:8],
            s.get("workspace"),
            s.get("agent_kind"),
            s.get("state"),
            s.get("source"),
            s.get("confidence"),
            s.get("resets") or "",
            s.get("evidence") or "",
        ]
        for s in o["sessions"]
    ]
    _table(rows, ["host", "session", "workspace", "agent", "state", "source", "conf", "resets", "evidence"])
    print(f"({o['count']} rows; by state: {o['counts_by_state']}; jev: {o['jev']}; {o['elapsed_s']}s)")
    for h, e in o["errors"].items():
        print(f"! {h}: {e}", file=sys.stderr)


def r_cleanup(o):
    rows = [
        [
            (d.get("session_id") or "")[:8],
            d.get("workspace"),
            d.get("agent_kind"),
            d.get("branch") or "",
            d.get("state") or "",
            d.get("decision"),
            "; ".join(d.get("reasons") or []),
            "; ".join(d.get("actions") or []),
        ]
        for d in o["decisions"]
        if not d.get("noop")
    ]
    _table(rows, ["session", "workspace", "agent", "branch", "state", "decision", "reasons", "actions"])
    print(f"{'DRY RUN ' if o['dry_run'] else ''}{o['host']}: {o['counts']} (jev: {o['jev']})")
    if o.get("escalation_summary"):
        print(o["escalation_summary"])


def _parse_answers(items: list[str] | None):
    if not items:
        return None
    if all("=" not in i for i in items):
        return items
    out = {}
    for i in items:
        k, sep, v = i.partition("=")
        if not sep:
            raise BatError("use --answer 'question=answer' (or only positional answers)")
        out[k] = v
    return out


async def _run(args) -> Any:
    if args.cmd == "policy" and os.environ.get("BATC_API_TOKEN"):
        from .task_daemon import request
        return await asyncio.to_thread(request, "session_policy", host=args.host, session_id=args.session,
                                       _auth_token=os.environ["BATC_API_TOKEN"], entry="cli"), r_policy
    if args.cmd in {"read", "wait"}:
        from .session_observation import client_request
        params = {"host": args.host, "session_id": args.session, "after": args.after}
        if args.cmd == "read":
            params.update(last_n=args.n, offset=args.offset, include_tools=args.tools, max_chars=args.max_chars)
        else:
            params.update(until=args.until, timeout_s=args.timeout, require_new=args.require_new)
        result = await client_request("session_" + args.cmd,
            token=os.environ.get("BATC_API_TOKEN"), entry="cli", **params)
        return result, r_read if args.cmd == "read" else None
    cfg = load_config(args.config)
    fleet = Fleet(cfg, read_only=args.read_only, idle_timeout=0, actor="cli")
    try:
        c = args.cmd
        if c == "hosts":
            return await service.hosts_list(fleet, probe=not args.no_probe), r_hosts
        if c == "status":
            return await service.host_status(fleet, args.host), r_status
        if c == "workspaces":
            return await service.workspaces_list(fleet, args.host), r_workspaces
        if c == "sessions":
            return await service.sessions_list(
                fleet,
                args.host,
                args.workspace,
                args.agent,
                args.loaded,
                args.active_within,
                args.pending,
                not args.fast,
                args.limit,
            ), r_sessions
        if c == "policy":
            return await resource_policy.session_policy(fleet, args.host, args.session), r_policy
        if c in {"send", "continue", "interrupt", "answer", "permissions"}:
            from .task_daemon import request

            if not args.confirm or not fleet.writes_enabled(args.host):
                raise WriteRefused(f"{c} needs --confirm and an enabled local write tier")
            params = {"host": args.host, "session_id": args.session,
                      "confirm": True, "idempotency_key": args.key}
            if args.control_version is not None:
                params["control_version"] = args.control_version
            if c in {"send", "continue"}:
                params.update(text=sys.stdin.read() if c == "send" and args.text == "-" else args.text,
                              queue=args.queue)
                if c == "send":
                    params["message_id"] = args.message_id
            elif c == "answer":
                params.update(answers=_parse_answers(args.answer), permission=args.permission,
                              deny_message=args.deny_message, tool_use_id=args.tool_use_id,
                              dont_ask_again=args.dont_ask_again)
            else:
                params["mode"] = args.mode
            try:
                out = await asyncio.to_thread(request, "session_set_permissions" if c == "permissions" else "session_" + c, entry="cli", timeout=40,
                                              _auth_token=os.environ.get("BATC_API_TOKEN") or None, **params)
            except OSError:
                raise WriteRefused(f"central {c} request failed; its outcome may be unknown. "
                                   "Read the saved operation or retry with the same explicit key") from None
            return out, None
        if c in ("triage", "quota"):
            states = ["quota_exhausted"] if c == "quota" else args.state
            return await triage.sessions_triage(
                fleet, args.host, args.workspace, args.agent, states, args.jev, not args.loaded_only
            ), r_triage
        if c in ("relay", "fanout-plan"):
            if not args.confirm and (c == "fanout-plan" or not args.dry_run):
                raise WriteRefused(f"{c} needs --confirm")
            msg = sys.stdin.read() if args.message == "-" else args.message
            brief = json.loads(args.brief) if args.brief and args.brief.lstrip().startswith("{") else args.brief
            if c == "relay":
                from .task_daemon import request
                try:
                    out = await asyncio.to_thread(request, "session_relay", _auth_token=os.environ.get("BATC_API_TOKEN") or None,
                        host=args.host, message=msg, workspace=args.workspace, session_id=args.session, channel=args.channel,
                        thread=args.thread, earlier=args.earlier, brief=brief, request_fanout=args.fanout is not None,
                        max_items=args.fanout, confirm=args.confirm, dry_run=args.dry_run, queue=args.queue,
                        start_if_missing=args.start_if_missing, idempotency_key=args.key, control_version=args.control_version,
                        entry="cli", timeout=40)
                except OSError:
                    raise WriteRefused("central relay reply unavailable; retain the original explicit key and operation") from None
                return out, None
            from .task_daemon import request
            try:
                out = await asyncio.to_thread(request, "fanout_plan_session", _auth_token=os.environ.get("BATC_API_TOKEN") or None,
                    host=args.host, workspace=args.workspace, message=msg, max_items=args.max_items, channel=args.channel,
                    thread=args.thread, earlier=args.earlier, brief=brief, confirm=args.confirm, idempotency_key=args.key,
                    entry="cli", timeout=40)
            except OSError:
                raise WriteRefused("central planner reply unavailable; retain the original key and operation") from None
            return out, None
        if c == "fanout-start":
            from .task_daemon import request
            if not args.confirm and not args.dry_run:
                raise WriteRefused("fanout-start needs --confirm")
            try:
                out = await asyncio.to_thread(request, "fanout_from_plan", _auth_token=os.environ.get("BATC_API_TOKEN") or None,
                    host=args.host, session_id=args.session, confirm=args.confirm, dry_run=args.dry_run,
                    agent=args.agent, max_items=args.max_items, idempotency_key=args.key, entry="cli", timeout=40)
            except OSError:
                raise WriteRefused("central fanout reply unavailable; retain the original key and operation") from None
            return out, None
        if c == "approve-pending":
            from .task_daemon import request
            token = os.environ.get("BATC_API_TOKEN")
            if not token:
                raise WriteRefused("approval preview/apply requires this client's BATC_API_TOKEN")
            if args.dry_run:
                return await asyncio.to_thread(request, "approval_preview", _auth_token=token,
                    entry="cli", host=args.host, workspace=args.workspace, timeout=40), None
            if not args.confirm or not fleet.writes_enabled(args.host):
                raise WriteRefused("bulk apply requires --confirm and the local write tier")
            if not args.preview_file or args.selection is None:
                raise OperationError("BULK_PREVIEW_REQUIRED", "use --dry-run, then --preview-file and explicit --selection JSON", 422)
            doc = json.loads(Path(args.preview_file).read_text())
            selection = json.loads(args.selection)
            return await asyncio.to_thread(request, "approve_pending", _auth_token=token,
                entry="cli", host=args.host, workspace=args.workspace, confirm=True, timeout=40,
                preview_token=doc.get("preview_token"), expected_fingerprint=doc.get("fingerprint"),
                selection=selection, idempotency_key=args.key), None
        if c == "failover":
            from .task_daemon import request
            if not args.confirm and not args.dry_run:
                raise WriteRefused("failover needs --confirm")
            try:
                out = await asyncio.to_thread(request, "session_failover", _auth_token=os.environ.get("BATC_API_TOKEN") or None,
                    entry="cli", host=args.host, session_id=args.session, confirm=args.confirm,
                    all_exhausted=args.all_exhausted, dry_run=args.dry_run, model=args.model, force=args.force,
                    tail_messages=args.tail, workspace=args.workspace, instructions=args.instructions,
                    archive_only=args.archive_only, idempotency_key=args.key, timeout=40)
            except (OSError, TimeoutError):
                raise WriteRefused("central failover reply unavailable; retain the original key and operation") from None
            return out, None
        if c == "cleanup":
            return await lifecycle.session_cleanup(
                fleet, args.host, args.confirm, not args.apply, args.session
            ), r_cleanup
        if c == "record-verification":
            from .task_daemon import request
            if not args.confirm:
                raise WriteRefused("record-verification needs --confirm")
            try:
                out = await asyncio.to_thread(request, "session_record_verification",
                    _auth_token=os.environ.get("BATC_API_TOKEN") or None, entry="cli",
                    host=args.host, session_id=args.session, candidate_commit=args.commit,
                    command=args.command, exit_code=args.exit_code, environment=args.environment,
                    log_ref=args.log_ref, confirm=True, idempotency_key=args.key, timeout=40)
            except (OSError, TimeoutError):
                raise WriteRefused("central verification reply unavailable; retain the original key and operation") from None
            return out, None
        if c == "worktrees":
            return await orchestrate.worktree_status(fleet, args.host, args.workspace), None
        if c == "wt-status":
            return await orchestrate.session_worktree_status(
                fleet, args.host, args.session, args.diff, args.max_diff_chars
            ), None
        if c == "start":
            from .task_daemon import request
            if not args.confirm:
                raise WriteRefused("start needs --confirm")
            # Central admission checks current tiers after replaying an existing key.
            # A changed local tier must not hide a previously accepted start receipt.
            prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
            try:
                out = await asyncio.to_thread(request, "session_start", _auth_token=os.environ.get("BATC_API_TOKEN"),
                    host=args.host, workspace=args.workspace, agent=args.agent, confirm=True, prompt=prompt,
                    model=args.model, use_worktree=not args.no_worktree, title=args.title,
                    idempotency_key=args.key, entry="cli", timeout=40)
            except OSError:
                raise WriteRefused("central start request failed; its outcome may be unknown. "
                                   "Read the saved operation or retry with the same explicit key") from None
            return out, None
        if c == "merge":
            from .task_daemon import request
            if not args.confirm:
                raise WriteRefused("merge needs --confirm")
            try:
                out = await asyncio.to_thread(request, "worktree_merge", _auth_token=os.environ.get("BATC_API_TOKEN") or None,
                    entry="cli", host=args.host, session_id=args.session, confirm=True, idempotency_key=args.key, timeout=40)
            except (OSError, TimeoutError):
                raise WriteRefused("central merge reply unavailable; retain the original key and operation") from None
            return out, None
        if c == "remove-worktree":
            return await orchestrate.worktree_remove(
                fleet,
                args.host,
                args.session,
                args.confirm,
                args.delete_branch,
                args.allow_unmerged,
                args.discard_uncommitted,
            ), None
        if c == "fanout":
            if args.start and not args.confirm:
                raise WriteRefused("fanout --start needs --confirm")
            plan = orchestrate.fanout_plan(orchestrate.read_plan(args.plan), max_tasks=args.max_tasks)
            if not args.start:
                return plan, None
            if not (args.host and args.workspace):
                raise BatError("--start needs --host and --workspace")
            from .task_daemon import request
            try:
                out = await asyncio.to_thread(request, "fanout_start", _auth_token=os.environ.get("BATC_API_TOKEN") or None,
                    host=args.host, workspace=args.workspace, plan=plan["tasks"], agent=args.agent, model=args.model,
                    confirm=args.confirm, idempotency_key=args.key, entry="cli", timeout=40)
            except OSError:
                raise WriteRefused("central fanout reply unavailable; retain the fixed original plan/key and operation") from None
            return out, None
        raise BatError(f"unknown command {c}")
    finally:
        await fleet.close()


def cmd_import(args) -> int:
    profs = read_bat_profiles(args.profiles_dir)
    rename = dict(r.split("=", 1) for r in args.rename or [])
    text = render_hosts_toml(profs, profiles_dir=args.profiles_dir, rename=rename, only=args.only)
    if args.output == "-":
        print(text)
        return 0
    out = Path(args.output).expanduser() if args.output else default_config_path()
    if out.exists() and not args.force:
        print(f"{out} exists; pass --force to overwrite (or --output -)", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    print(
        f"wrote {out} ({len([p for p in profs if not args.only or p['id'] in args.only])} hosts, writes disabled)"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="batc", description="Better Agent Terminal connector CLI (unofficial)")
    ap.add_argument("--config", help=f"hosts.toml (default {default_config_path()})")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--read-only", action="store_true", help="refuse all write commands regardless of config")
    ap.add_argument("--version", action="version", version=__version__)
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("hosts", help="list configured hosts (+ probe)")
    p.add_argument("--no-probe", action="store_true")
    p = sp.add_parser("status", help="host status")
    p.add_argument("host")
    p = sp.add_parser("workspaces", help="list workspaces")
    p.add_argument("host", nargs="?")
    p = sp.add_parser("sessions", help="list agent sessions, most recent first")
    p.add_argument("host", nargs="?")
    p.add_argument("--workspace")
    p.add_argument("--agent", choices=["claude", "codex"])
    p.add_argument("--loaded", action="store_true", help="only sessions loaded in the host runtime")
    p.add_argument("--active-within", type=float, metavar="HOURS")
    p.add_argument("--pending", choices=["auto", "all", "none"], default="auto")
    p.add_argument("--fast", action="store_true", help="skip transcript/archive lookups for last activity")
    p.add_argument("--limit", type=int, default=50)
    p = sp.add_parser("policy", help="who may change what: a host's mutation table, or one session's verdicts")
    p.add_argument("host")
    p.add_argument("session", nargs="?")
    p = sp.add_parser("read", help="read latest messages through the central daemon (own observe token required)")
    p.add_argument("host")
    p.add_argument("session")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--tools", action="store_true", help="include tool calls")
    p.add_argument("--max-chars", type=int, default=12000)
    p.add_argument("--after", help="turn_marker from relay/send: show only newer messages")
    p = sp.add_parser("wait", help="wait for turn-end / ask-user through central (own observe token required)")
    p.add_argument("host")
    p.add_argument("session")
    p.add_argument("--until", choices=["attention", "turn-end", "ask-user"], default="attention")
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--require-new", action="store_true")
    p.add_argument("--after", help="turn_marker from relay/send: wait for the reply to that send")

    p = sp.add_parser("send", help="WRITE: send a message (needs --confirm and writes=true)")
    p.add_argument("host")
    p.add_argument("session")
    p.add_argument("text", help="message text, or - for stdin")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--message-id")
    p.add_argument("--queue", action="store_true", help="queue behind a running turn")
    p.add_argument("--key", help="reuse for retries; omitted means no cross-call deduplication")
    p.add_argument("--control-version", type=int, help="expected owning task control version")
    p = sp.add_parser("continue", help="WRITE: nudge a session with 'continue'")
    p.add_argument("host")
    p.add_argument("session")
    p.add_argument("--text", default="continue")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--queue", action="store_true")
    p.add_argument("--key", help="reuse for retries; omitted means no cross-call deduplication")
    p.add_argument("--control-version", type=int, help="expected owning task control version")
    p = sp.add_parser("interrupt", help="WRITE: interrupt the running turn")
    p.add_argument("host")
    p.add_argument("session")
    p.add_argument("--mode", choices=["soft", "hard"], default="soft")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--key", help="reuse for retries; omitted means no cross-call deduplication")
    p.add_argument("--control-version", type=int, help="expected owning task control version")
    p = sp.add_parser("answer", help="WRITE: answer a pending question / permission")
    p.add_argument("host")
    p.add_argument("session")
    p.add_argument("--answer", action="append", help="'question=answer' or positional answer (repeatable)")
    p.add_argument("--permission", choices=["allow", "deny"])
    p.add_argument("--deny-message")
    p.add_argument("--tool-use-id")
    p.add_argument("--dont-ask-again", action="store_true", help="Codex: accept for the rest of the session")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--key", help="reuse for retries; omitted means no cross-call deduplication")
    p.add_argument("--control-version", type=int, help="expected owning task control version")
    p = sp.add_parser("triage", help="classify sessions (quota / waiting / working / done), pattern + optional Jev")
    p.add_argument("host", nargs="?")
    p.add_argument("--workspace")
    p.add_argument("--agent", choices=["claude", "codex"])
    p.add_argument("--state", action="append", choices=list(triage.STATES))
    p.add_argument("--jev", choices=["auto", "always", "never"], default="auto")
    p.add_argument("--loaded-only", action="store_true")
    p = sp.add_parser("quota", help="list sessions stuck on a quota / usage limit (with evidence)")
    p.add_argument("host", nargs="?")
    p.add_argument("--workspace")
    p.add_argument("--agent", choices=["claude", "codex"])
    p.add_argument("--jev", choices=["auto", "always", "never"], default="auto")
    p.add_argument("--loaded-only", action="store_true")
    p = sp.add_parser("permissions", help="WRITE: set a live session's permission mode (allow_all|default)")
    p.add_argument("host")
    p.add_argument("session")
    p.add_argument("--mode", choices=["allow_all", "default"], default="allow_all")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--key", help="reuse for retries; omitted means each call is independent")
    p.add_argument("--control-version", type=int, help="expected owning task control version")
    p = sp.add_parser("approve-pending", help="review with --dry-run, then apply an explicit preview selection")
    p.add_argument("host")
    p.add_argument("--workspace")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--preview-file", help="JSON saved from --dry-run")
    p.add_argument("--selection", help='JSON [{"item_id":"...","mode":null|"default"|"allow_all"}]')
    p.add_argument("--key", help="reuse for retry; omitted means an independent batch")

    p = sp.add_parser("relay", help="WRITE: send a task verbatim + a labeled brief to a session (default: main)")
    p.add_argument("host")
    p.add_argument("--workspace")
    p.add_argument("--session")
    p.add_argument("--message", required=True, help="the person's exact words, or - for stdin")
    p.add_argument("--brief", help="relay's interpretation: text or JSON {goal, context, constraints, acceptance}")
    p.add_argument("--channel")
    p.add_argument("--thread")
    p.add_argument("--earlier", action="append", help="earlier message in the thread, verbatim (repeatable)")
    p.add_argument("--fanout", type=int, metavar="N", help="ask for a bat-fanout plan of at most N items")
    p.add_argument("--queue", action="store_true")
    p.add_argument("--start-if-missing", action="store_true", help="create a new managed Codex worktree if no writable target exists")
    p.add_argument("--key", help="reuse the exact original key after a lost reply; omitted means independent call")
    p.add_argument("--control-version", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p = sp.add_parser("fanout-plan", help="ORCHESTRATE: start a read-only Codex planner for a bat-fanout plan")
    p.add_argument("host")
    p.add_argument("workspace")
    p.add_argument("--message", required=True)
    p.add_argument("--brief")
    p.add_argument("--max-items", type=int)
    p.add_argument("--channel")
    p.add_argument("--thread")
    p.add_argument("--earlier", action="append")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--key", help="reuse the exact original key after a lost reply")
    p = sp.add_parser("fanout-start", help="ORCHESTRATE: start worktrees exactly per a session's bat-fanout block")
    p.add_argument("host")
    p.add_argument("session")
    p.add_argument("--agent", choices=["claude", "codex"], default="codex")
    p.add_argument("--max-items", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm", action="store_true")

    p.add_argument("--key", help="reuse the exact original key after a lost reply")
    p = sp.add_parser("worktrees", help="list worktree sessions on a host")
    p.add_argument("host")
    p.add_argument("--workspace")
    p = sp.add_parser("wt-status", help="worktree status of one session")
    p.add_argument("host")
    p.add_argument("session")
    p.add_argument("--diff", action="store_true")
    p.add_argument("--max-diff-chars", type=int, default=20000)
    p = sp.add_parser("start", help="ORCHESTRATE: start a session (default: in a new worktree)")
    p.add_argument("host")
    p.add_argument("workspace")
    p.add_argument("--agent", choices=["claude", "codex"], default="claude")
    p.add_argument("--prompt", help="initial prompt, or - for stdin")
    p.add_argument("--model")
    p.add_argument("--title")
    p.add_argument("--no-worktree", action="store_true")
    p.add_argument("--key", help="stable operation key; omitted means a new independent start")
    p.add_argument("--confirm", action="store_true")
    p = sp.add_parser("merge", help="INTEGRATE: durable managed worktree merge; verifier SSH must use BAT's Git context")
    p.add_argument("--key", help="retain the original key after reply loss; omitted means an independent request")
    p.add_argument("host")
    p.add_argument("session")
    p.add_argument("--confirm", action="store_true")
    p = sp.add_parser("remove-worktree", help="DISABLED: use reviewed resource-cleanup preview/apply")
    p.add_argument("host")
    p.add_argument("session")
    p.add_argument("--delete-branch", action="store_true")
    p.add_argument("--allow-unmerged", action="store_true")
    p.add_argument("--discard-uncommitted", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p = sp.add_parser("failover", help="ORCHESTRATE: continue a quota-exhausted Claude session with Codex")
    p.add_argument("host")
    p.add_argument("session", nargs="?")
    p.add_argument("--all-exhausted", action="store_true")
    p.add_argument("--workspace", help="with --all-exhausted: limit to one workspace")
    p.add_argument("--model")
    p.add_argument("--tail", type=int, default=12, help="recent messages to include in the handoff")
    p.add_argument("--force", action="store_true", help="fail over even if not detected as exhausted")
    p.add_argument("--instructions", help="replace the default 'continue the task' steps (single session)")
    p.add_argument("--archive-only", action="store_true", help="mark successor work as archive-only; reviewed cleanup keeps its commits")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--key", help="original explicit operation key; omitted means an independent request")
    p = sp.add_parser("cleanup", help="read-only legacy session evaluation; use resource-cleanup to reclaim resources")
    p.add_argument("host")
    p.add_argument("session", nargs="?")
    p.add_argument("--apply", action="store_true", help="disabled: returns LEGACY_CLEANUP_DISABLED; use resource-cleanup apply")
    p.add_argument("--dry-run", action="store_true", help="report only (default)")
    p.add_argument("--confirm", action="store_true")
    p = sp.add_parser("record-verification", help="ORCHESTRATE: bind a test run to the current clean commit")
    p.add_argument("--key", help="reuse the original key after reply loss; omitted means an independent record")
    p.add_argument("host")
    p.add_argument("session")
    p.add_argument("--commit", required=True, help="full current candidate commit hash")
    p.add_argument("--command", required=True, help="exact verification command")
    p.add_argument("--exit-code", required=True, type=int)
    p.add_argument("--environment", required=True, help="where and with which runtime the command ran")
    p.add_argument("--log-ref", required=True, help="durable verification log path or URL")
    p.add_argument("--confirm", action="store_true")
    p = sp.add_parser("fanout", help="split a plan into worktree task prompts (and optionally start them)")
    p.add_argument("plan", help="markdown plan file")
    p.add_argument("--max-tasks", type=int, default=8)
    p.add_argument("--start", action="store_true", help="ORCHESTRATE: start one worktree session per task")
    p.add_argument("--host")
    p.add_argument("--workspace")
    p.add_argument("--agent", choices=["claude", "codex"], default="claude")
    p.add_argument("--model")
    p.add_argument("--confirm", action="store_true")

    p.add_argument("--key", help="reuse the exact original key after a lost reply")
    p = sp.add_parser(
        "import-bat", help="generate hosts.toml from BAT's profiles/index.json (no tokens copied)"
    )
    p.add_argument("--profiles-dir", default=DEFAULT_BAT_PROFILES_DIR)
    p.add_argument("--output", help="output path (default: config path; - for stdout)")
    p.add_argument("--rename", action="append", metavar="PROFILE_ID=NAME")
    p.add_argument("--only", action="append", metavar="PROFILE_ID")
    p.add_argument("--force", action="store_true")
    sp.add_parser("mcp", help="run the MCP server on stdio (see bat-agent-connector-mcp --help)")
    p = sp.add_parser("serve", help="run the loopback task daemon and worker (no deployment is made)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=18796)
    p.add_argument("--db", help="SQLite task journal path")
    p = sp.add_parser("task-events", help="read the task milestone feed (started/needs_ted/done/failed)")
    p.add_argument("--since", type=int, default=0, help="cursor from a previous read (0 = from the beginning)")
    p.add_argument("--limit", type=int, default=50, help="max milestones (0 = only report head_cursor)")
    p = sp.add_parser("task-reconcile", help="attest one uncertain task command and optionally send a new prompt")
    p.add_argument("--key", help="operation idempotency key (reuse on retries)")
    p.add_argument("--control-version", type=int, help="required task control version")
    p.add_argument("--task-id", required=True)
    p.add_argument("--command-id", required=True)
    p.add_argument("--outcome", required=True, choices=["delivered", "not_delivered", "superseded"])
    p.add_argument("--actor", required=True, choices=["operator", "ted"])
    p.add_argument("--source", required=True, help="operator ticket or user message reference")
    p.add_argument("--evidence", required=True, help="what was inspected; never a guessed result")
    p.add_argument("--observed-result", default="none", choices=["none", "milestone", "review_pass"])
    p.add_argument("--turn-ref", help="BAT turn/message reference required for observed result")
    p.add_argument("--candidate-commit")
    p.add_argument("--tree-hash")
    p.add_argument("--next-prompt-file", help="explicit new prompt file; never reuses uncertain text")
    p = sp.add_parser("resource-cleanup", help="preview and apply reviewed resource cleanup; retained content and permanent history")
    csp = p.add_subparsers(dest="cleanup_cmd", required=True)
    c = csp.add_parser("preview", help="pure read; signed plan expires after 15 minutes")
    targets = c.add_mutually_exclusive_group(required=True)
    for flag in ("item", "checkpoint", "integration", "host", "task"):
        targets.add_argument("--" + flag)
    c.add_argument("--include-children", action="store_true")
    c.add_argument("--discard-uncommitted", action="append", default=[], metavar="RESOURCE_ID",
                   help="destroys uncommitted content; needs cleanup_discard (person-controlled)")
    c.add_argument("--release-undelivered", action="append", default=[], metavar="RESOURCE_ID",
                   help="keeps commits and branch; needs cleanup, result remains undelivered")
    c.add_argument("--json", action="store_true")
    c = csp.add_parser("apply", help="execute exactly a reviewed preview through cleanup.apply")
    c.add_argument("--preview-file", help="JSON output saved from preview")
    c.add_argument("--preview-token")
    c.add_argument("--fingerprint")
    c.add_argument("--key", required=True, help="keep this key after a lost reply")
    c.add_argument("--confirm", action="store_true")
    c.add_argument("--json", action="store_true")
    for name in ("retained", "history"):
        c = csp.add_parser(name)
        c.add_argument("--host")
        c.add_argument("--query")
        c.add_argument("--original-id")
        c.add_argument("--resource-id")
        c.add_argument("--limit", type=int, default=50)
        c.add_argument("--cursor")
        c.add_argument("--json", action="store_true")
    p = sp.add_parser("api-token", help="manage /api/v1 tokens on the local task daemon (admin)")
    tsp = p.add_subparsers(dest="api_token_cmd", required=True)
    t = tsp.add_parser("issue", help="issue a token for an actor (printed once)")
    t.add_argument("--actor", required=True, help="e.g. operator-dashboard, hermes, grokbot")
    t.add_argument("--scope", action="append", required=True,
                   choices=list(api_auth.SCOPES))
    t.add_argument("--ttl-days", type=float)
    t.add_argument("--label")
    tsp.add_parser("list", help="list actors, scopes and expiry (never tokens)")
    t = tsp.add_parser("revoke", help="revoke every token of an actor")
    t.add_argument("--actor", required=True)
    p = sp.add_parser("artifact", help="Connector-owned immutable attachments")
    asp = p.add_subparsers(dest="artifact_cmd", required=True)
    c = asp.add_parser("upload", help="upload a file; local paths never enter prompts")
    c.add_argument("file")
    c.add_argument("--confirm", action="store_true")
    c.add_argument("--key", required=True, help="reuse for retries of the same file")
    c.add_argument("--artifact-id")
    c.add_argument("--expected-latest-revision", type=int)
    c.add_argument("--type", default="application/octet-stream")
    c = asp.add_parser("capture-preview", help="review one manual-session file without modifying its source")
    c.add_argument("host")
    c.add_argument("session_id")
    c.add_argument("relative_path")
    c = asp.add_parser("capture", help="save the exact reviewed manual file as an immutable artifact")
    c.add_argument("--preview-file", required=True, help="JSON saved from capture-preview")
    c.add_argument("--key", required=True, help="reuse this key after a lost reply")
    c.add_argument("--confirm", action="store_true")
    c = asp.add_parser("managed-capture-preview", help="review one managed file with central execution lineage")
    c.add_argument("host")
    c.add_argument("session_id")
    c.add_argument("relative_path")
    c.add_argument("--execution-operation-id")
    c.add_argument("--task-id")
    c.add_argument("--command-id")
    c = asp.add_parser("managed-capture", help="save the exact reviewed managed file")
    c.add_argument("--preview-file", required=True)
    c.add_argument("--key", required=True)
    c.add_argument("--confirm", action="store_true")
    c = asp.add_parser("accept", help="record approval of an exact managed artifact revision only")
    c.add_argument("artifact_id")
    c.add_argument("revision", type=int)
    c.add_argument("--digest", required=True)
    c.add_argument("--source-fingerprint", required=True)
    c.add_argument("--receipt", required=True)
    c.add_argument("--key", required=True)
    c.add_argument("--confirm", action="store_true")
    c = asp.add_parser("list")
    c.add_argument("--limit", type=int, default=50)
    c.add_argument("--cursor")
    for name in ("show", "download"):
        c = asp.add_parser(name)
        c.add_argument("artifact_id")
        c.add_argument("revision", type=int)
        if name == "download":
            c.add_argument("--output", required=True, help="new local file; existing files are refused")

    p = sp.add_parser("inventory", help="read persisted observation; never polls or starts a session")
    isp = p.add_subparsers(dest="inventory_cmd", required=True)
    c = isp.add_parser("sessions")
    for name in ("host", "profile-id", "work-item-id", "execution-id", "provider", "provenance", "access", "cursor"):
        c.add_argument("--" + name)
    c.add_argument("--project-id", action="append")
    for name in ("has-tab", "loaded", "streaming", "stale", "attention"):
        c.add_argument("--" + name, choices=["true", "false"])
    c.add_argument("--lifecycle", choices=["active", "ended", "unknown"])
    c.add_argument("--relation-scope", choices=["current", "history"], default="history")
    c.add_argument("--include-gone", action="store_true")
    c.add_argument("--order", choices=["id", "activity"], default="id")
    c.add_argument("--limit", type=int, default=50)
    c = isp.add_parser("session")
    c.add_argument("host")
    c.add_argument("session_id")
    c = isp.add_parser("worktree")
    c.add_argument("worktree_id")
    c = isp.add_parser("hosts")
    c.add_argument("--host")
    c.add_argument("--discovery", action="store_true")
    c = isp.add_parser("discovery")
    c.add_argument("host")
    c.add_argument("--after", type=int, default=0)
    c.add_argument("--limit", type=int, default=20)
    c = isp.add_parser("events", description="Read journal events after a durable cursor. Session updates include "
                       "fields_stale/field_evidence changes; observation/activity timestamps alone emit no update.")
    c.add_argument("--after", type=int, default=0)
    c.add_argument("--limit", type=int, default=100)
    c.add_argument("--kind")
    c.add_argument("--related-resource-type", choices=["session", "worktree", "execution"])
    c.add_argument("--related-resource-id")
    for command in ("history", "relations"):
        p = sp.add_parser(command, help="read resource journal " + command)
        rsp = p.add_subparsers(dest="resource_type", required=True)
        for resource_type in ("session", "worktree", "execution"):
            c = rsp.add_parser(resource_type)
            if resource_type == "session":
                c.add_argument("host")
                c.add_argument("session_id")
            else:
                c.add_argument("resource_id")
            c.add_argument("--cursor")
            c.add_argument("--limit", type=int, default=50)
            if command == "history":
                c.add_argument("--order", choices=["asc", "desc"], default="desc")
                c.add_argument("--kind", action="append")
                c.add_argument("--since", type=float, help="inclusive occurrence UTC epoch seconds; excludes unknown times")
                c.add_argument("--until", type=float, help="inclusive occurrence UTC epoch seconds; excludes unknown times")
            else:
                c.add_argument("--execution-id")
                c.add_argument("--include-closed", choices=["true", "false"], default="true")
    p = sp.add_parser("repository", help="start from an explicitly bound published GitHub version")
    rsp = p.add_subparsers(dest="repository_cmd", required=True)
    for name in ("preview", "continue"):
        c = rsp.add_parser(name)
        c.add_argument("repository")
        c.add_argument("host")
        c.add_argument("workspace_id", help="exact configured BAT workspace ID")
        c.add_argument("--ref", required=True, help="exact refs/heads/... name")
        if name == "continue":
            c.add_argument("--sha", required=True, help="reviewed full published head SHA")
            c.add_argument("--repository-id", required=True, type=int)
            c.add_argument("--binding-digest", required=True)
            c.add_argument("--prompt", required=True)
            c.add_argument("--agent", choices=["claude", "codex"], default="claude")
            c.add_argument("--title")
            c.add_argument("--model")
            c.add_argument("--artifact", action="append", help="Exact ready ArtifactRef JSON; repeat for each input")
            c.add_argument("--project-id")
            c.add_argument("--project-version", type=int)
            c.add_argument("--key", help="original idempotency key; omitted creates an independent request")
            c.add_argument("--confirm", action="store_true")
    p = sp.add_parser("checkpoint", help="record a session's commit, then continue from it in a managed session")
    csp = p.add_subparsers(dest="checkpoint_cmd", required=True)
    c = csp.add_parser("create", help="record a checkpoint (reads only; works on sessions created in BAT)")
    c.add_argument("host")
    c.add_argument("session_id")
    c.add_argument("--commit", help="full SHA from the session's history (default: HEAD)")
    c.add_argument("--note", help="the request this checkpoint is for, verbatim")
    c.add_argument("--artifact", action="append", help="ArtifactRef JSON; repeat for each immutable input")
    c.add_argument("--last-n", type=int, default=20, help="conversation messages to keep (0-50)")
    c.add_argument("--key", help="idempotency key (default: a new one, printed with the result)")
    c = csp.add_parser("continue", help="start a new managed session at the checkpoint's commit")
    c.add_argument("checkpoint_id")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--instructions")
    g.add_argument("--instructions-file")
    c.add_argument("--agent", choices=["claude", "codex"], default="claude")
    c.add_argument("--artifact", action="append", help="ArtifactRef JSON; omitted uses the checkpoint set")
    c.add_argument("--source-head", help="observed source HEAD SHA (required with attachments)")
    c.add_argument("--key", help="idempotency key (default: a new one, printed with the result)")
    c = csp.add_parser("revalidate", help="confirm the original inputs and resume the same continuation")
    c.add_argument("operation_id")
    c.add_argument("--source-head", required=True)
    c.add_argument("--manifest", required=True, help="external_refs.input_manifest_digest")
    c.add_argument("--key", required=True)
    c = csp.add_parser("list", help="recent checkpoints")
    c.add_argument("--host")
    c.add_argument("--session")
    c.add_argument("--limit", type=int, default=20)
    c = csp.add_parser("show", help="one checkpoint, its excerpt and the sessions started from it")
    c.add_argument("checkpoint_id")
    p = sp.add_parser("delivery", help="review, edit metadata and merge a GitHub PR")
    dsp = p.add_subparsers(dest="delivery_cmd", required=True)
    c = dsp.add_parser("pr", help="read a PR and save a fixed merge scope preview")
    c.add_argument("repository")
    c.add_argument("number", type=int)
    c.add_argument("--method", choices=["merge", "squash", "rebase"])
    c = dsp.add_parser("update-pr", help="edit title/body at a reviewed metadata digest (integrate)")
    c.add_argument("repository")
    c.add_argument("number", type=int)
    c.add_argument("--metadata-digest", required=True)
    c.add_argument("--title")
    c.add_argument("--body-file")
    c.add_argument("--key", required=True)
    c = dsp.add_parser("merge", help="merge a reviewed immutable mpv_ preview (merge; deploy for --recipe)")
    c.add_argument("--preview", required=True)
    c.add_argument("--recipe")
    c.add_argument("--generation", type=int, help="deployment preview's generation (with --recipe)")
    c.add_argument("--recipe-digest", help="deployment preview's digest (with --recipe)")
    c.add_argument("--key", required=True)
    for name in ("preview", "history", "show"):
        c = dsp.add_parser(name, help=f"read deployment {name}")
        c.add_argument("deployment_id" if name == "show" else "recipe")
        if name == "history":
            c.add_argument("--cursor")
            c.add_argument("--limit", type=int, default=50)
    for name in ("deploy", "rollback", "retry"):
        c = dsp.add_parser(name, help=f"{name} through the reviewed configured recipe (deploy scope)")
        c.add_argument("recipe")
        if name == "deploy":
            c.add_argument("--sha", required=True)
        else:
            c.add_argument("deployment_id")
        c.add_argument("--generation", type=int, required=True)
        c.add_argument("--recipe-digest", required=True)
        c.add_argument("--key", required=True)
    p = sp.add_parser("integrate", help="put results into an existing PR's head branch (one normal push)")
    isp = p.add_subparsers(dest="integrate_cmd", required=True)
    c = isp.add_parser("candidates", help="agent results and checkpoints on a host, and where they went")
    c.add_argument("--host", required=True)
    c = isp.add_parser("preview", help="pin the PR head and sources; list every commit and file that would enter")
    c.add_argument("--host", required=True)
    c.add_argument("--repo", required=True, help="owner/name")
    c.add_argument("--pr", type=int, required=True)
    c.add_argument("--source", action="append", required=True,
                   help="kind:id, in order (checkpoint:cp_..., checkpoint_run:op_..., execution:op_..., task_command:COMMAND_ID, branch:NAME)")
    c.add_argument("--pick", action="append", default=[], help="SEQ=SHA,SHA: copy only these commits of source SEQ")
    c.add_argument("--key", help="idempotency key (default: a new one; the same key returns the same preview)")
    c = isp.add_parser("apply", help="compose a reviewed preview and push it to the PR head")
    c.add_argument("preview_id")
    c = isp.add_parser("handoff", help="start a managed session that resolves an apply's conflict, then Resume")
    c.add_argument("operation_id")
    c.add_argument("--agent", choices=["claude", "codex"], default="claude")
    c.add_argument("--instructions-file", help="your note for the session (optional)")
    c.add_argument("--key", help="idempotency key (default: a new one)")
    c = isp.add_parser("show", help="one preview (ipv_...) or integration operation (op_...)")
    c.add_argument("id")
    p = sp.add_parser("project", help="projects: the connector's own grouping of work items")
    psp = p.add_subparsers(dest="project_cmd", required=True)
    c = psp.add_parser("list", help="the project tree with work item counts")
    c.add_argument("--archived", action="store_true", help="also list archived projects")
    c = psp.add_parser("show", help="one project and its work item tree")
    c.add_argument("project_id")
    c.add_argument("--archived", action="store_true", help="also list archived work items")
    c.add_argument("--projection", choices=("full", "navigation"), default="full", help="read full items or compact navigation summaries")
    for name in ("create", "update"):
        c = psp.add_parser(name, help=f"{name} a project")
        c.add_argument("name" if name == "create" else "project_id")
        if name == "update":
            c.add_argument("--name")
        c.add_argument("--description")
        c.add_argument("--parent", help="parent project ID ('' for the top level)")
        c.add_argument("--repo", action="append", help="owner/name (repeat; replaces the list on update)")
        c.add_argument("--task-project", help="the Task Service project name this project covers")
        if name == "update":
            g = c.add_mutually_exclusive_group()
            g.add_argument("--archive", action="store_true")
            g.add_argument("--restore", action="store_true")
    p = sp.add_parser("item", help="work items: goals, requests, acceptance, steps and completion")
    wsp = p.add_subparsers(dest="item_cmd", required=True)
    c = wsp.add_parser("list", help="work items, most recently changed first")
    c.add_argument("--project")
    c.add_argument("--state", choices=["todo", "doing", "waiting", "awaiting_approval", "done"])
    c.add_argument("--pending", action="store_true", help="only items waiting for a person's decision")
    c.add_argument("--unread", action="store_true", help="only work-item versions this principal has not marked read")
    c.add_argument("--limit", type=int, default=50)
    c.add_argument("--cursor", help="next_cursor from the previous page")
    c = wsp.add_parser("show", help="one work item with its links and history")
    c.add_argument("work_item_id")
    for name in ("create", "update"):
        c = wsp.add_parser(name, help=f"{name} a work item")
        if name == "create":
            c.add_argument("project_id")
            c.add_argument("title")
            c.add_argument("--derived-from", help="work item ID this one branches from")
        else:
            c.add_argument("work_item_id")
            c.add_argument("--title")
            c.add_argument("--state", choices=["todo", "doing", "waiting", "done"])
            c.add_argument("--check", type=int, action="append", default=[], help="mark step N (1-based) done")
            c.add_argument("--uncheck", type=int, action="append", default=[], help="mark step N not done")
            g = c.add_mutually_exclusive_group()
            g.add_argument("--archive", action="store_true", help="archive it and everything under it")
            g.add_argument("--restore", action="store_true")
        c.add_argument("--goal")
        c.add_argument("--attachment", action="append", help="JSON ArtifactRef plus role: input|result; replaces the set")
        c.add_argument("--request-file", help="the request, verbatim, from a file")
        c.add_argument("--acceptance")
        c.add_argument("--step", action="append", help="a step (repeat; replaces the list on update)")
        c.add_argument("--parent", help="parent work item ID ('' for the top level)")
    for name, text in (("approve", "accept it as done (needs the approve scope)"),
                       ("continue", "send a done claim back: not finished")):
        c = wsp.add_parser(name, help=text)
        c.add_argument("work_item_id")
        c.add_argument("--note")
        # Approving accepts the content you read: pass completion.fingerprint from `batc item show`.
        c.add_argument("--fingerprint", required=name == "approve",
                       help="completion.fingerprint from `batc item show` (the content you read)")
    c = wsp.add_parser("link", help="link it to a session (host/id), checkpoint, operation, task or PR (o/r#n)")
    c.add_argument("work_item_id")
    c.add_argument("kind", choices=["session", "checkpoint", "operation", "task", "pull_request"])
    c.add_argument("ref")
    c.add_argument("--note")
    c.add_argument("--remove", action="store_true", help="remove the link instead (it stays in the history)")
    p = sp.add_parser("op", help="show one operation, or list recent ones; --cancel / --resume one")
    p.add_argument("operation_id", nargs="?")
    steer = p.add_mutually_exclusive_group()
    steer.add_argument("--cancel", action="store_true", help="stop it before its next step (local admin)")
    steer.add_argument("--resume", action="store_true", help="run a needs_attention operation again (local admin)")
    p.add_argument("--status", action="append")
    p.add_argument("--limit", type=int, default=20)
    sp.add_parser("config-path", help="print the config path")
    return ap


def cmd_artifact(args) -> int:
    from .artifact_client import content_request, upload
    from .task_daemon import request

    token = os.environ.get("BATC_API_TOKEN") or None
    if args.artifact_cmd == "upload":
        if not args.confirm:
            raise ValueError("artifact upload requires --confirm")
        path = Path(args.file)
        limit = request("api_capabilities", _auth_token=token)["artifacts"]["limits"]["max_file_bytes"]
        with path.open("rb") as file:
            data = file.read(limit + 1)
        if len(data) > limit:
            raise ValueError("ARTIFACT_TOO_LARGE")
        out = upload(data, path.name, args.key, media_type=args.type, artifact_id=args.artifact_id,
                     expected_latest_revision=args.expected_latest_revision, token=token)
    elif args.artifact_cmd == "capture-preview":
        out = request("artifact_capture_preview", _auth_token=token, entry="cli", host=args.host,
                      session_id=args.session_id, relative_path=args.relative_path)
    elif args.artifact_cmd == "managed-capture-preview":
        selector = {k: getattr(args, k) for k in ("execution_operation_id", "task_id", "command_id") if getattr(args, k)}
        out = request("artifact_managed_capture_preview", _auth_token=token, entry="cli", host=args.host,
                      session_id=args.session_id, relative_path=args.relative_path, **selector)
    elif args.artifact_cmd == "accept":
        if not args.confirm:
            raise ValueError("artifact accept requires --confirm")
        out = request("op_submit", _auth_token=token, entry="cli", action="artifact.accept",
                      target={"artifact_id": args.artifact_id, "revision": args.revision},
                      params={"digest": args.digest, "source_fingerprint": args.source_fingerprint, "receipt": args.receipt},
                      preconditions={}, idempotency_key=args.key)
    elif args.artifact_cmd in {"capture", "managed-capture"}:
        if not args.confirm:
            raise ValueError("artifact capture requires --confirm")
        with Path(args.preview_file).open("rb") as file:
            raw = file.read(65537)
        if len(raw) > 65536:
            raise ValueError("capture preview exceeds its bound")
        preview = json.loads(raw)
        preview = preview.get("preview", preview)
        out = request("op_submit", _auth_token=token, entry="cli",
                      action="artifact.capture.managed" if args.artifact_cmd == "managed-capture" else "artifact.capture",
                      target={"preview_id": preview["preview_id"]}, params={"preview_token": preview["preview_token"]},
                      preconditions={"expected_fingerprint": preview["fingerprint"]}, idempotency_key=args.key)
    elif args.artifact_cmd == "list":
        out = request("artifacts_list", _auth_token=token, limit=args.limit, cursor=args.cursor)
    else:
        out = request("artifact_get", _auth_token=token, artifact_id=args.artifact_id, revision=args.revision)
        if args.artifact_cmd == "download":
            data = content_request(out["artifact"]["content_url"], token=token)
            with Path(args.output).open("xb") as file:
                file.write(data)
    _print(out, True)
    return 0


def cmd_repository(args) -> int:
    import uuid

    from .task_daemon import request
    target = {k: getattr(args, k) for k in ("repository", "host", "workspace_id")}
    if args.repository_cmd == "preview":
        out = request("repository_preview", **target, source_ref=args.ref, entry="cli")
    else:
        if not args.confirm:
            raise WriteRefused("repository continue requires --confirm")
        key = args.key or "cli-" + str(uuid.uuid4())
        out = request("op_submit", action="repository.continue", target=target,
            params={"source_ref": args.ref, "source_sha": args.sha, "prompt": args.prompt, "agent": args.agent,
                    **({"title": args.title} if args.title is not None else {}),
                    **({"model": args.model} if args.model is not None else {}),
                    **({"artifacts": [json.loads(ref) for ref in args.artifact]} if args.artifact is not None else {}),
                    **({"project_id": args.project_id} if args.project_id is not None else {})},
            preconditions={"repository_id": args.repository_id, "binding_digest": args.binding_digest,
                           **({"expected_project_version": args.project_version} if args.project_version is not None else {})},
            idempotency_key=key, wait_s=30, entry="cli", timeout=40)
        out = {**out, "idempotency_key": key}
    _print(out, True)
    return 1 if (out.get("operation") or {}).get("status") in {"failed", "cancelled"} else 0


def cmd_checkpoint(args) -> int:
    import uuid

    from .task_daemon import request

    def submit(action: str, target: dict, params: dict, pre=None) -> dict:
        key = args.key or f"cli-{uuid.uuid4()}"
        out = request("op_submit", action=action, idempotency_key=key, target=target, params=params, wait_s=30,
                      entry="cli", timeout=40.0, preconditions=pre)
        return {**out, "idempotency_key": key}

    cmd = args.checkpoint_cmd
    if cmd == "create":
        params = {"last_n": args.last_n, **({"commit": args.commit} if args.commit else {}),
                  **({"note": args.note} if args.note else {})}
        if args.artifact is not None:
            params["artifacts"] = [json.loads(ref) for ref in args.artifact]
        out = submit("checkpoint.create", {"host": args.host, "session_id": args.session_id}, params)
    elif cmd == "continue":
        text = Path(args.instructions_file).read_text() if args.instructions_file else args.instructions
        out = submit("checkpoint.continue", {"checkpoint_id": args.checkpoint_id},
                     {"instructions": text, "agent": args.agent,
                      **({"artifacts": [json.loads(ref) for ref in args.artifact]} if args.artifact is not None else {})},
                     {"expected_source_head_sha": args.source_head} if args.source_head else {})
    elif cmd == "revalidate":
        out = submit("checkpoint.continue.revalidate", {"operation_id": args.operation_id},
                     {"observed_source_head_sha": args.source_head}, {"expected_input_manifest_digest": args.manifest})
    elif cmd == "list":
        out = request("checkpoints_list", host=args.host, session_id=args.session, limit=args.limit, entry="cli")
    else:
        out = request("checkpoint_get", checkpoint_id=args.checkpoint_id, entry="cli", timeout=40.0)
    _print(out, True)
    return 0


def cmd_resource_cleanup(args) -> int:
    from . import cleanup
    if args.cleanup_cmd == "preview":
        kind, key, value = next((kind, key, getattr(args, flag)) for flag, kind, key in
            (("item", "work_item", "work_item_id"), ("checkpoint", "checkpoint", "checkpoint_id"),
             ("integration", "integration", "operation_id"), ("host", "host", "host"),
             ("task", "task", "task_id")) if getattr(args, flag))
        target = {"kind": kind, key: value}
        if args.include_children:
            target["include_children"] = True
        out = cleanup.http_request("/api/v1/cleanup-previews", body={"target": target, "choices":
            {"discard_uncommitted": args.discard_uncommitted, "release_undelivered": args.release_undelivered}})
    elif args.cleanup_cmd == "apply":
        if not args.confirm:
            raise ValueError("resource-cleanup apply requires --confirm")
        if args.preview_file:
            doc = json.loads(Path(args.preview_file).read_text())
            doc = doc.get("preview", doc)
        else:
            # The token carries its preview identity; the server verifies it, the adapter does not grant authority.
            import base64
            raw = (args.preview_token or "").split(".")
            if len(raw) != 3 or not args.fingerprint:
                raise ValueError("apply needs --preview-file or --preview-token and --fingerprint")
            payload = json.loads(base64.urlsafe_b64decode(raw[1] + "=" * (-len(raw[1]) % 4)))
            doc = {"preview_id": "clpv_" + cleanup._hash(payload)[:32], "preview_token": args.preview_token,
                   "fingerprint": args.fingerprint}
        out = cleanup.http_request("/api/v1/operations?wait=3", body=cleanup.apply_request(doc, args.key), key=args.key)
    else:
        filters = {"host": args.host, "query": args.query, "limit": args.limit, "cursor": args.cursor}
        if args.cleanup_cmd == "history":
            filters["original_id"] = args.original_id
        else:
            filters["resource_id"] = args.resource_id
        out = cleanup.http_request(cleanup.read_path("tombstones" if args.cleanup_cmd == "history" else "retained", **filters))
    _print(out, True)
    return 0


def integrate_sources(sources: list[str], picks: list[str]) -> list[dict]:
    out = []
    for s in sources:
        kind, sep, ident = s.partition(":")
        if not sep or not ident:
            raise ValueError(f"--source {s!r} must look like kind:id")
        out.append({"kind": kind, "id": ident})
    for p in picks:
        seq, sep, shas = p.partition("=")
        if not sep or not seq.isdigit() or not 1 <= int(seq) <= len(out):
            raise ValueError(f"--pick {p!r} must look like SEQ=SHA,SHA for one of the sources")
        out[int(seq) - 1].update(mode="pick", commits=[x for x in shas.split(",") if x])
    return out


def cmd_delivery(args) -> int:
    from .deployment import retry_envelope
    from .pr_delivery import merge_envelope
    from .task_daemon import request

    token = os.environ.get("BATC_API_TOKEN")
    if args.delivery_cmd == "pr":
        out = request("github_pr_preview", repository=args.repository, pull_number=args.number,
                      method=args.method, entry="cli", _auth_token=token or None)
    elif args.delivery_cmd == "preview":
        out = request("deployment_preview", recipe=args.recipe, entry="cli", _auth_token=token or None)
    elif args.delivery_cmd == "history":
        out = request("deployments_list", recipe=args.recipe, cursor=args.cursor, limit=args.limit,
                      entry="cli", _auth_token=token or None)
    elif args.delivery_cmd == "show":
        out = request("deployment_status", deployment_id=args.deployment_id, entry="cli", _auth_token=token or None)
    else:
        if not token:
            raise ValueError("delivery writes need this client's BATC_API_TOKEN (integrate, merge or deploy scope)")
        if args.delivery_cmd == "merge":
            doc = request("github_merge_preview_get", preview_id=args.preview, entry="cli", _auth_token=token)["preview"]
            envelope = merge_envelope(doc, recipe=args.recipe)
            if args.recipe:
                envelope["preconditions"].update(expected_environment_generation=args.generation,
                                                  expected_recipe_digest=args.recipe_digest)
        elif args.delivery_cmd in {"deploy", "rollback", "retry"}:
            pre = {"expected_environment_generation": args.generation, "expected_recipe_digest": args.recipe_digest}
            if args.delivery_cmd == "retry":
                saved = request("deployment_status", deployment_id=args.deployment_id, entry="cli", _auth_token=token)["deployment"]
                if saved["recipe"] != args.recipe:
                    raise ValueError("retry selects a deployment of this recipe")
                envelope = retry_envelope(saved, pre)
            else:
                envelope = {"action": "deployment.start" if args.delivery_cmd == "deploy" else "deployment.rollback",
                            "target": {"recipe": args.recipe}, "preconditions": pre,
                            "params": {"source_sha": args.sha} if args.delivery_cmd == "deploy" else {"deployment_id": args.deployment_id}}
        else:
            params = {"title": args.title} if args.title is not None else {}
            if args.body_file is not None:
                with Path(args.body_file).open(encoding="utf-8", newline="") as body_file:
                    params["body"] = body_file.read()
            envelope = {"action": "github.pr.update", "target": {"repository": args.repository,
                        "pull_number": args.number}, "params": params,
                        "preconditions": {"expected_metadata_digest": args.metadata_digest}}
        out = request("op_submit", **envelope, idempotency_key=args.key, wait_s=10, entry="cli",
                      timeout=40.0, _auth_token=token)
    _print(out, True)
    return 0


def cmd_integrate(args) -> int:
    import uuid

    from .integration import apply_request
    from .task_daemon import request

    cmd = args.integrate_cmd
    if cmd == "candidates":
        out = request("integration_candidates", host=args.host, entry="cli")
    elif cmd == "preview":
        key = args.key or f"cli-{uuid.uuid4()}"
        out = request("op_submit", action="integration.preview", idempotency_key=key,
                      target={"host": args.host, "repository": args.repo, "pull_number": args.pr},
                      params={"sources": integrate_sources(args.source, args.pick)}, wait_s=30, entry="cli",
                      timeout=40.0)
        out["note"] = "your local folders are never updated; sync them in BAT after the PR changes"
    elif cmd == "apply":
        doc = request("integration_preview_get", preview_id=args.preview_id, entry="cli")["preview"]
        out = request("op_submit", **apply_request(doc), wait_s=30, entry="cli", timeout=40.0)
    elif cmd == "handoff":
        note = Path(args.instructions_file).read_text() if args.instructions_file else ""
        out = request("op_submit", action="integration.handoff", idempotency_key=args.key or f"cli-{uuid.uuid4()}",
                      target={"operation_id": args.operation_id}, params={"agent": args.agent, "instructions": note},
                      wait_s=30, entry="cli", timeout=40.0)
        out["next"] = f"when the session has committed: batc op {args.operation_id} --resume"
    elif args.id.startswith("ipv_"):
        out = request("integration_preview_get", preview_id=args.id, entry="cli")
    else:
        out = request("integration_get", operation_id=args.id, entry="cli")
    _print(out, True)
    return 0


def _manage(action: str, target: dict, params: dict, pre: dict | None = None) -> dict:
    import uuid

    from .task_daemon import request

    return request("op_submit", action=action, idempotency_key=f"cli-{uuid.uuid4()}", target=target, params=params,
                   preconditions=pre or {}, wait_s=10, entry="cli", timeout=20.0)


def cmd_project(args) -> int:
    from .task_daemon import request

    cmd = args.project_cmd
    if cmd == "list":
        out = request("projects_list", include_archived=args.archived, entry="cli")
    elif cmd == "show":
        out = request("project_get", project_id=args.project_id, include_archived=args.archived, projection=args.projection, entry="cli")
    else:
        params = {k: v for k, v in (("description", args.description), ("parent_id", args.parent),
                                    ("repositories", args.repo), ("task_project", args.task_project))
                  if v is not None}
        if cmd == "create":
            out = _manage("project.create", {}, {"name": args.name, **params})
        else:
            current = request("project_get", project_id=args.project_id, entry="cli")["project"]
            if args.archive or args.restore:
                if params or args.name is not None:
                    raise ValueError("--archive and --restore cannot be combined with other changes")
                params = {"archived": bool(args.archive)}
            elif args.name is not None:
                params["name"] = args.name
            out = _manage("project.update", {"project_id": args.project_id}, params,
                          {"expected_version": current["version"]})
    _print(out, True)
    return 0


def cmd_item(args) -> int:
    from .task_daemon import request

    cmd = args.item_cmd
    if cmd == "list":
        out = request("work_items_list", project_id=args.project, state=args.state,
                      pending=True if args.pending else None, unread=True if args.unread else None,
                      limit=args.limit, cursor=args.cursor, entry="cli")
    elif cmd == "show":
        out = request("work_item_get", work_item_id=args.work_item_id, entry="cli")
    elif cmd in {"create", "update"}:
        params = {k: v for k, v in (("goal", args.goal), ("acceptance", args.acceptance), ("steps", args.step),
                                    ("parent_id", args.parent)) if v is not None}
        if args.attachment is not None:
            params["attachments"] = [json.loads(ref) for ref in args.attachment]
        if args.request_file:
            params["request"] = Path(args.request_file).read_text()
        if cmd == "create":
            if args.derived_from:
                params["derived_from"] = args.derived_from
            out = _manage("work_item.create", {"project_id": args.project_id}, {"title": args.title, **params})
        else:
            item = request("work_item_get", work_item_id=args.work_item_id, entry="cli")["work_item"]
            if args.archive or args.restore:
                if params or args.title is not None or args.state is not None or args.check or args.uncheck:
                    raise ValueError("--archive and --restore cannot be combined with other changes")
                params = {"archived": bool(args.archive)}
            else:
                if args.title is not None:
                    params["title"] = args.title
                if args.state is not None:
                    params["state"] = args.state
                if args.check or args.uncheck:
                    steps = [{"text": x, "done": False} if isinstance(x, str) else dict(x)
                             for x in params.get("steps", item["steps"])]
                    for n, done in [(n, True) for n in args.check] + [(n, False) for n in args.uncheck]:
                        if not 1 <= n <= len(steps):
                            raise ValueError(f"there is no step {n}")
                        steps[n - 1]["done"] = done
                    params["steps"] = steps
            out = _manage("work_item.update", {"work_item_id": args.work_item_id}, params,
                          {"expected_version": item["version"]})
    elif cmd in {"approve", "continue"}:
        fp = args.fingerprint
        if not fp:  # continue only sends the claim back; it may use the current content
            fp = request("work_item_get", work_item_id=args.work_item_id, entry="cli")["work_item"]["completion"][
                "fingerprint"]
        out = _manage(f"work_item.{cmd}", {"work_item_id": args.work_item_id},
                      {"note": args.note} if args.note else {}, {"expected_fingerprint": fp})
    else:
        params = {"kind": args.kind, "ref": args.ref, **({"note": args.note} if args.note else {}),
                  **({"remove": True} if args.remove else {})}
        out = _manage("work_item.link", {"work_item_id": args.work_item_id}, params)
    _print(out, True)
    return 0


def cmd_observation(args) -> int:
    from .task_daemon import request
    if args.cmd == "inventory":
        sub = args.inventory_cmd
        method = {"sessions": "inventory_sessions", "session": "inventory_session", "worktree": "inventory_worktree",
                  "hosts": "inventory_hosts", "discovery": "inventory_hosts", "events": "api_events"}[sub]
        params = {k: v for k, v in vars(args).items() if k not in {"cmd", "inventory_cmd", "config", "json", "read_only"} and v is not None}
        if sub == "discovery":
            params["discovery"] = True
    else:
        method = "resource_" + args.cmd
        params = {k: getattr(args, k) for k in ("cursor", "limit", "order", "kind", "since", "until", "execution_id", "include_closed") if hasattr(args, k) and getattr(args, k) is not None}
        params.update(resource_type=args.resource_type, resource_id=f"{args.host}/{args.session_id}" if args.resource_type == "session" else args.resource_id)
    for key in ("has_tab", "loaded", "streaming", "stale", "attention", "include_closed"):
        if key in params and isinstance(params[key], str):
            params[key] = params[key] == "true"
    _print(request(method, _auth_token=os.environ.get("BATC_API_TOKEN") or None, entry="cli", **params), True)
    return 0


def _mutation_requested(args) -> bool:
    """Classify before daemon calls or input-file reads, including commands that do not build a Fleet."""
    command = args.cmd
    if command in {"send", "continue", "interrupt", "answer", "permissions", "start", "merge",
                   "remove-worktree", "record-verification", "fanout-plan", "task-reconcile", "serve"}:
        return True
    if command in {"approve-pending", "relay", "failover", "fanout-start"}:
        return not args.dry_run
    if command == "fanout":
        return args.start
    if command == "cleanup":
        return args.apply
    if command == "op":
        return args.cancel or args.resume
    if command == "import-bat":
        return args.output != "-"
    mutating = {"artifact": ("artifact_cmd", {"upload", "capture", "managed-capture", "accept"}),
                "repository": ("repository_cmd", {"continue"}),
                "resource-cleanup": ("cleanup_cmd", {"apply"}),
                "checkpoint": ("checkpoint_cmd", {"create", "continue", "revalidate"}),
                "delivery": ("delivery_cmd", {"update-pr", "merge", "deploy", "rollback", "retry"}),
                "integrate": ("integrate_cmd", {"preview", "apply", "handoff"}),
                "project": ("project_cmd", {"create", "update"}),
                "item": ("item_cmd", {"create", "update", "approve", "continue", "link"}),
                "api-token": ("api_token_cmd", {"issue", "revoke"})}
    field, values = mutating.get(command, ("cmd", set()))
    return getattr(args, field) in values


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["mcp"]:
        from .mcp_server import main as mcp_main

        mcp_main(argv[1:])
        return 0
    args = build_parser().parse_args(argv)
    try:
        if args.read_only and _mutation_requested(args):
            raise WriteRefused("--read-only refuses this mutation")
        if args.cmd in {"inventory", "history", "relations"}:
            return cmd_observation(args)
        if args.cmd == "serve":
            from .task_daemon import TaskDaemon

            asyncio.run(TaskDaemon(load_config(args.config), args.db).serve(args.host, args.port))
            return 0
        if args.cmd == "task-events":
            from .task_daemon import request

            _print(request("work_events", since_cursor=args.since, limit=args.limit), args.json)
            return 0
        if args.cmd == "task-reconcile":
            from .task_daemon import request

            cap = request("work_reconcile_capability", task_id=args.task_id,
                          command_id=args.command_id,
                          **({"idempotency_key": args.key} if args.key is not None else {}))["capability"]
            next_prompt = Path(args.next_prompt_file).read_text() if args.next_prompt_file else None
            result = request("work_reconcile", _auth_token=cap, timeout=40, task_id=args.task_id,
                             command_id=args.command_id, outcome=args.outcome, actor=args.actor,
                             source=args.source, evidence=args.evidence,
                             observed_result=args.observed_result, turn_ref=args.turn_ref,
                             candidate_commit=args.candidate_commit, tree_hash=args.tree_hash,
                             next_prompt=next_prompt, entry="cli",
                             **({"idempotency_key": args.key} if args.key is not None else {}),
                             **({"control_version": args.control_version} if args.control_version is not None else {}))
            _print(result, args.json)
            return 0
        if args.cmd == "api-token":
            from .task_daemon import request

            calls = {
                "issue": lambda: request("api_token_issue", actor=args.actor, scopes=args.scope,
                                         ttl_days=args.ttl_days, label=args.label),
                "revoke": lambda: request("api_token_revoke", actor=args.actor),
                "list": lambda: request("api_token_list"),
            }
            out = calls[args.api_token_cmd]()
            _print(out, True)
            return 0
        if args.cmd == "artifact":
            return cmd_artifact(args)

        if args.cmd == "resource-cleanup":
            return cmd_resource_cleanup(args)
        if args.cmd == "repository":
            return cmd_repository(args)
        if args.cmd == "checkpoint":
            return cmd_checkpoint(args)
        if args.cmd == "delivery":
            return cmd_delivery(args)
        if args.cmd == "integrate":
            return cmd_integrate(args)
        if args.cmd == "project":
            return cmd_project(args)
        if args.cmd == "item":
            return cmd_item(args)
        if args.cmd == "op":
            from .task_daemon import request

            auth = {"_auth_token": os.environ.get("BATC_API_TOKEN") or None}
            if (args.cancel or args.resume) and not args.operation_id:
                raise ValueError("--cancel and --resume need an operation ID")
            if args.cancel:
                out = request("op_cancel", operation_id=args.operation_id, entry="cli", **auth)
            elif args.resume:
                out = request("op_resume", operation_id=args.operation_id, entry="cli", **auth)
            elif args.operation_id:
                out = request("op_get", operation_id=args.operation_id, entry="cli", **auth)
            else:
                out = request("op_list", statuses=args.status, limit=args.limit, entry="cli", **auth)
            _print(out, True)
            return 0
        if args.cmd == "import-bat":
            return cmd_import(args)
        if args.cmd == "config-path":
            print(args.config or default_config_path())
            return 0
        obj, render = asyncio.run(_run(args))
        _print(obj, args.json, render)
        return 1 if args.cmd in {"merge", "record-verification", "failover", "relay", "fanout-plan", "fanout-start", "fanout", "start", "send", "continue", "interrupt", "answer", "permissions"} and obj.get("operation_status") in {"failed", "cancelled"} else 0
    except (BatError, ValueError, OperationError) as e:
        print(f"error: {redact(e)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
