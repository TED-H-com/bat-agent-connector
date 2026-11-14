#!/usr/bin/env python3
"""Check tracked public content without printing potentially private values.

This is a publication guard, not a substitute for reviewing deployment narratives,
screenshots, Git history and issue/PR text. It does not read any live configuration.
"""

from __future__ import annotations

import argparse
import fnmatch
import ipaddress
import re
import shutil
import subprocess
from pathlib import Path

PRIVATE_CONFIG_NAMES = {"hosts.toml", "discord-channels.toml", ".env"}
PRIVATE_CONFIG_GLOBS = ("*.token", "*.enc.json", ".env.*")
# Allowed template filename under the .env.* rule.
SYNTHETIC_CONFIG_NAMES = {".env.example"}

# Synthetic home-directory usernames used in tests, fixtures and documentation examples
# (e.g. docs/getting-started.md, desktop/tests/diagnostics.test.ts, tests/test_public_content.py).
SYNTHETIC_USERS = {
    "alice", "bob", "developer", "example", "fixture", "operator", "person", "runner",
    "runneradmin", "test", "user",
}
HOME_PATH = re.compile(
    r"(?<![A-Za-z0-9_])(?:/var/home/|/home/|/Users/|[A-Za-z]:/Users/)([A-Za-z0-9_][A-Za-z0-9_.-]*)",
    re.IGNORECASE,
)

# Synthetic shared conversation links (none in tracked files).
SYNTHETIC_SHARED_LINKS: set[str] = set()
SHARED_CHAT = re.compile(
    r"https?://(?:"
    r"(?:chatgpt\.com|chat\.openai\.com|claude\.ai|gemini\.google\.com|grok\.com)/share/"
    r"|g\.co/gemini/share/"
    r"|copilot\.microsoft\.com/shares/"
    r")[A-Za-z0-9_-]+"
)

# Synthetic SSH key fingerprints (none in tracked files).
SYNTHETIC_SSH_FINGERPRINTS: set[str] = set()
SSH_FINGERPRINT = re.compile(
    r"(?<![A-Za-z0-9_])SHA256:[A-Za-z0-9+/]{43}(?![A-Za-z0-9+/])"
    r"|(?<![0-9A-Za-z:])(?:MD5:)?[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){15}(?![0-9A-Za-z:])"
)

# Synthetic Connector API tokens (none in tracked files).
SYNTHETIC_CONNECTOR_TOKENS: set[str] = set()
# Matches api_auth.issue(): "batc_" + secrets.token_urlsafe(32) (43 URL-safe base64 characters).
CONNECTOR_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])batc_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")

PRIVATE_IPV4_NETWORKS = tuple(
    ipaddress.IPv4Network(f"{first}.{second}.0.0/{prefix}")
    for first, second, prefix in ((10, 0, 8), (172, 16, 12), (192, 168, 16), (100, 64, 10))
)
# Synthetic private and Tailscale IPv4 addresses used in tests, fixtures and documentation:
# - 10.0.0.5: desktop/fleet-core/src/source_catalog_tests.rs,
#   tests/fixtures/bat-source-policy-v1.json, tests/source_policy_inputs.py,
#   tests/test_native_source_admission.py
# - 100.64.0.0: docs/design/fleet-configuration.md
# - 100.64.0.1: desktop/fleet-core/src/supervisor_status.rs,
#   desktop/fleet-core/tests/fixtures/inventory.json,
#   desktop/fleet-core/tests/fixtures/route-probe/main.rs,
#   desktop/fleet-core/tests/route_native.rs, desktop/fleet-core/tests/routes.rs,
#   desktop/fleet-core/tests/supervisor.rs
# - 100.64.0.2: desktop/fleet-core/tests/connection_setup.rs,
#   desktop/fleet-core/tests/fixtures/inventory.json, desktop/fleet-core/tests/routes.rs
# - 100.64.0.3, 100.64.0.5: desktop/fleet-core/tests/fixtures/inventory.json
SYNTHETIC_IPV4_ADDRESSES = {
    "10.0.0.5",
    "100.64.0.0",
    "100.64.0.1",
    "100.64.0.2",
    "100.64.0.3",
    "100.64.0.5",
}
IPV4_CANDIDATE = re.compile(r"(?<![A-Za-z0-9_.])(?:\d{1,3}\.){3}\d{1,3}(?![A-Za-z0-9_])(?!\.\d)")

