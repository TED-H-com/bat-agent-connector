"""Bounded, descriptive OpenSpec path/Markdown observations, not a schema validator."""
from __future__ import annotations

import re

ADAPTER_VERSION = "openspec-fixed-git-v1"
UPSTREAM_REVISION = "9111a7654d7800391459431fff4eaf66e33a3d2e"
MAX_FILE_BYTES = 128_000
MAX_TOTAL_BYTES = 384_000
MAX_FILES = 100
MAX_WIRE_BYTES = 1_800_000
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")


def describe_path(path):
    """Recognize only the declared standard layout; never normalize a caller path."""
    if not isinstance(path, str) or len(path) > 800 or "\\" in path:
        return None
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    if path == "openspec/config.yaml":
        return {"role": "project_metadata", "location": "project", "change_key": None, "capability": None}
    result = {"role": "main_spec", "location": "main", "change_key": None, "capability": None}
    if parts[:2] == ["openspec", "specs"]:
        capability = parts[2:-1]
    elif parts[:2] == ["openspec", "changes"] and len(parts) >= 4:
        offset = 3 if parts[2] == "archive" else 2
        if len(parts) <= offset + 1 or not _COMPONENT.fullmatch(parts[offset]):
            return None
        result.update(location="archive" if offset == 3 else "change", change_key=parts[offset], role="delta_spec")
        tail = parts[offset + 1:]
        roles = {"proposal.md": "proposal", "design.md": "design", "tasks.md": "tasks", ".openspec.yaml": "change_metadata"}
        if len(tail) == 1 and tail[0] in roles:
            return {**result, "role": roles[tail[0]]}
        if tail[0] != "specs":
            return None
        capability = tail[1:-1]
    else:
        return None
    if parts[-1] != "spec.md" or not 1 <= len(capability) <= 8 or any(not _COMPONENT.fullmatch(part) for part in capability):
        return None
    return {**result, "capability": "/".join(capability)}


def schema_declaration(content):
    """A deliberately small top-level scalar observation, never effective YAML configuration."""
    candidates = [line for line in content.splitlines() if re.match(r"schema\s*:", line)]
    if not candidates:
        return {"declared_name": None, "status": "not_identified", "effective_schema": "unknown"}
    match = re.fullmatch(r"schema\s*:\s*(?:([A-Za-z0-9_.-]+)|'([A-Za-z0-9_.-]+)'|\"([A-Za-z0-9_.-]+)\")\s*(?:#.*)?", candidates[0])
    if len(candidates) != 1 or not match:
        return {"declared_name": None, "status": "unsupported_yaml", "effective_schema": "unknown"}
    name = next(value for value in match.groups() if value is not None)
    return {"declared_name": name, "status": "standard_name_declared" if name == "spec-driven" else "custom_name_declared",
            "effective_schema": "unknown"}


def markdown_observations(content, *, tasks=False):
    """Record revision-local locations. No line, checkbox or heading becomes a WorkItem."""
    headings, reports = [], []
    # Conservative lexical subset: standard list checkbox lines, including nested lists.
    # Fenced lines are explicitly labelled examples/ambiguous rather than silently omitted.
    fence = None
    for line_number, line in enumerate(content.splitlines(), 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading and fence is None:
            headings.append({"line": line_number, "level": len(heading[1]), "title": heading[2]})
        if tasks:
            match = re.match(r"^\s*(?:[-*+]|[0-9]{1,9}[.)])\s+\[([^]\r\n]*)\](?![([])\s*(.*)$", line)
            if match and len(match[1].strip()) <= 1:
                reports.append({"line": line_number, "text": match[2], "marker": match[1],
                    "reported_checked": match[1].strip().lower() == "x", "in_fenced_block": fence is not None,
                    "source_status": "document_checkbox_report", "verification": "unknown", "counts_as_done": False})
        if len(headings) > 500 or len(reports) > 500:
            return {"status": "observation_limit", "headings": [], "task_reports": [], "task_report_count": None}
    return {"status": "lexical_observation_only", "headings": headings, "task_reports": reports,
            "task_report_count": len(reports) if tasks else None}
