"""Configuration: host list (TOML), token references, client identity, paths.

Config file (default ``~/.config/bat-agent-connector/hosts.toml``)::

    [client]
    label = "BAT Agent Connector"      # shown on the host's remote-client list
    connect_timeout_s = 10             # each key also accepts per-host overrides; finite 1–600 s
    invoke_timeout_s = 60              # optional minimum; omitted uses channel deadlines
    reconnect_grace_s = 120            # subscriptions survive transient recovery failures

    [safety]
    audit_preview_chars = 0            # 0 = log only sha256 prefix + length of prompts
    write_min_interval_s = 60          # per (host, session)
    max_writes_per_hour = 30           # across all hosts

    [bat]
    profiles_dir = "~/.local/share/org.tonyq.better-agent-terminal/profiles"

    [hosts.myhost]
    url = "wss://127.0.0.1:9876/"
    fingerprint = "AA:BB:...:FF"       # SHA-256 of the server cert (DER)
    token_ref = "env:BAT_TOKEN_MYHOST" # or "file:/path/to/token" or "bat-profile:<profile id>"
    writes = false                     # write tools stay unavailable unless true
    orchestrate = false                # 3rd tier: start worktree sessions, merge, remove (needs writes)
    orchestrate_max_sessions = 4       # cap on concurrently orchestrated sessions on this host
    orchestrate_register_tabs = false  # append a tab to the host workspace (workspace:save, append-only)
    default_permission_mode = "default" # default/allow_all preserve BAT policy; confined gates tools/sandboxes
    # confined Codex may block installs/network/local sockets; BAT cannot pass network or writable roots.
    auto_cleanup = false               # deprecated: parses only; never enables cleanup writes
    codex_model = ""                   # default model for Codex sessions started/failed over here ("" = BAT default)
    profile_id = "default"             # workspace profile on the host
    managed_roots = []                 # host folders the connector owns (its own clones); see resource_policy.py
    shared_clone_worktrees = true      # legacy: new worktrees may live in a human checkout's .bat-worktrees

    [jev]                              # optional judgment layer (TypeSafe Jev); off without an API key
    enabled = "auto"                   # on when TYPESAFE_API_KEY or OPENROUTER_API_KEY is set
    timeout_s = 3.0

Token values never live in this file.
"""

from __future__ import annotations

import ipaddress
import os
import posixpath
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ._private_files import read_private
from .connection_sources import (
    DEFAULT_BAT_PROFILES_DIR as DEFAULT_BAT_PROFILES_DIR,
)
from .connection_sources import SourceUnavailable, TokenMode, read_token_store, select_profiles_directory
from .errors import ConfigError, TokenUnavailable
from .redact import register_secret
from .session_history_config import HistoryConfig, parse_history
from .usage_config import UsageConfig, parse_usage

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

APP = "bat-agent-connector"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_FP_RE = re.compile(r"^[0-9A-F]{64}$")


def config_dir() -> Path:
    if os.environ.get("BATC_CONFIG_DIR"):
        return Path(os.environ["BATC_CONFIG_DIR"]).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / APP


def state_dir() -> Path:
    if os.environ.get("BATC_STATE_DIR"):
        return Path(os.environ["BATC_STATE_DIR"]).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(base).expanduser() / APP


def default_journal_path() -> Path:
    """Journal used by a local task daemon without an explicit database path."""
    return state_dir() / "tasks.sqlite3"


def default_config_path() -> Path:
    if os.environ.get("BATC_CONFIG"):
        return Path(os.environ["BATC_CONFIG"]).expanduser()
    return config_dir() / "hosts.toml"


def normalize_fingerprint(fp: str) -> str:
    s = fp.strip().upper()
    if s.startswith("SHA256:"):
        s = s[7:]
    s = s.replace(":", "").replace(" ", "")
    if not _FP_RE.match(s):
        raise ConfigError("fingerprint must be a SHA-256 hex digest (64 hex chars, colons optional)")
    return s


def format_fingerprint(hex64: str) -> str:
    return ":".join(hex64[i : i + 2] for i in range(0, len(hex64), 2))


class GitTransportConfigError(ConfigError):
    code = "HOST_GIT_INVALID"


def git_transport(value: object) -> str:
    if type(value) is not str or value not in ("ssh", "local"):
        raise GitTransportConfigError("host git must be exactly ssh or local")
    return value


CLIENT_TIMEOUTS = {"connect_timeout_s": 10.0, "invoke_timeout_s": None, "reconnect_grace_s": 120.0}


def client_timeouts(values: dict) -> dict[str, float | None]:
    result = {}
    for key, default in CLIENT_TIMEOUTS.items():
        value = values.get(key, default)
        if key not in values and value is None:
            result[key] = None
            continue
        if type(value) not in (int, float) or not 1 <= value <= 600:
            raise ConfigError(f"{key} must be a finite number between 1 and 600 seconds")
        result[key] = float(value)
    return result