ALLOWED_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "localhost",
    "users.noreply.github.com",
}
ALLOWED_EMAIL_DOMAIN_SUFFIXES = (".example", ".invalid", ".test")
# Allowed non-example address used in commit attribution.
SYNTHETIC_EMAILS = {
    "noreply@anthropic.com",
}
EMAIL_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9_][A-Za-z0-9._%+-]*@(?:"
    r"localhost|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}"
    r"))(?![A-Za-z0-9._%+-])"
)
URL_AUTHORITY_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>()/?#@]*\Z")
SCP_PATH_SUFFIX = re.compile(r":[A-Za-z0-9_./~-]")


def is_private_config_name(name: str) -> bool:
    if name in SYNTHETIC_CONFIG_NAMES:
        return False
    return name in PRIVATE_CONFIG_NAMES or any(
        fnmatch.fnmatchcase(name, pattern) for pattern in PRIVATE_CONFIG_GLOBS
    )


def _is_flagged_ipv4(candidate: str) -> bool:
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return (
        isinstance(address, ipaddress.IPv4Address)
        and str(address) not in SYNTHETIC_IPV4_ADDRESSES
        and any(address in network for network in PRIVATE_IPV4_NETWORKS)
    )


def _is_flagged_email(line: str, match: re.Match[str]) -> bool:
    address = match.group(1)
    start, end = match.span(1)
    if URL_AUTHORITY_PREFIX.search(line[max(0, start - 256) : start]):
        return False
    suffix = line[end:]
    low = address.casefold()
    if SCP_PATH_SUFFIX.match(suffix) or (suffix.startswith(":") and low.split("@") == ["git", "github.com"]):
        return False
    if low in SYNTHETIC_EMAILS:
        return False
    domain = low.rsplit("@", 1)[1]
    if any(label.startswith("-") or label.endswith("-") for label in domain.split(".")):
        return False
    return not (domain in ALLOWED_EMAIL_DOMAINS or domain.endswith(ALLOWED_EMAIL_DOMAIN_SUFFIXES))


def check_text(text: str) -> list[tuple[int, str]]:
    findings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if any(
            match.group(0) not in SYNTHETIC_SHARED_LINKS
            for match in SHARED_CHAT.finditer(line)
        ):
            findings.append((line_number, "shared conversation link"))
        normalized = line.replace("\\\\", "\\").replace("\\", "/")
        if any(match[1].casefold() not in SYNTHETIC_USERS for match in HOME_PATH.finditer(normalized)):
            findings.append((line_number, "non-example home path"))
        if any(
            match.group(0) not in SYNTHETIC_SSH_FINGERPRINTS
            for match in SSH_FINGERPRINT.finditer(line)
        ):
            findings.append((line_number, "SSH key fingerprint"))
        if any(
            match.group(0) not in SYNTHETIC_CONNECTOR_TOKENS
            for match in CONNECTOR_TOKEN.finditer(line)
        ):
            findings.append((line_number, "connector token"))
        if any(_is_flagged_ipv4(match.group(0)) for match in IPV4_CANDIDATE.finditer(line)):
            findings.append((line_number, "private or tailnet IPv4 address"))
        if any(_is_flagged_email(line, match) for match in EMAIL_CANDIDATE.finditer(line)):
            findings.append((line_number, "e-mail address"))
    return findings


def check_tree(root: Path) -> list[tuple[str, int, str]]:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git is required to inspect tracked public content")
    # Resolved executable and fixed arguments; root is only the working directory.
    tracked = subprocess.check_output([git, "ls-files", "-z"], cwd=root).decode().split("\0")  # noqa: S603
    findings = []
    for name in filter(None, tracked):
        path = Path(name)
        if is_private_config_name(path.name):
            findings.append((name, 1, "private configuration filename"))
        # Third-party source must retain its original provenance and checksum.
        if name.startswith("desktop/vendor/"):
            continue
        source = root / path
        if not source.is_file() or source.is_symlink():
            continue
        data = source.read_bytes()
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend((name, line, category) for line, category in check_text(text))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    findings = check_tree(args.root)
    for path, line, category in findings:
        print(f"{path}:{line}: {category}")
    if findings:
        print(f"Public content check failed: {len(findings)} finding(s); values withheld.")
        return 1
    print("Public content check passed for tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
