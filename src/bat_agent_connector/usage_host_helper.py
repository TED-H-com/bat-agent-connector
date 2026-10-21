"""Versioned metrics-only helper entrypoint; dependencies are shipped from shared sources."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

from .usage_adapters import METRICS, UsageInvalid, count, identifier, quota, timestamp
from .usage_collector import PAGE_LINES, read_page

VERSION = 1
REQUEST_BYTES = 16384
REPLY_BYTES = 2 * 1024 * 1024
ERRORS = frozenset({"unsafe_file", "source_changed", "line_limit", "invalid_json", "invalid_timestamp",
    "invalid_counter", "missing_counter", "non_usage_counter", "invalid_inclusion", "invalid_record",
    "session_mismatch", "missing_identity", "counter_regression", "source_unavailable", "invalid_reply"})


def invalid():
    raise UsageInvalid("invalid_reply")


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        invalid()


def state(value):
    if not isinstance(value, dict) or set(value) - {"identified", "cli_version", "requested_model", "effort", "counters"}:
        invalid()
    for key, item in value.items():
        if key == "identified":
            if item is not True:
                invalid()
        elif key == "counters":
            metrics(item)
        elif item is not None and identifier(item) != item:
            invalid()


def metrics(value):
    exact(value, METRICS)
    for item in value.values():
        count(item)


def cursor(value, empty=False):
    if empty and value == {}:
        return
    exact(value, {"identity", "offset", "prefix_len", "prefix", "anchor"})
    if (not isinstance(value["identity"], list) or len(value["identity"]) != 2
            or any(type(n) is not int or n < 0 for n in value["identity"])
            or type(value["offset"]) is not int or not 0 <= value["offset"] <= 2**63 - 1
            or type(value["prefix_len"]) is not int or not 0 <= value["prefix_len"] <= min(256, value["offset"])):
        invalid()
    if any(not isinstance(value[k], str) or not re.fullmatch(r"[0-9a-f]{64}", value[k]) for k in ("prefix", "anchor")):
        invalid()


def validate(page):
    exact(page, {"version", "cursor", "parser", "samples", "quota", "status"})
    if type(page["version"]) is not int or page["version"] != VERSION or page["status"] not in {"current", "catching_up"}:
        invalid()
    cursor(page["cursor"])
    state(page["parser"])
    if not isinstance(page["samples"], list) or len(page["samples"]) > PAGE_LINES:
        invalid()
    for sample in page["samples"]:
        exact(sample, {"key", "at", "metrics", "actual_model", "requested_model", "effort", "cli_version"})
        if identifier(sample["key"]) != sample["key"] or sample["key"] is None:
            invalid()
        if timestamp(sample["at"]) != sample["at"]:
            invalid()
        metrics(sample["metrics"])
        for key in ("actual_model", "requested_model", "effort", "cli_version"):
            if sample[key] is not None and identifier(sample[key]) != sample[key]:
                invalid()
    rate = page["quota"]
    if rate is not None:
        exact(rate, {"observed_at", "limit_id", "windows"})
        if timestamp(rate["observed_at"]) != rate["observed_at"]:
            invalid()
        if not isinstance(rate["windows"], list) or not 1 <= len(rate["windows"]) <= 2:
            invalid()
        raw = {"limit_id": rate["limit_id"]}
        for window in rate["windows"]:
            exact(window, {"window", "used_percent", "window_minutes", "resets_at"})
            if window["window"] not in {"primary", "secondary"} or window["window"] in raw:
                invalid()
            raw[window["window"]] = {k: v for k, v in window.items() if k != "window"}
        if quota(raw, rate["observed_at"]) != rate:
            invalid()
    return page


def execute(binding, request):
    # The source grant is embedded in the fixed helper, never selected from stdin.
    exact(request, {"version", "cursor", "parser"})
    if type(request["version"]) is not int or request["version"] != VERSION:
        invalid()
    cursor(request["cursor"], empty=True)
    state(request["parser"])
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        raise UsageInvalid("unsafe_file")
    source = SimpleNamespace(**{**binding, "path": Path(binding["path"])})
    return validate({"version": VERSION, **read_page(source, request["cursor"], request["parser"])})


def main(binding):
    try:
        raw = sys.stdin.buffer.read(REQUEST_BYTES + 1)
        if len(raw) > REQUEST_BYTES:
            invalid()
        result = execute(binding, json.loads(raw))
        output = json.dumps(result, separators=(",", ":")).encode()
        if len(output) > REPLY_BYTES:
            invalid()
    except UsageInvalid as exc:
        output = json.dumps({"version": VERSION, "error": str(exc) if str(exc) in ERRORS else "invalid_reply"}).encode()
    except OSError:
        output = json.dumps({"version": VERSION, "error": "source_unavailable"}).encode()
    except (ValueError, TypeError, KeyError, RecursionError):
        output = json.dumps({"version": VERSION, "error": "invalid_reply"}).encode()
    sys.stdout.buffer.write(output)