@dataclass
class HostConfig:
    name: str
    url: str
    fingerprint: str  # normalized 64-hex
    token_ref: str
    writes: bool = False
    orchestrate: bool = False
    orchestrate_max_sessions: int = 4
    orchestrate_register_tabs: bool = False
    default_permission_mode: str = "default"
    auto_cleanup: bool = False  # Deprecated: parses for compatibility; never enables cleanup writes.
    codex_model: str | None = None
    profile_id: str = "default"
    bat_profiles_dir: str = ""  # Empty selects the current platform's BAT source at resolution time.
    labels: list[str] = field(default_factory=list)
    managed_roots: tuple[str, ...] = ()
    shared_clone_worktrees: bool = True
    confinement: dict = field(default_factory=dict)
    connect_timeout_s: float = 10.0
    invoke_timeout_s: float | None = None
    reconnect_grace_s: float = 120.0
    git: str = "ssh"

    def __post_init__(self) -> None:
        git_transport(self.git)
        client_timeouts({key: getattr(self, key) for key in CLIENT_TIMEOUTS
                         if key != "invoke_timeout_s" or self.invoke_timeout_s is not None})

    def __repr__(self) -> str:  # never include token material
        return f"HostConfig(name={self.name!r}, url={self.url!r}, writes={self.writes}, orchestrate={self.orchestrate})"

    @property
    def token_kind(self) -> str:
        return self.token_ref.split(":", 1)[0] if ":" in self.token_ref else "invalid"

    def token_available(self) -> bool:
        """Readiness of this configured reference uses the actual runtime resolver."""
        try:
            self.resolve_token()
            return True
        except TokenUnavailable:
            return False

    def resolve_token(self) -> str:
        tok = _resolve_token_ref(self.token_ref, self.bat_profiles_dir)
        register_secret(tok)
        return tok


def _resolve_token_ref(ref: str, profiles_dir: str) -> str:
    kind, _, arg = ref.partition(":")
    arg = arg.strip()
    if not arg:
        raise TokenUnavailable("token_ref must look like env:NAME, file:PATH or bat-profile:ID")
    if kind == "env":
        val = os.environ.get(arg, "").strip()
        if not val:
            raise TokenUnavailable(f"environment variable {arg} is not set")
        return val
    if kind == "file":
        p = Path(arg).expanduser()
        try:
            val = read_private(p, 64 * 1024).strip()
        except (OSError, ValueError):
            raise TokenUnavailable("cannot read token file: validate private file permissions, type and size") from None
        if not val:
            raise TokenUnavailable(f"token file {p} is empty")
        return val
    if kind == "bat-profile":
        return _token_from_bat_profiles(profiles_dir, arg)
    raise TokenUnavailable(f"unknown token_ref kind {kind!r}")


def _bat_token_unavailable(reason: str) -> TokenUnavailable:
    """Stable policy code plus actionable, content-free runtime guidance."""
    messages = {
        "TOKEN_STORE_ENCRYPTED": "BAT token store is encrypted (OS keychain); use token_ref env: or file: instead",
        "TOKEN_STORE_UNREADABLE": "cannot read BAT token store: validate private file permissions, type, size and JSON format",
        "TOKEN_MISSING": "no token for BAT profile; configure its token in BAT or use token_ref env: or file: instead",
        "DATA_DIRECTORY_UNAVAILABLE": "BAT profiles directory is unavailable; set APPDATA for this account on Windows or configure profiles_dir",
        "DATA_DIRECTORY_INVALID": "BAT profiles directory is invalid; configure profiles_dir to point to a directory",
        "DATA_DIRECTORY_UNREADABLE": "cannot read BAT profiles directory; check directory permissions or configure profiles_dir",
    }
    message = messages.get(reason, "BAT token store has an unexpected format")
    return TokenUnavailable(f"{reason}: {message}")


def _token_from_bat_profiles(pdir: str | Path | None, profile_id: str) -> str:
    try:
        directory = select_profiles_directory(
            pdir, platform=sys.platform, environment=os.environ, home=Path.home())
    except SourceUnavailable as error:
        raise _bat_token_unavailable(str(error)) from None
    result = read_token_store(directory, mode=TokenMode.LEGACY_RUNTIME).lookup(profile_id)
    if result.token is None:
        raise _bat_token_unavailable(result.reason)
    return result.token


@dataclass
class SafetyConfig:
    audit_preview_chars: int = 0
    write_min_interval_s: float = 60.0
    max_writes_per_hour: int = 30
    max_start_per_call: int = 4


PERMISSION_MODES = ("default", "allow_all", "confined")


def normalize_host_path(raw: str) -> str:
    """A host-side absolute POSIX path in canonical text form (no symlink resolution; that needs the host)."""
    path = str(raw or "").strip()
    if not path.startswith("/") or "\0" in path or ".." in path.split("/"):
        raise ConfigError(f"host path {raw!r} must be absolute and must not contain '..'")
    path = posixpath.normpath(path)
    return "/" + path.lstrip("/") if path != "/" else "/"


