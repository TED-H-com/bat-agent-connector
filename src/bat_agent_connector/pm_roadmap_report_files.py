"""Pure, bounded file set for one already authorized rendered roadmap report."""
import hashlib
import json
import re

from . import pm_revision_paths
from .operations import OperationError


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def too_large():
    return OperationError("PM_REPORT_MATERIALIZATION_TOO_LARGE", "The report exceeds the owned carrier byte limit", 413)


def files(report, source_manifest, operation_id):
    if not isinstance(operation_id, str) or not re.fullmatch(r"op_[0-9a-f]{32}", operation_id):
        raise OperationError("PM_REVISION_INVALID", "An original operation identity is required", 422)
    identity = "pmrpt_" + operation_id[3:]
    metadata = {key: report[key] for key in pm_revision_paths.REPORT_METADATA}
    manifest = {"schema": "batc.pm.roadmap-report.v1", "artifact_kind": pm_revision_paths.REPORT_KIND,
        "materialization_id": identity, "operation_id": operation_id, **metadata, "source_manifest": source_manifest}
    revision = {"workspace_id": report["workspace_id"], "revision_id": identity,
        "artifact_kind": pm_revision_paths.REPORT_KIND,
        **{key: report[key] for key in pm_revision_paths.REPORT_FIELDS[1:]}}
    paths = pm_revision_paths.rev_paths(revision)
    content = {path: canonical(manifest) + "\n" if path.endswith("/manifest.json") else report["content"] for path in sorted(paths)}
    encoded = {path: value.encode("utf-8") for path, value in content.items()}
    if sum(map(len, encoded.values())) > 512_000:
        raise too_large()
    digests = {path: hashlib.sha256(value).hexdigest() for path, value in encoded.items()}
    revision.update(files=content, file_digests=digests, document_digest=hashlib.sha256(canonical(digests).encode()).hexdigest())
    pm_revision_paths.report_manifest({**revision, "operation_id": operation_id})
    return revision
