"""Configuration and pre-dispatch fallback for the experimental Goose PM.

No provider is allowed to replay a prompt whose submission is uncertain. Live
Goose is gated separately by the daemon's pinned ACP contract tests.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import platform_files

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib

from .task_journal import Journal

KINDS = frozenset({"agy-shim", "openai-compatible", "codex-acp", "claude-acp", "gemini"})
FALLBACK_ERRORS = frozenset({"quota_error", "rate_limited", "auth_error"})
AGY_CLAUDE_MODEL = "claude-opus-4-6-thinking"
CLAUDE_OPUS_55_MODEL = "claude-opus-5-5"
DEFAULT_ORDER = ("claude", "agy-claude", "codex")


@dataclass(frozen=True)
class ProviderEntry:
    id: str
    kind: str
    base_url: str | None = None
    model: str | None = None
    daily_cap: int = 0

    def __post_init__(self):
        if not self.id or self.kind not in KINDS or self.daily_cap < 0:
            raise ValueError("invalid PM provider entry")
        if (self.kind in {"agy-shim", "openai-compatible"}
                and (self.id == "agy-claude" or str(self.model or "").startswith("claude-"))
                and self.model != AGY_CLAUDE_MODEL):
            raise ValueError("AGY Claude model must be claude-opus-4-6-thinking")
        if self.id == "claude" and (self.kind != "claude-acp" or self.model != CLAUDE_OPUS_55_MODEL):
            raise ValueError("default Claude ACP provider must select claude-opus-5-5")
        if self.kind in {"agy-shim", "openai-compatible"} and self.base_url:
            parsed = urlsplit(self.base_url)
            if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("PM shim endpoint must be loopback HTTP without credentials")


class ProviderAdapter:
    def environment(self, entry: ProviderEntry, source: dict[str, str]) -> dict[str, str]:
        raise NotImplementedError


class AgyShimAdapter(ProviderAdapter):
    def environment(self, entry: ProviderEntry, source: dict[str, str]) -> dict[str, str]:
        if not entry.base_url or not entry.model:
            raise ProviderSetupError("agy shim endpoint/model unavailable")
        token = source.get("BATC_AGY_SHIM_TOKEN")
        if not token:
            raise ProviderSetupError("agy shim token unavailable")
        policy = ("omit_reasoning_effort" if entry.model.startswith("claude-")
                  else "pass_reasoning_effort")
        return {"GOOSE_PROVIDER": "openai", "GOOSE_MODEL": entry.model,
                "OPENAI_BASE_URL": entry.base_url, "OPENAI_API_KEY": token,
                # Consumed by compatible AGY shims; Claude thinking models
                # select thinking from the model id and reject --effort.
                "BATC_AGY_REQUEST_POLICY": policy}

    async def contract_probe(self, entry: ProviderEntry, source: dict[str, str]) -> bool:
        """Read a fake or local shim's model list; never submit a paid prompt."""
        env = self.environment(entry, source)

        def fetch():
            req = urllib.request.Request(  # noqa: S310 - entry URL validated loopback HTTP
                env["OPENAI_BASE_URL"].rstrip("/") + "/models",
                headers={"Authorization": "Bearer " + env["OPENAI_API_KEY"]},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=3) as response:  # noqa: S310 - loopback validated
                return json.load(response)

        result = await asyncio.to_thread(fetch)
        return (isinstance(result, dict) and isinstance(result.get("data"), list)
                and any(isinstance(row, dict) and row.get("id") == entry.model
                        for row in result["data"]))


class CodexACPAdapter(ProviderAdapter):
    def environment(self, entry: ProviderEntry, source: dict[str, str]) -> dict[str, str]:
        return {"GOOSE_PROVIDER": "chatgpt_codex", **({"GOOSE_MODEL": entry.model} if entry.model else {})}


class ClaudeACPAdapter(ProviderAdapter):
    def environment(self, entry: ProviderEntry, source: dict[str, str]) -> dict[str, str]:
        return {"GOOSE_PROVIDER": "claude-acp", **({"GOOSE_MODEL": entry.model} if entry.model else {})}


class GeminiAdapter(ProviderAdapter):
    def environment(self, entry: ProviderEntry, source: dict[str, str]) -> dict[str, str]:
        if not entry.model:
            raise ProviderSetupError("Gemini model unavailable")
        return {"GOOSE_PROVIDER": "gemini", "GOOSE_MODEL": entry.model}


class ProviderSetupError(RuntimeError):
    """Failure before session/prompt was submitted; fallback can be safe."""


class UncertainPrompt(RuntimeError):
    """A prompt may have reached a provider; never retry on another provider."""