def _managed_roots(name: str, raw) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ConfigError(f"host {name!r}: managed_roots must be a list of absolute paths")
    roots = tuple(dict.fromkeys(normalize_host_path(x) for x in raw))
    if "/" in roots:
        raise ConfigError(f"host {name!r}: managed_roots must not contain '/'")
    return roots


@dataclass
class JevConfig:
    enabled: str = "auto"  # auto | true | false
    timeout_s: float = 3.0
    model: str = "jev-latest"
    base_url: str = "https://api.typesafe.ai"
    api_key_env: str = "TYPESAFE_API_KEY"


@dataclass
class ApiConfig:
    """[api]: the task daemon's /api/v1 (inventory refresh and browser origins)."""

    inventory_interval_s: float = 60.0
    stale_after_s: float = 180.0
    activity_every: int = 5
    allowed_origins: tuple[str, ...] = ()


@dataclass
class CleanupConfig:
    retained_refs: str = "keep"
    history_retention: str = "forever"
    permanent_delete: bool = False


_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
MERGE_METHODS = ("merge", "squash", "rebase")
_INPUT_FIELDS = ("source_sha", "operation_id", "environment", "repository", "environment_generation",
                 "artifact_id", "artifact_digest")


# Never a PR head that integration pushes to; the PR's base and the default branch are refused as well.
PROTECTED_REFS = ("main", "master", "release/*", "releases/*", "production", "staging")


@dataclass(frozen=True)
class IntegrateConfig:
    """``integrate = {...}`` on a [[github.repos]] entry: which hosts may push composed results to its PR heads,
    and the exact URL they push to (with that host's own git credentials). Design: docs/design/integration.md."""

    hosts: tuple[str, ...]
    remote_url: str
    protected_refs: tuple[str, ...] = PROTECTED_REFS
    fetch_timeout_s: float = 1800.0
    workspace: str | None = None  # BAT workspace for a conflict-resolving session when its source has none


@dataclass(frozen=True)
class RepositorySyncConfig:
    remote_url: str
    bindings: tuple[tuple[str, str], ...]
    fetch_timeout_s: float = 1800.0


@dataclass(frozen=True)
class GitHubRepo:
    repository: str  # owner/name
    allow_merge: bool = True
    merge_methods: tuple[str, ...] = MERGE_METHODS
    default_merge_method: str = "squash"
    integrate: IntegrateConfig | None = None
    allow_pr_update: bool = False
    sync: RepositorySyncConfig | None = None


@dataclass(frozen=True)
class DeployVerification:
    kind: str
    url: str
    version_required: bool = True
    health_required: bool = False
    timeout_s: float = 10.0
    max_bytes: int = 65536
    token_ref: str | None = None


@dataclass(frozen=True)
class DeployOrdering:
    mode: str = "serialized"
    concurrency_group: str = ""
    cancel_in_progress: bool = False


@dataclass(frozen=True)
class DeployRollback:
    supported: bool = False
    identity: str = "source_sha"
    not_undone: tuple[str, ...] = ()


def verification_url(url: str) -> str:
    from urllib.parse import urlsplit
    if not isinstance(url, str):
        raise ConfigError("verification.url must be a URL string")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        _ = parsed.port
    except ValueError:
        raise ConfigError("verification.url is invalid") from None
    loopback = host == "localhost"
    try:
        loopback = loopback or bool(host and ipaddress.ip_address(host).is_loopback)
    except ValueError:
        pass
    if (not host or parsed.username or parsed.password or parsed.fragment
            or any(c.isspace() for c in url)
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))):
        raise ConfigError("verification.url must be HTTPS (HTTP only on loopback), without credentials or fragment")
    return url


def _deploy_verification(raw) -> DeployVerification | None:
    if raw is None:
        return None
    allowed = {"kind", "url", "version_required", "health_required", "timeout_s", "max_bytes", "token_ref"}
    if not isinstance(raw, dict) or set(raw) - allowed or raw.get("kind") != "http_json":
        raise ConfigError("verification must declare kind=http_json and supported settings only")
    url = verification_url(raw.get("url", ""))
    version, health = raw.get("version_required", True), raw.get("health_required", False)
    if not isinstance(version, bool) or not isinstance(health, bool) or not (version or health):
        raise ConfigError("verification requires version_required and/or health_required")
    try:
        timeout, size = float(raw.get("timeout_s", 10)), int(raw.get("max_bytes", 65536))
    except (ValueError, TypeError):
        raise ConfigError("verification timeout_s/max_bytes must be numbers") from None
    if not 2 <= timeout <= 60 or not 1 <= size <= 1048576:
        raise ConfigError("verification timeout_s must be 2-60 and max_bytes 1-1048576")
    token = raw.get("token_ref")
    if token is not None and (not isinstance(token, str) or not re.fullmatch(r"(?:env|file):.+", token)):
        raise ConfigError("verification.token_ref must be env:NAME or file:PATH")
    return DeployVerification("http_json", url, version, health, timeout, size, token)


