"""Frozen, owned spec adapter artifacts; task keys are identities, never prose."""
from __future__ import annotations

import hashlib
import json
import re

from . import pm_work_packages
from .operations import OperationError

SCHEMA = "batc.pm.spec-materialization/v1"
MATERIALIZATION = re.compile(r"pmsp_[0-9a-f]{32}")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def history(db, package):
    rows, seen = [], set()
    while package is not None:
        identity = package["package_id"]
        if identity in seen or len(seen) >= pm_work_packages.MAX_ANCESTORS:
            raise OperationError("PM_SPEC_SOURCE_UNAVAILABLE", "The original package chain cannot be verified", 404)
        seen.add(identity)
        rows.append(package)
        previous = package["previous_package_id"]
        package = pm_work_packages._record(db, package["workspace_id"], previous) if previous else None
    return list(reversed(rows))


def document(db, package):
    chain = history(db, package)
    root = chain[0]["package_id"]
    tasks = {}
    for revision in chain:
        current = {task["key"]: task for task in revision["document"]["tasks"]}
        for key in sorted(set(tasks) | set(current)):
            task = tasks.setdefault(key, {"task_id": "pmt_" + digest([package["workspace_id"], root, key])[:32],
                                          "key": key, "history": []})
            state = "active" if key in current else "removed"
            task["history"].append({"package_id": revision["package_id"], "document_digest": revision["document_digest"],
                                    "state": state, **({"task": current[key]} if key in current else {})})
            task["state"] = state
    return {"schema": SCHEMA, "workspace_id": package["workspace_id"], "change_id": "pm-" + root[5:],
            "root_package_id": root, "package_ref": {key: package[key] for key in ("workspace_id", "package_id", "document_digest")},
            "package": package["document"], "tasks": [tasks[key] for key in sorted(tasks)],
            "source_chain": [{key: row[key] for key in ("package_id", "document_digest", "operation_id")} for row in chain],
            "verification": "unassessed", "delivery": "not_observed", "layout": "connector_owned_revision_adapter",
            "openspec_cli_compatibility": "not_claimed"}


def paths(value):
    root = "openspec/changes/" + value["change_id"] + "/revisions/" + value["package_ref"]["package_id"] + "/"
    index = "docs/pm/specs/" + value["change_id"] + "/revisions/" + value["package_ref"]["package_id"] + ".json"
    return root, index


def files(value, materialization_id, artifact_ref):
    root, index = paths(value)
    package = value["package"]
    active = {row["key"]: row for row in value["tasks"] if row["state"] == "active"}
    task_text = "# Tasks\n\nCheckboxes are planning markers; they do not prove verification or delivery.\n\n"
    for task in package["tasks"]:
        task_text += "- [ ] " + task["key"] + " (" + active[task["key"]]["task_id"] + "): " + task["description"] + "\n"
    removed = [row for row in value["tasks"] if row["state"] == "removed"]
    if removed:
        task_text += "\n## Removed identities\n\n" + "\n".join("- " + row["key"] + " (" + row["task_id"] + ")" for row in removed) + "\n"
    output = {root + "package.json": canonical(value),
              root + "proposal.md": "# " + package["title"] + "\n\n" + package["rationale"] + "\n\nFixed package: " + value["package_ref"]["package_id"] + "\n",
              root + "requirements.md": "# Adopted requirements\n\n```json\n" + canonical(package["requirements"]) + "\n```\n",
              root + "design.md": "# Design\n\n" + package["design"]["text"] + "\n",
              root + "tasks.md": task_text,
              root + "manifest.json": canonical({key: value[key] for key in ("schema", "package_ref", "change_id", "root_package_id", "tasks", "source_chain", "verification", "delivery", "openspec_cli_compatibility")})}
    output[index] = canonical({"materialization_id": materialization_id, "document_digest": digest(value),
                              "artifact_ref": artifact_ref, "files": {path: hashlib.sha256(raw.encode()).hexdigest() for path, raw in output.items()}})
    hashes = {path: hashlib.sha256(raw.encode()).hexdigest() for path, raw in output.items()}
    if sum(len(raw.encode()) for raw in output.values()) > 512_000:
        raise OperationError("PM_SPEC_TOO_LARGE", "The complete owned spec files exceed the bounded Git carrier", 413)
    return {"workspace_id": value["workspace_id"], "revision_id": materialization_id,
            "artifact_kind": "pm-spec", "change_id": value["change_id"], "package_id": value["package_ref"]["package_id"],
            "files": output, "file_digests": hashes, "document_digest": digest(hashes)}
