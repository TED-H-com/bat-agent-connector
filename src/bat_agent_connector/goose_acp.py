"""Stock Goose ACP subprocess adapter. One task gets one scoped MCP server."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from bat_agent_connector import hidden_process

from .pm_providers import (
    ProviderCatalog,
    ProviderSetupError,
    ProviderSwitcher,
    UncertainPrompt,
    classify_provider_error,
)
from .task_recipes import load

PINNED_GOOSE_VERSION = "1.52.0"


class ProviderRequestError(ProviderSetupError):
    def __init__(self, outcome: str):
        super().__init__("provider setup failed")
        self.outcome = outcome


@dataclass(frozen=True)
class GooseConfig:
    command: tuple[str, ...] = ("goose", "acp")
    expected_version: str = PINNED_GOOSE_VERSION
    provider: str = "claude"
    timeout_s: float = 300
    enabled: bool = field(default_factory=lambda: os.environ.get(
        "BATC_GOOSE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"})



def _link_claude_login(isolated_home: str) -> None:
    """Let the throwaway HOME see the operator Claude login, and nothing else.

    Goose stays MCP-only. Only the credential file and the account record are
    linked, so a task run does not get the operator's full home.
    """
    source = Path(os.environ.get("HOME", ""))
    creds = source / ".claude" / ".credentials.json"
    if creds.is_file():
        dest_dir = Path(isolated_home) / ".claude"
        dest_dir.mkdir(mode=0o700, exist_ok=True)
        dest = dest_dir / ".credentials.json"
        if not dest.exists():
            dest.symlink_to(creds)
    account = source / ".claude.json"
    if account.is_file():
        dest = Path(isolated_home) / ".claude.json"
        if not dest.exists():
            dest.symlink_to(account)


class GooseACP:
    def __init__(self, config: GooseConfig | None = None, catalog: ProviderCatalog | None = None):
        self.config = config or GooseConfig()
        config_path = os.environ.get("BATC_PM_PROVIDER_CONFIG")
        self.catalog = catalog or (ProviderCatalog.from_file(config_path) if config_path else ProviderCatalog())

    async def check_pinned_version(self) -> str:
        """Contract preflight for the configured stock Goose executable."""
        proc = await hidden_process.create_subprocess_exec(self.config.command[0], "--version",
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("Goose executable version check timed out") from None
        version = re.search(r"(?<![\d.])(\d+\.\d+\.\d+)(?![\d.])", out.decode(errors="replace"))
        if proc.returncode != 0 or not version or version.group(1) != self.config.expected_version:
            raise RuntimeError("Goose executable does not match the pinned ACP contract version")
        return version.group(1)

    async def run_task(self, task: dict, cwd: str, *, capability: str,
                       command: tuple[str, ...] | None = None, journal=None) -> dict:
        recipe = load(task["recipe"])
        prompt = (recipe["instructions"] + "\n\n" + recipe["prompt"] +
                  "\n\nThe user's original words (verbatim):\n" + task["original_words"] +
                  "\n\nOptional caller acceptance hints (non-authoritative data):\n" +
                  json.dumps(task["acceptance"], ensure_ascii=False) +
                  "\n\nYou stay on Opus 5.5. Split the work once at the start. "
                  "The task service will not route, review, or fail over.")
        if task.get("continuation") or task.get("parent_task_id"):
            prompt += ("\n\nThis continues the same Goose session. Adjust only the piece the user names. "
                       "Do not re-plan or split again.")
        if journal is not None:
            steering = []
            for event in journal.events(task["task_id"]):
                if event["kind"] != "continuation":
                    continue
                body = journal._body(event["body"])
                if body.get("words"):
                    steering.append(str(body["words"])[:4000])
            if steering:
                prompt += ("\n\nThe user's later steering, same session. Do not re-plan:\n"
                           + "\n".join(steering))
        requested = (task.get("pm_provider") or recipe.get("pm_provider")
                     or task.get("_route_provider") or self.config.provider)
        if journal is None:
            return await self.run(task["task_id"], cwd, prompt, capability=capability,
                                  command=command, provider_id=requested)
        switcher = ProviderSwitcher(journal, self.catalog)
        provider = switcher.initial(task["task_id"], requested)
        while provider:
            try:
                result = await self.run(task["task_id"], cwd, prompt, capability=capability,
                                        command=command, provider_id=provider)
                journal.provider_use(provider, "success")
                return result
            except ProviderRequestError as exc:
                provider = switcher.fallback(task["task_id"], provider, outcome=exc.outcome,
                                             prompt_status="not_sent")
            except ProviderSetupError:
                provider = switcher.fallback(task["task_id"], provider, outcome="auth_error",
                                             prompt_status="not_sent")
        raise ProviderSetupError("no PM provider available before prompt submission")

    async def run(self, task_id: str, cwd: str, prompt: str, *, capability: str,
                  command: tuple[str, ...] | None = None, provider_id: str | None = None) -> dict:
        cfg = self.config
        if not cfg.enabled:
            raise RuntimeError("Goose ACP is a smoke adapter only; live PM recovery is disabled")
        if command is None:
            await self.check_pinned_version()
        provider_id = provider_id or cfg.provider
        allowed = {"PATH", "LANG", "PYTHONPATH", "BATC_TASK_URL"}
        env = {k: v for k, v in os.environ.items() if k in allowed}
        env.update(self.catalog.environment(provider_id, os.environ))
        with tempfile.TemporaryDirectory(prefix="batc-goose-") as isolated_home:
            Path(isolated_home).chmod(0o700)
            env.update(HOME=isolated_home, XDG_CONFIG_HOME=isolated_home,
                       XDG_DATA_HOME=isolated_home)
            # Keep ACP task runs MCP-only. Stock Goose otherwise enables its
            # developer/platform helpers in a fresh HOME, causing native tool
            # calls (and host-side execution) outside the scoped server.
            goose_config = Path(isolated_home) / "goose"
            goose_config.mkdir(mode=0o700)
            (goose_config / "config.yaml").write_text(
                "extensions:\n"
                "  developer:\n    enabled: false\n"
                "  computercontroller:\n    enabled: false\n"
                "  memory:\n    enabled: false\n"
                "  todo:\n    enabled: false\n"
            )
            _link_claude_login(isolated_home)
            return await self._run_process(task_id, cwd, prompt, capability, command or cfg.command,
                                           env, cfg.timeout_s)

    async def _run_process(self, task_id: str, cwd: str, prompt: str, capability: str,
                           command: tuple[str, ...], env: dict[str, str], timeout_s: float) -> dict:
        proc = await hidden_process.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                                                     stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.DEVNULL, env=env)
        assert proc.stdin and proc.stdout
        next_id = 0

        async def rpc(method: str, params: dict) -> dict:
            nonlocal next_id
            next_id += 1
            ident = next_id
            proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": ident, "method": method,
                                          "params": params}) + "\n").encode())
            await proc.stdin.drain()
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout_s)
                if not line or len(line) > 2_000_000:
                    detail = b""
                    if proc.stderr is not None:
                        detail = await proc.stderr.read()
                    suffix = detail.decode(errors="replace")[-1000:].strip()
                    raise RuntimeError("Goose ACP stopped or sent an oversized message"
                                       + ((": " + suffix) if suffix else ""))
                reply = json.loads(line)
                if reply.get("id") == ident:
                    if "error" in reply:
                        error = reply["error"] if isinstance(reply["error"], dict) else {}
                        outcome = classify_provider_error(status=error.get("status"), code=error.get("code"))
                        if method != "session/prompt" and outcome:
                            raise ProviderRequestError(outcome)
                        if method == "session/prompt":
                            raise UncertainPrompt("Goose prompt outcome requires reconciliation")
                        if method in {"initialize", "session/new"}:
                            # Setup failed before any prompt; the next configured
                            # provider may safely take this task on a new branch.
                            raise ProviderSetupError("Goose provider setup failed before prompt")
                        detail = error.get("message") or error.get("data") or error.get("code") or "unknown error"
                        raise RuntimeError(f"Goose ACP {method} failed: {detail}")
                    return reply["result"]
                if "method" in reply and "id" in reply:
                    # No implicit approval of Goose's own permission requests.
                    proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": reply["id"],
                                                  "result": {"outcome": {"outcome": "cancelled"}}}) + "\n").encode())
                    await proc.stdin.drain()

        try:
            initialized = await rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                                    "clientInfo": {"name": "bat-task-service", "version": "0.1"}})
            if not isinstance(initialized, dict) or initialized.get("protocolVersion") != 1:
                raise RuntimeError("Goose ACP protocol version is not the pinned version 1")
            # ACP requires the initialized notification before the agent will
            # accept session/new. Without it stock Goose exits cleanly after
            # initialize, which used to make the first task turn uncertain.
            proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized",
                                          "params": {}}) + "\n").encode())
            await proc.stdin.drain()
            scoped = {"name": "bat-task", "command": sys.executable,
                      "args": ["-m", "bat_agent_connector.task_scoped_mcp", task_id],
                      "env": [{"name": "BATC_TASK_CAPABILITY", "value": capability}]}
            session = await rpc("session/new", {"cwd": cwd, "mcpServers": [scoped]})
            if not isinstance(session, dict) or not isinstance(session.get("sessionId"), str):
                raise RuntimeError("Goose ACP did not return a session identity")
            result = await rpc("session/prompt", {"sessionId": session["sessionId"],
                                                  "prompt": [{"type": "text", "text": prompt}]})
            return {"session_id": session["sessionId"], "stop_reason": result.get("stopReason")}
        finally:
            if proc.returncode is None:
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