@dataclass(frozen=True)
class DeployRecipe:
    """One environment's deploy route. The Dashboard names a recipe; it never passes workflows or inputs."""

    name: str
    repository: str
    environment: str
    mode: str  # "workflow_dispatch" (connector starts it) or "on_merge" (the push already started it)
    workflow: str  # workflow file name, e.g. deploy.yml
    deploy_job: str  # this job must conclude success; skipped is not deployed
    ref: str = "main"  # branch whose workflow file runs (workflow_dispatch)
    inputs: tuple[tuple[str, str], ...] = ()  # workflow input -> one of _INPUT_FIELDS
    run_name_contains: str | None = None  # "operation_id": the workflow's run-name carries it
    ordering: DeployOrdering = field(default_factory=DeployOrdering)
    verification: DeployVerification | None = None
    rollback: DeployRollback = field(default_factory=DeployRollback)


@dataclass
class GitHubConfig:
    token_ref: str | None = None
    api_url: str = "https://api.github.com"
    api_version: str = "2026-03-10"
    timeout_s: float = 20.0
    wait_max_s: float = 3600.0
    deployment_reconcile_interval_s: float = 300.0
    repos: dict[str, GitHubRepo] = field(default_factory=dict)
    recipes: dict[str, DeployRecipe] = field(default_factory=dict)

    def token(self) -> str:
        if not self.token_ref:
            raise TokenUnavailable("[github] token_ref is not configured")
        tok = _resolve_token_ref(self.token_ref, "")
        register_secret(tok)
        return tok


def _integrate_url(url, repository: str, api_url: str) -> str:
    """The push URL must name this repository on this GitHub, with no credentials or tricks in it."""
    # ASCII only: case-insensitive matching would otherwise fold lookalikes (U+0131 "ı" matches "i").
    if not isinstance(url, str) or not url or not url.isascii() or url != url.strip() or url.startswith("-") or any(
            c.isspace() or c in "?#" for c in url):
        raise ConfigError(f"[[github.repos]] {repository}: integrate.remote_url is not a plain URL")
    api_host = re.sub(r"^https?://", "", api_url).split("/", 1)[0].split(":", 1)[0]
    if api_host in {"127.0.0.1", "localhost"} and (url.startswith("/") or url.startswith("file:///")):
        return url  # a local bare repository stands in for GitHub in tests
    gh = "github.com" if api_host == "api.github.com" else api_host
    owner, name = (re.escape(p) for p in repository.split("/", 1))
    tail = rf"{owner}/{name}(\.git)?"
    forms = (rf"git@{re.escape(gh)}:{tail}", rf"ssh://git@{re.escape(gh)}/{tail}", rf"https://{re.escape(gh)}/{tail}")
    if not any(re.fullmatch(f, url, re.IGNORECASE) for f in forms):
        raise ConfigError(f"[[github.repos]] {repository}: integrate.remote_url must be git@{gh}:{repository}.git, "
                          f"ssh://git@{gh}/{repository}.git or https://{gh}/{repository}.git (no token in it)")
    return url


def _integrate(r: dict, repository: str, api_url: str, host_names: set[str]) -> IntegrateConfig | None:
    raw = r.get("integrate")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"[[github.repos]] {repository}: integrate must be a table")
    hosts = raw.get("hosts")
    if not isinstance(hosts, list) or not hosts or any(h not in host_names for h in hosts):
        raise ConfigError(f"[[github.repos]] {repository}: integrate.hosts must list configured hosts")
    protected = raw.get("protected_refs", list(PROTECTED_REFS))
    if not isinstance(protected, list) or any(not isinstance(p, str) or not p for p in protected):
        raise ConfigError(f"[[github.repos]] {repository}: integrate.protected_refs must be a list of globs")
    try:
        timeout = float(raw.get("fetch_timeout_s", 1800))
    except (TypeError, ValueError):
        timeout = -1.0
    if not 60 <= timeout <= 7200:
        raise ConfigError(f"[[github.repos]] {repository}: integrate.fetch_timeout_s must be 60-7200")
    workspace = raw.get("workspace")
    if workspace is not None and (not isinstance(workspace, str) or not workspace.strip()):
        raise ConfigError(f"[[github.repos]] {repository}: integrate.workspace must be a BAT workspace name or id")
    return IntegrateConfig(tuple(dict.fromkeys(str(h) for h in hosts)),
                           _integrate_url(raw.get("remote_url"), repository, api_url), tuple(protected), timeout,
                           workspace)


