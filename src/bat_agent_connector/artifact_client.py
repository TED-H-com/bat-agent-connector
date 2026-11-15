"""CLI/MCP byte transport to the authenticated loopback HTTP adapter."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .config import state_dir
from .task_daemon import DEFAULT_URL, TASK_URL_REFUSAL, request


def content_request(path, *, data=None, token=None, timeout=310):
    url = os.environ.get("BATC_TASK_URL", DEFAULT_URL)
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.path != "/rpc" or parsed.query or parsed.fragment):
        raise ValueError(TASK_URL_REFUSAL)
    token = token or os.environ.get("BATC_API_TOKEN") or Path(os.environ.get(
        "BATC_TASK_ADMIN_TOKEN_FILE", state_dir() / "task-admin.token")).read_text().strip()
    req = urllib.request.Request(  # noqa: S310 - validated loopback origin, fixed API path
        f"{parsed.scheme}://{parsed.netloc}{path}", data=data, method="POST" if data is not None else "GET",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/octet-stream"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:  # noqa: S310 - loopback checked
            return response.read()
    except urllib.error.HTTPError as exc:
        error = json.load(exc)["error"]
        raise ValueError(error["code"] + ": " + error["message"]) from None


def upload(data, name, key, *, media_type="application/octet-stream", artifact_id=None,
           expected_latest_revision=None, token=None, mcp=False):
    token = token or os.environ.get("BATC_API_TOKEN")
    out = request("op_submit", _auth_token=token, timeout=40, entry="mcp" if mcp else "cli", wait_s=10,
                  action="artifact.upload", idempotency_key=key,
                  target={"artifact_id": artifact_id} if artifact_id else {},
                  params={"display_name": name, "media_type": media_type, "size_bytes": len(data),
                          "expected_digest": hashlib.sha256(data).hexdigest(), **({"mcp": True} if mcp else {})},
                  preconditions={"expected_latest_revision": expected_latest_revision} if artifact_id else {})
    op = out["operation"]
    if op["status"] == "waiting_external":
        row = request("op_get", _auth_token=token, operation_id=op["operation_id"])["operation"]
        if row["external_refs"].get("content_url"):
            content_request(row["external_refs"]["content_url"], data=data, token=token)
        op = request("op_get", _auth_token=token, operation_id=op["operation_id"], wait_s=20, timeout=30)["operation"]
    return {"operation": op, "idempotency_key": key}
