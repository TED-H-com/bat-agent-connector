"""Portable fixed revision paths shared with the owned-host Git script."""
import hashlib
import json
import re

REPORT_KIND = "pm-roadmap-report"
REPORT_FIELDS = ("artifact_kind", "observation_revision_id", "format", "locale", "renderer_version")
REPORT_METADATA = ("workspace_id", "observation_revision_id", "snapshot_id", "snapshot_digest",
    "snapshot_view_digest", "baseline_revision_id", "forecast_revision_id", "baseline_digest",
    "forecast_digest", "observation_digest", "source_set_digest", "view_digest", "template_version",
    "renderer_version", "format", "locale", "media_type", "byte_length", "output_digest", "page_count", "coverage")
REPORT_EXTENSIONS = {"markdown": "md", "html": "html", "svg": "svg"}


def report_manifest(data):
    """Validate the typed report envelope on both the service and owned host."""
    paths = rev_paths(data)
    if not isinstance(data["files"], dict) or any(not isinstance(value, str) for value in data["files"].values()):
        raise ValueError("PM_REVISION_INVALID")
    if data["revision_id"] != "pmrpt_" + data["operation_id"][3:]:
        raise ValueError("PM_REVISION_INVALID")
    prefix = "docs/pm/reports/" + data["revision_id"] + "/"
    manifest = json.loads(data["files"][prefix + "manifest.json"])
    expected = {"schema": "batc.pm.roadmap-report.v1", "artifact_kind": REPORT_KIND,
        "materialization_id": data["revision_id"], "operation_id": data["operation_id"],
        **{key: data[key] for key in ("workspace_id", *REPORT_FIELDS[1:])}}
    if (set(data["files"]) != paths or not isinstance(manifest, dict)
            or set(manifest) != set(REPORT_METADATA) | {"schema", "artifact_kind", "materialization_id", "operation_id", "source_manifest"}
            or any(manifest.get(key) != value for key, value in expected.items())
            or type(manifest["template_version"]) is not int or manifest["template_version"] != 1
            or type(manifest["renderer_version"]) is not int
            or not isinstance(manifest["source_manifest"], list)):
        raise ValueError("PM_REVISION_INVALID")
    content = data["files"][prefix + "report." + REPORT_EXTENSIONS[data["format"]]].encode("utf-8")
    if (type(manifest["byte_length"]) is not int or manifest["byte_length"] != len(content)
            or manifest["output_digest"] != hashlib.sha256(content).hexdigest()):
        raise ValueError("PM_REVISION_INVALID")
    return manifest


def rev_paths(data):
    if data.get("artifact_kind") == REPORT_KIND:
        if (not re.fullmatch(r"pmrpt_[0-9a-f]{32}", data["revision_id"])
                or not re.fullmatch(r"pmro_[0-9a-f]{32}", data["observation_revision_id"])
                or data["format"] not in REPORT_EXTENSIONS or data["locale"] not in {"en", "zh-TW"}
                or type(data["renderer_version"]) is not int or data["renderer_version"] != 1):
            raise ValueError("PM_REVISION_INVALID")
        prefix = "docs/pm/reports/" + data["revision_id"] + "/"
        return {prefix + "report." + REPORT_EXTENSIONS[data["format"]], prefix + "manifest.json"}
    if data.get("artifact_kind") == "pm-spec":
        if (not re.fullmatch(r"pmsp_[0-9a-f]{32}", data["revision_id"])
                or not re.fullmatch(r"pm-[0-9a-f]{32}", data["change_id"])
                or not re.fullmatch(r"pmwp_[0-9a-f]{32}", data["package_id"])):
            raise ValueError("PM_REVISION_INVALID")
        prefix = "openspec/changes/" + data["change_id"] + "/revisions/" + data["package_id"] + "/"
        return {prefix + name for name in ("package.json", "proposal.md", "requirements.md", "design.md", "tasks.md", "manifest.json")} | {
            "docs/pm/specs/" + data["change_id"] + "/revisions/" + data["package_id"] + ".json"}
    if "artifact_kind" in data or not re.fullmatch(r"pmrev_[0-9a-f]{32}", data["revision_id"]):
        raise ValueError("PM_REVISION_INVALID")
    return {"docs/pm/revisions/" + data["revision_id"] + "/plan." + ext for ext in ("md", "json")}