def _repository_sync(r, repository, api_url, host_names):
    raw = r.get("sync")
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"remote_url", "bindings", "fetch_timeout_s"}:
        raise ConfigError(f"[[github.repos]] {repository}: invalid sync table")
    bindings = raw.get("bindings")
    if not isinstance(bindings, list) or not bindings or len(bindings) > 200:
        raise ConfigError(f"[[github.repos]] {repository}: sync.bindings must contain 1-200 exact host/workspace IDs")
    out = []
    for b in bindings:
        if (not isinstance(b, dict) or set(b) != {"host", "workspace_id"}
                or not isinstance(b["host"], str) or b["host"] not in host_names
                or not isinstance(b["workspace_id"], str) or not 1 <= len(b["workspace_id"]) <= 256
                or b["workspace_id"] != b["workspace_id"].strip() or any(ord(c) < 32 for c in b["workspace_id"])):
            raise ConfigError(f"[[github.repos]] {repository}: invalid exact sync binding")
        pair = b["host"], b["workspace_id"]
        if pair in out:
            raise ConfigError(f"[[github.repos]] {repository}: repeated sync binding")
        out.append(pair)
    timeout = raw.get("fetch_timeout_s", 1800)
    if type(timeout) not in (int, float) or not 60 <= timeout <= 7200:
        raise ConfigError(f"[[github.repos]] {repository}: sync.fetch_timeout_s must be 60-7200")
    return RepositorySyncConfig(_integrate_url(raw.get("remote_url"), repository, api_url), tuple(out), float(timeout))