ADAPTERS: dict[str, ProviderAdapter] = {
    "agy-shim": AgyShimAdapter(), "openai-compatible": AgyShimAdapter(),
    "codex-acp": CodexACPAdapter(), "claude-acp": ClaudeACPAdapter(),
    "gemini": GeminiAdapter(),
}


class ProviderCatalog:
    def __init__(self, entries: list[ProviderEntry] | None = None,
                 order: tuple[str, ...] = DEFAULT_ORDER):
        entries = entries or [
            ProviderEntry("claude", "claude-acp", model=CLAUDE_OPUS_55_MODEL),
            ProviderEntry("agy-claude", "agy-shim", os.environ.get("BATC_AGY_SHIM_BASE_URL"),
                          AGY_CLAUDE_MODEL, 10),
            ProviderEntry("codex", "codex-acp"),
            ProviderEntry("agy-gemini-flash", "agy-shim", os.environ.get("BATC_AGY_SHIM_BASE_URL"),
                          os.environ.get("BATC_AGY_GEMINI_MODEL")),
        ]
        self.entries = {e.id: e for e in entries}
        self.order = order
        if len(self.entries) != len(entries) or any(p not in self.entries for p in order):
            raise ValueError("invalid PM provider order")

    @classmethod
    def from_file(cls, path: str | Path) -> ProviderCatalog:
        p = Path(path).expanduser()
        if platform_files.WINDOWS:
            platform_files.check_private(p)
        elif stat.S_IMODE(p.stat().st_mode) & 0o077:
            raise ValueError("PM provider config must be mode 0600")
        raw = tomllib.loads(platform_files.read_private(p).decode() if platform_files.WINDOWS else p.read_text())
        entries = [ProviderEntry(**e) for e in raw.get("providers", [])]
        return cls(entries, tuple(raw.get("fallback_order", DEFAULT_ORDER)))

    def entry(self, provider_id: str) -> ProviderEntry:
        return self.entries[provider_id]

    def environment(self, provider_id: str, source: dict[str, str]) -> dict[str, str]:
        entry = self.entry(provider_id)
        return ADAPTERS[entry.kind].environment(entry, source)

    def available(self, journal: Journal, provider_id: str) -> bool:
        entry = self.entry(provider_id)
        since = int(time.time() // 86400) * 86400
        return (not journal.provider_unavailable(provider_id, since=since)
                and (not entry.daily_cap or journal.provider_daily_count(provider_id, since=since) < entry.daily_cap))

    def next(self, journal: Journal, current: str | None = None) -> str | None:
        order = self.order
        start = order.index(current) + 1 if current in order else 0
        return next((p for p in order[start:] if self.available(journal, p)), None)


def classify_provider_error(*, status: int | None = None, code: str | None = None) -> str | None:
    """Classify structured provider errors only; never guess from exception text."""
    # The provider's recognized code is more specific than an HTTP envelope:
    # exhausted quota can also arrive as 429, without being a transient throttle.
    code = code if isinstance(code, str) else None
    if code in {"insufficient_quota", "quota_exceeded"}:
        return "quota_error"
    if code in {"invalid_api_key", "unauthorized"}:
        return "auth_error"
    if code in {"rate_limit_exceeded", "rate_limited"}:
        return "rate_limited"
    if type(status) is int and status in {401, 403}:
        return "auth_error"
    if type(status) is int and status == 429:
        return "rate_limited"
    return None


class ProviderSwitcher:
    def __init__(self, journal: Journal, catalog: ProviderCatalog):
        self.journal = journal
        self.catalog = catalog

    def initial(self, task_id: str, requested: str | None = None) -> str:
        if requested and requested not in self.catalog.entries:
            raise ValueError("unknown PM provider")
        provider = requested or self.catalog.next(self.journal)
        if provider and not self.catalog.available(self.journal, provider):
            provider = self.catalog.next(self.journal, provider)
        if not provider:
            raise ProviderSetupError("all configured PM providers exhausted")
        self.journal.add_branch(task_id, session_id=None, provider=provider, role="pm", reason="initial")
        return provider

    def fallback(self, task_id: str, current: str, *, outcome: str,
                 prompt_status: str) -> str | None:
        if prompt_status in {"needs_review", "uncertain", "accepted", "running", "settled"}:
            raise UncertainPrompt("reconcile submitted prompt before provider switch")
        if outcome not in FALLBACK_ERRORS:
            return None
        self.journal.provider_use(current, outcome)
        successor = self.catalog.next(self.journal, current)
        if successor:
            parent = self.journal.branches(task_id)[-1]["branch_id"]
            self.journal.add_branch(task_id, session_id=None, provider=successor, role="pm",
                                    reason=outcome, parent_branch_id=parent)
        return successor
