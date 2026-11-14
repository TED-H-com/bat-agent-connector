#!/usr/bin/env python3
"""Generate self-contained agent adapters; --check is read-only and fails on drift.

Only the standard library is needed (Python 3.10+). The canonical skill remains the
packaged source; adapters add transport context, never a second workflow policy.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("skills/bat-agent-connector/SKILL.md")
GENERATOR = "scripts/generate_agent_skills.py"
ADAPTERS = {
    "hermes": """## Hermes adapter

Register `bat-agent-connector-mcp --principal-only` as `bat` in Hermes's MCP configuration
(`~/.hermes/config.yaml`, `mcp_servers.bat`). Tools may appear as
`mcp__bat__capabilities_get`; use the names actually returned by tool discovery.
Use the operator-configured stdio or streamable HTTP transport and supply this
agent's `BATC_API_TOKEN` through the server environment; `BATC_TASK_URL` selects
the configured daemon RPC endpoint. The `batc` CLI can carry the same central
operation with the same identity when available; a refusal still applies.
""",
    "grokbot": """## Grokbot adapter

Use the runtime's standard MCP client configuration to register
`bat-agent-connector-mcp --principal-only` (stdio), or a principal-only streamable
HTTP MCP endpoint configured for this agent. Discover the actual tool names;
a runtime may add its own server prefix.
Supply this agent's `BATC_API_TOKEN` through the server environment;
`BATC_TASK_URL` selects the configured daemon RPC endpoint. This bundle assumes
no Grokbot-specific installation path, command or Hermes dependency.
""",
}


def literal(path: Path, name: str) -> str | int:
    """Read source constants without importing a backend or opening its state."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f"{path}: missing constant {name}")


def match(pattern: str, text: str, field: str) -> str:
    found = re.search(pattern, text, re.MULTILINE)
    if not found:
        raise ValueError(f"canonical skill: missing or invalid {field}")
    return found.group(1)


LINK_START = re.compile(r"(?<!\\)\]\([ \t\n]*|(?m:^[ ]{0,3}\[(?:\\.|[^]\\\n])+\]:[ \t]*)")


def mask_code(text: str) -> str:
    """Hide fenced blocks and inline code while preserving destination offsets."""
    lines = []
    fence = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = None
            lines.append(re.sub(r"[^\n]", " ", line))
        elif marker:
            fence = marker.group(1)
            lines.append(re.sub(r"[^\n]", " ", line))
        else:
            lines.append(line)
    masked = "".join(lines)
    return re.sub(r"(?<![\\`])(`+)(?!`)(?s:.*?)(?<!`)\1(?!`)",
                  lambda found: re.sub(r"[^\n]", " ", found.group()), masked)


def markdown_targets(text: str):
    """Yield destination spans from inline links/images and reference definitions."""
    masked = mask_code(text)
    for start in LINK_START.finditer(masked):
        pos = start.end()
        if pos >= len(masked):
            continue
        angle = masked[pos] == "<"
        if angle:
            pos += 1
        begin = pos
        depth = 0
        while pos < len(masked):
            char = masked[pos]
            if char == "\\" and pos + 1 < len(masked):
                pos += 2
                continue
            if angle:
                if char in ">\n":
                    break
            elif char.isspace() or (char == ")" and depth == 0):
                break
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            pos += 1
        if pos > begin and (not angle or (pos < len(masked) and masked[pos] == ">")):
            yield begin, pos, text[begin:pos]


def relative_file(target: str) -> tuple[str, str] | None:
    """Split a relative destination into its file part and query/fragment suffix."""
    if target.startswith(("/", "#")) or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
        return None
    parts = re.split(r"([?#])", target, maxsplit=1)
    return (parts[0], "".join(parts[1:])) if parts[0] else None


def rewrite_links(text: str, source: Path, destination: Path) -> str:
    replacements = []
    for begin, end, target in markdown_targets(text):
        relative = relative_file(target)
        if relative:
            file_part, suffix = relative
            rebased = os.path.relpath(source.parent / file_part, destination.parent)
            replacements.append((begin, end, rebased.replace(os.sep, "/") + suffix))
    for begin, end, replacement in reversed(replacements):
        text = text[:begin] + replacement + text[end:]
    return text