def parse_github(data: dict) -> GitHubConfig:
    g = data.get("github") or {}
    interval = g.get("deployment_reconcile_interval_s", 300)
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 60 <= interval <= 86400:
        raise ConfigError("[github] deployment_reconcile_interval_s must be between 60 and 86400 seconds")
    api_url = str(g.get("api_url") or "https://api.github.com").rstrip("/")
    host = re.sub(r"^https?://", "", api_url).split("/", 1)[0].split(":", 1)[0]
    if not (api_url.startswith("https://") or (api_url.startswith("http://")
                                               and host in {"127.0.0.1", "localhost"})):
        raise ConfigError("[github] api_url must be https:// (http only for a loopback test server)")
    token_ref = g.get("token_ref")
    if token_ref is not None and (not isinstance(token_ref, str) or token_ref.split(":", 1)[0] not in {"env", "file"}):
        raise ConfigError("[github] token_ref must look like env:NAME or file:PATH")
    repos: dict[str, GitHubRepo] = {}
    for r in g.get("repos") or []:
        name = str(r.get("repository") or "")
        if not _REPO_RE.match(name):
            raise ConfigError(f"[[github.repos]] repository {name!r} must be owner/name")
        methods = tuple(r.get("merge_methods") or MERGE_METHODS)
        if not methods or any(m not in MERGE_METHODS for m in methods):
            raise ConfigError(f"[[github.repos]] {name}: merge_methods must be a subset of {MERGE_METHODS}")
        default = str(r.get("default_merge_method") or methods[0])
        if default not in methods:
            raise ConfigError(f"[[github.repos]] {name}: default_merge_method must be one of merge_methods")
        repos[name.lower()] = GitHubRepo(name, bool(r.get("allow_merge", True)), methods, default,
                                         _integrate(r, name, api_url, set((data.get("hosts") or {}).keys())),
                                         bool(r.get("allow_pr_update", False)),
                                         _repository_sync(r, name, api_url, set((data.get("hosts") or {}).keys())))
    bound = set()
    for repo in repos.values():
        for pair in repo.sync.bindings if repo.sync else ():
            if pair in bound:
                raise ConfigError("a host/workspace ID may have only one github.repos sync binding")
            bound.add(pair)
    recipes: dict[str, DeployRecipe] = {}
    for r in (data.get("deploy") or {}).get("recipes") or []:
        name = str(r.get("name") or "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name) or name in recipes:
            raise ConfigError(f"[[deploy.recipes]] name {name!r} is invalid or repeated")
        repo = str(r.get("repository") or "")
        if repo.lower() not in repos:
            raise ConfigError(f"[[deploy.recipes]] {name}: repository {repo!r} needs a [[github.repos]] entry")
        mode = str(r.get("mode") or "")
        if mode not in {"workflow_dispatch", "on_merge"}:
            raise ConfigError(f"[[deploy.recipes]] {name}: mode must be workflow_dispatch or on_merge")
        workflow, job = str(r.get("workflow") or ""), str(r.get("deploy_job") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", workflow) or not job:
            raise ConfigError(f"[[deploy.recipes]] {name}: workflow must be a file name and deploy_job is required")
        inputs = r.get("inputs") or {}
        if not isinstance(inputs, dict) or len(inputs) > 10 or any(
                not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(k)) or v not in _INPUT_FIELDS for k, v in inputs.items()):
            raise ConfigError(f"[[deploy.recipes]] {name}: inputs map workflow inputs to one of {_INPUT_FIELDS}")
        run_name = r.get("run_name_contains")
        if run_name not in (None, "operation_id"):
            raise ConfigError(f"[[deploy.recipes]] {name}: run_name_contains may only be \"operation_id\"")
        if mode == "workflow_dispatch" and not {"operation_id", "source_sha"} <= set(inputs.values()):
            raise ConfigError(f"[[deploy.recipes]] {name}: pass source_sha and operation_id as workflow inputs so a lost "
                              "dispatch reply can be matched to its run")
        ordering = r.get("ordering") or {}
        if (not isinstance(ordering, dict) or set(ordering) - {"mode", "concurrency_group", "cancel_in_progress"}
                or ordering.get("mode", "serialized") != "serialized" or ordering.get("cancel_in_progress", False) is not False):
            raise ConfigError(f"[[deploy.recipes]] {name}: ordering must be serialized with cancel_in_progress=false")
        environment = str(r.get("environment") or name)
        group = ordering.get("concurrency_group", "deploy-" + repo.replace("/", "-") + "-" + environment)
        if not isinstance(group, str) or not group.strip():
            raise ConfigError(f"[[deploy.recipes]] {name}: ordering.concurrency_group is required")
        rollback = r.get("rollback") or {}
        if (not isinstance(rollback, dict) or set(rollback) - {"supported", "identity", "not_undone"}
                or not isinstance(rollback.get("supported", False), bool)):
            raise ConfigError(f"[[deploy.recipes]] {name}: invalid rollback settings")
        supported, identity = rollback.get("supported", False), rollback.get("identity", "source_sha")
        limits = rollback.get("not_undone", [])
        if (identity not in {"source_sha", "artifact"} or not isinstance(limits, list)
                or any(not isinstance(s, str) for s in limits)
                or (supported and (mode != "workflow_dispatch" or "not_undone" not in rollback))
                or (supported and identity == "artifact" and not {"artifact_id", "artifact_digest"} <= set(inputs.values()))):
            raise ConfigError(f"[[deploy.recipes]] {name}: rollback needs workflow_dispatch, explicit not_undone and identity inputs")
        recipes[name] = DeployRecipe(name, repos[repo.lower()].repository, str(r.get("environment") or name),
                                     mode, workflow, job, str(r.get("ref") or "main"),
                                     tuple(sorted((str(k), str(v)) for k, v in inputs.items())), run_name,
                                     DeployOrdering("serialized", group, False), _deploy_verification(r.get("verification")),
                                     DeployRollback(supported, identity, tuple(limits)))
    routes = {}
    for recipe in recipes.values():
        key = (recipe.repository.lower(), recipe.environment)
        route = recipe.ordering
        if key in routes and routes[key] != route:
            raise ConfigError("recipes for the same environment must share provider ordering")
        routes[key] = route
    return GitHubConfig(
        token_ref=token_ref, api_url=api_url, api_version=str(g.get("api_version") or "2026-03-10"),
        timeout_s=max(2.0, min(120.0, float(g.get("timeout_s", 20)))),
        wait_max_s=max(60.0, min(7 * 86400.0, float(g.get("wait_max_s", 3600)))),
        deployment_reconcile_interval_s=float(interval),
        repos=repos, recipes=recipes)


@dataclass
class Config:
    hosts: dict[str, HostConfig]
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    jev: JevConfig = field(default_factory=JevConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    ahp: dict = field(default_factory=dict)
    usage: UsageConfig = field(default_factory=UsageConfig)
    session_history: HistoryConfig = field(default_factory=HistoryConfig)
    client_label: str = "BAT Agent Connector"
    path: Path | None = None
    human_name: str | None = None  # [client] human_name: who relayed messages come from (NEED-<NAME> marker)
    relay_name: str | None = None  # [client] relay_name: the relaying bot, named in relay headers

    def host(self, name: str) -> HostConfig:
        if name not in self.hosts:
            raise ConfigError(
                f"unknown host {name!r}; configured: {', '.join(sorted(self.hosts)) or '(none)'}"
            )
        return self.hosts[name]

    @property
    def any_writes(self) -> bool:
        return any(h.writes for h in self.hosts.values())

    @property
    def any_orchestrate(self) -> bool:
        return any(h.writes and h.orchestrate for h in self.hosts.values())


def parse_config(data: dict, path: Path | None = None) -> Config:
    client_options = data.get("client") or {}
    if not isinstance(client_options, dict):
        raise ConfigError("[client] must be a table")
    defaults = {key: value for key, value in client_timeouts(client_options).items() if value is not None}
    bat = data.get("bat") or {}
    pdir = str(bat.get("profiles_dir") or "")
    hosts: dict[str, HostConfig] = {}
    for name, h in (data.get("hosts") or {}).items():
        if not _NAME_RE.match(name):
            raise ConfigError(f"invalid host name {name!r}")
        if not isinstance(h, dict):
            raise ConfigError(f"host {name!r} must be a table")
        for key in ("url", "fingerprint", "token_ref"):
            if not h.get(key):
                raise ConfigError(f"host {name!r} is missing {key!r}")
        if "token" in h:
            raise ConfigError(f"host {name!r}: inline 'token' is not supported; use token_ref")
        url = str(h["url"])
        if not url.startswith("wss://"):
            raise ConfigError(f"host {name!r}: url must start with wss://")
        writes = h.get("writes", False)
        if not isinstance(writes, bool):
            raise ConfigError(f"host {name!r}: writes must be true or false")
        orch = h.get("orchestrate", False)
        tabs = h.get("orchestrate_register_tabs", False)
        if not isinstance(orch, bool) or not isinstance(tabs, bool):
            raise ConfigError(f"host {name!r}: orchestrate/orchestrate_register_tabs must be true or false")
        if orch and not writes:
            raise ConfigError(f"host {name!r}: orchestrate = true requires writes = true")
        pmode = str(h.get("default_permission_mode", "default"))
        if pmode not in PERMISSION_MODES:
            raise ConfigError(f"host {name!r}: default_permission_mode must be one of {', '.join(PERMISSION_MODES)}")
        if pmode != "default" and not writes:
            raise ConfigError(f"host {name!r}: default_permission_mode = {pmode!r} requires writes = true")
        cleanup = h.get("auto_cleanup", False)
        if not isinstance(cleanup, bool):
            raise ConfigError(f"host {name!r}: auto_cleanup must be true or false")
        if cleanup and not orch:
            raise ConfigError(f"host {name!r}: auto_cleanup = true requires orchestrate = true")
        shared = h.get("shared_clone_worktrees", True)
        if not isinstance(shared, bool):
            raise ConfigError(f"host {name!r}: shared_clone_worktrees must be true or false")
        cmodel = str(h.get("codex_model") or "").strip() or None
        if cmodel and not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", cmodel):
            raise ConfigError(f"host {name!r}: codex_model {cmodel!r} is not a valid model id")
        managed_roots = _managed_roots(name, h.get("managed_roots"))
        confinement = h.get("confinement", {})
        if not isinstance(confinement, dict):
            raise ConfigError(f"host {name!r}: confinement must be a table")
        allowed = {"host_account", "expected_uid", "protected_roots", "check_max_age_s",
                   "check_timeout_s", "check_max_entries", "bat_port", "check_ssh_alias", "check_uid", "bat_account"}
        if set(confinement) - allowed or not isinstance(confinement.get("host_account", False), bool):
            raise ConfigError(f"host {name!r}: invalid confinement settings")
        if confinement.get("host_account"):
            uid = confinement.get("expected_uid")
            if type(uid) is not int or uid <= 0:
                raise ConfigError(f"host {name!r}: confinement.expected_uid must be a non-root UID")
            roots = _managed_roots(name, confinement.get("protected_roots"))
            if not roots or any(a == b or a.startswith(b + "/") or b.startswith(a + "/")
                                for a in roots for b in managed_roots):
                raise ConfigError(f"host {name!r}: protected_roots must be nonempty and separate from managed_roots")
            confinement = {**confinement, "protected_roots": list(roots)}
        if "check_ssh_alias" in confinement:
            alias = confinement["check_ssh_alias"]
            account = confinement.get("bat_account")
            auditor = confinement.get("check_uid")
            if (not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", alias)
                    or not isinstance(account, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", account)
                    or type(auditor) is not int or auditor < 0 or auditor == confinement.get("expected_uid")):
                raise ConfigError(f"host {name!r}: trusted check requires an SSH alias, BAT account and different check_uid")
        elif "check_uid" in confinement or "bat_account" in confinement:
            raise ConfigError(f"host {name!r}: check_uid and bat_account require check_ssh_alias")
        for key, maximum in (("check_max_age_s", 3600), ("check_timeout_s", 30),
                             ("check_max_entries", 100000), ("bat_port", 65535)):
            if key in confinement and (type(confinement[key]) is not int or not 0 < confinement[key] <= maximum):
                raise ConfigError(f"host {name!r}: confinement.{key} must be between 1 and {maximum}")
        hosts[name] = HostConfig(
            name=name,
            url=url,
            fingerprint=normalize_fingerprint(str(h["fingerprint"])),
            token_ref=str(h["token_ref"]),
            writes=writes,
            orchestrate=orch,
            orchestrate_max_sessions=max(1, min(32, int(h.get("orchestrate_max_sessions", 4)))),
            orchestrate_register_tabs=tabs,
            default_permission_mode=pmode,
            auto_cleanup=cleanup,
            codex_model=cmodel,
            profile_id=str(h.get("profile_id") or "default"),
            bat_profiles_dir=str(h.get("bat_profiles_dir") or pdir),
            labels=list(h.get("labels") or []),
            managed_roots=managed_roots,
            shared_clone_worktrees=shared,
            confinement=confinement,
            git=git_transport(h.get("git", "ssh")),
            **client_timeouts({**defaults, **{key: h[key] for key in CLIENT_TIMEOUTS if key in h}}),
        )
    s = data.get("safety") or {}
    safety = SafetyConfig(
        audit_preview_chars=max(0, min(200, int(s.get("audit_preview_chars", 0)))),
        write_min_interval_s=float(s.get("write_min_interval_s", 60)),
        max_writes_per_hour=int(s.get("max_writes_per_hour", 30)),
        max_start_per_call=max(1, min(16, int(s.get("max_start_per_call", 4)))),
    )
    j = data.get("jev") or {}
    en = j.get("enabled", "auto")
    en = "true" if en is True else "false" if en is False else str(en).lower()
    if en not in ("auto", "true", "false"):
        raise ConfigError('[jev] enabled must be "auto", true or false')
    base = str(j.get("base_url") or "https://api.typesafe.ai").rstrip("/")
    if not base.startswith("https://"):
        raise ConfigError("[jev] base_url must be https://")
    jev = JevConfig(
        enabled=en,
        timeout_s=max(0.5, min(30.0, float(j.get("timeout_s", 3.0)))),
        model=str(j.get("model") or "jev-latest"),
        base_url=base,
        api_key_env=str(j.get("api_key_env") or "TYPESAFE_API_KEY"),
    )
    a = data.get("api") or {}
    origins = a.get("allowed_origins") or []
    if not isinstance(origins, list) or not all(
            isinstance(o, str) and re.fullmatch(r"https?://[A-Za-z0-9.\-\[\]:]+", o) for o in origins):
        raise ConfigError("[api] allowed_origins must be a list of scheme://host[:port] origins")
    api = ApiConfig(
        inventory_interval_s=max(10.0, min(3600.0, float(a.get("inventory_interval_s", 60)))),
        stale_after_s=max(30.0, min(86400.0, float(a.get("stale_after_s", 180)))),
        activity_every=max(1, min(100, int(a.get("activity_every", 5)))),
        allowed_origins=tuple(origins),
    )
    cleanup_data = data.get("cleanup") or {}
    if (set(cleanup_data) - {"retained_refs", "history_retention", "permanent_delete"} or
            cleanup_data.get("retained_refs", "keep") != "keep" or
            cleanup_data.get("history_retention", "forever") != "forever" or
            cleanup_data.get("permanent_delete", False) is not False):
        raise ConfigError('[cleanup] supports only retained_refs="keep", history_retention="forever", permanent_delete=false')
    cl = data.get("client") or {}
    label = str(cl.get("label") or "BAT Agent Connector")
    human = str(cl.get("human_name") or "").strip() or None
    if human and not re.fullmatch(r"[A-Za-z0-9 ._-]{1,40}", human):
        raise ConfigError("[client] human_name must be 1-40 letters/digits/spaces")
    relay_name = str(cl.get("relay_name") or "").strip() or None
    if relay_name and not re.fullmatch(r"[A-Za-z0-9 ._-]{1,40}", relay_name):
        raise ConfigError("[client] relay_name must be 1-40 letters/digits/spaces")
    from .ahp_config import parse_ahp
    return Config(hosts=hosts, safety=safety, jev=jev, api=api, github=parse_github(data), ahp=parse_ahp(data), usage=parse_usage(data), session_history=parse_history(data), client_label=label,
                  path=path, human_name=human, relay_name=relay_name)


def _merge_config(target: dict, source: dict) -> None:
    """Tables merge recursively; scalars and arrays replace earlier values."""
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_config(target[key], value)
        else:
            target[key] = value


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path).expanduser() if path else default_config_path()
    overlay = p.parent / "hosts.d"
    try:
        fragments = sorted(
            (f for f in overlay.iterdir() if not f.name.startswith(".") and f.suffix == ".toml"),
            key=lambda f: f.name,
        ) if overlay.exists() else []
    except OSError:
        raise ConfigError(f"cannot list config fragments: {overlay}") from None
    files = ([p] if os.path.lexists(p) else []) + fragments
    if not files:
        raise ConfigError(
            f"config not found: {p} (run `batc import-bat` or copy examples/hosts.example.toml)"
        )
    data: dict = {}
    host_files: dict[str, Path] = {}
    for source in files:
        try:
            # Keep base-file compatibility; overlays use the hardened private-file reader.
            content = source.read_text() if source == p else read_private(source, 1024 * 1024)
            fragment = tomllib.loads(content)
        except (OSError, ValueError):
            # Parser and OS exception messages may contain configuration values.
            raise ConfigError(f"cannot read or parse {source}: check TOML and private file permissions/type") from None
        hosts = fragment.get("hosts", {})
        if not isinstance(hosts, dict):
            raise ConfigError(f"hosts must be a table in {source}")
        for name in hosts:
            if name in host_files:
                raise ConfigError(f"host {name!r} is defined in both {host_files[name]} and {source}")
            host_files[name] = source
        _merge_config(data, fragment)
    return parse_config(data, p)


def device_id() -> str:
    """Stable per-client deviceId (avoids 'remote client connected' spam on hosts)."""
    if os.environ.get("BATC_DEVICE_ID"):
        return os.environ["BATC_DEVICE_ID"].strip()
    d = config_dir()
    f = d / "device-id"
    try:
        v = f.read_text().strip()
        if v:
            return v
    except OSError:
        pass
    d.mkdir(parents=True, exist_ok=True)
    v = f"batc-{uuid.uuid4()}"
    try:
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return f.read_text().strip()
    with os.fdopen(fd, "w") as fh:
        fh.write(v + "\n")
    return v
