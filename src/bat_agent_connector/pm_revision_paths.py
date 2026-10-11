"""Portable fixed revision paths shared with the owned-host Git script."""
import re


def rev_paths(data):
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