def broken_links(path: Path) -> list[str]:
    broken = []
    for _, _, target in markdown_targets(path.read_text(encoding="utf-8")):
        relative = relative_file(target)
        if relative:
            file_part, _ = relative
            file_part = unquote(re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", file_part))
            if not (path.parent / file_part).exists():
                broken.append(f"{path}: {target}")
    return broken


def render(root: Path) -> dict[Path, bytes]:
    source = (root / SOURCE).read_bytes()
    text = source.decode("utf-8")
    if not text.startswith("---\n") or "\r" in text or not text.endswith("\n"):
        raise ValueError("canonical skill must have YAML frontmatter, LF endings and a final newline")
    header, body = text[4:].split("\n---\n", 1)
    # Keep the historical top-level version for existing skill installers. New
    # metadata is flat and quoted; append provenance to its final mapping.
    if re.findall(r"^([a-z_-]+):", header, re.MULTILINE)[-1] != "metadata":
        raise ValueError("canonical metadata must be the final frontmatter mapping")
    version = match(r"^version: ([0-9.]+)$", header, "version")
    workflow = match(r'^  workflow_version: "([0-9.-]+)"$', header, "workflow_version")
    api = match(r'^  api_version: "([0-9]+)"$', header, "api_version")
    contract = match(r'^  contract_version: "([0-9-]+)"$', header, "contract_version")
    package = literal(root / "src/bat_agent_connector/__init__.py", "__version__")
    project = (root / "pyproject.toml").read_text(encoding="utf-8").split("[project]\n", 1)[1].split("\n[", 1)[0]
    project_version = match(r'^version = "([0-9.]+)"$', project, "project version")
    api_path = root / "src/bat_agent_connector/api_v1.py"
    if version != package or version != project_version:
        raise ValueError(f"skill/package version mismatch: {version}, {package}, {project_version}")
    if api != str(literal(api_path, "API_VERSION")) or contract != literal(api_path, "CONTRACT_VERSION"):
        raise ValueError("skill API/contract metadata differs from api_v1.py; review canonical workflow first")
    if len(workflow) > 64 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", workflow):
        raise ValueError("workflow_version must be YYYY-MM-DD.revision (at most 64 characters)")
    digest = hashlib.sha256(source).hexdigest()
    outputs = {}
    for adapter, intro in ADAPTERS.items():
        relative = Path("skills") / adapter / "bat-agent-connector/SKILL.md"
        adapter_header = rewrite_links(header, root / SOURCE, root / relative)
        adapter_body = rewrite_links(body, root / SOURCE, root / relative)
        provenance = (
            f'  generated_by: "{GENERATOR}"\n'
            '  generator_version: "1"\n'
            f'  adapter: "{adapter}"\n'
            f'  canonical_source: "{SOURCE.as_posix()}"\n'
            f'  canonical_sha256: "{digest}"\n'
        )
        if adapter == "hermes":
            provenance += (
                "  hermes:\n"
                "    tags: [bat, better-agent-terminal, claude-code, codex, mcp, supervision, worktree, orchestration]\n"
                "    category: autonomous-ai-agents\n"
                "    related_skills: [claude-code, codex]\n"
            )
        generated = (
            f"---\n{adapter_header}\n{provenance}---\n\n"
            f"<!-- GENERATED by {GENERATOR}; do not edit. -->\n\n"
            f"{intro}\n<!-- BEGIN CANONICAL WORKFLOW -->\n{adapter_body}"
            "<!-- END CANONICAL WORKFLOW -->\n"
        )
        outputs[relative] = generated.encode("utf-8")
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if a generated adapter is missing or changed")
    args = parser.parse_args(argv)
    try:
        outputs = render(ROOT)
    except (ValueError, OSError, SyntaxError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    drift = []
    for relative, expected in outputs.items():
        path = ROOT / relative
        if path.exists() and path.read_bytes() == expected:
            continue
        if args.check:
            drift.append(relative.as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
            print(f"Generated {relative}")
    broken = []
    if args.check:
        for relative in (SOURCE, *outputs):
            path = ROOT / relative
            if path.exists():
                broken.extend(broken_links(path))
    if broken:
        print("Broken relative links:\n" + "\n".join(broken), file=sys.stderr)
    if drift:
        print("Generated skills are out of date: " + ", ".join(drift), file=sys.stderr)
        print(f"Run python3 {GENERATOR}", file=sys.stderr)
    return 1 if drift or broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
