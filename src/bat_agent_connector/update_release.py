"""Bounded public release discovery; no user credentials or configurable feed."""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

REPOSITORY = "https://github.com/TED-H-com/bat-agent-connector"
API = "https://api.github.com/repos/TED-H-com/bat-agent-connector/releases?per_page=30"
PROTOCOL = 1
EPOCH = 1


class UpdateError(ValueError):
    """A public, credential-free diagnostic code."""


class EntrypointDirectoryError(UpdateError):
    """Stable code with actionable directory detail."""

    def __init__(self, directory):
        super().__init__("entrypoint_directory_not_owned")
        self.directory = str(directory)

    def diagnostic(self):
        return {"code": str(self), "directory": self.directory,
                "hint": "chmod g-w,o-w " + shlex.quote(self.directory)}


def require(condition, code):
    if not condition:
        raise UpdateError(code)


def version(value):
    require(isinstance(value, str) and re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value),
            "invalid_package_version")
    require(len(value) <= 30, "invalid_package_version")
    return tuple(map(int, value.split(".")))


def commit(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value), "invalid_source_commit")
    return value


def _allowed(url):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == "https" and parsed.port in (None, 443)
            and not parsed.username and not parsed.password and not parsed.fragment
            and parsed.hostname in {"api.github.com", "github.com", "release-assets.githubusercontent.com",
                                    "objects.githubusercontent.com"})


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(_allowed(newurl), "release_redirect_refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, *, limit=1024 * 1024):
    require(_allowed(url), "release_origin_refused")
    # No environment proxy, netrc, cookies or API token is used by this public feed.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _Redirect())
    # _allowed above and _Redirect enforce HTTPS and the fixed publisher hosts.
    request = urllib.request.Request(url, headers={"User-Agent": "batc-update/1", "Accept": "application/json"})  # noqa: S310
    started = time.monotonic()
    try:
        with opener.open(request, timeout=15) as response:
            require(response.status == 200, "release_http_status")
            data = bytearray()
            while True:
                require(time.monotonic() - started < 60, "release_download_timeout")
                chunk = response.read(min(65536, limit + 1 - len(data)))
                if not chunk:
                    return bytes(data)
                data.extend(chunk)
                require(len(data) <= limit, "release_response_too_large")
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError("release_network_unavailable") from exc


def manifest(release, raw):
    try:
        doc = json.loads(raw)
        tag = release["tag_name"]
        require(isinstance(tag, str) and re.fullmatch(r"v[0-9][A-Za-z0-9.+-]{0,95}", tag), "invalid_release_tag")
        require(not release.get("draft") and release.get("published_at"), "release_unpublished")
        require(doc["format"] == 1 and doc["repository"] == REPOSITORY and doc["release"] == tag,
                "release_manifest_mismatch")
        update = doc["connector_update"]
        require(update["protocol"] == PROTOCOL and update["compatibility_epoch"] == EPOCH,
                "release_incompatible")
        require(update["python"] == "3.13", "release_python_unsupported")
        ver = doc["versions"]["connector"]
        version(ver)
        require(tag == "v" + ver or tag.startswith("v" + ver + "-"), "release_version_mismatch")
        sha = commit(doc["source_commit"])
        skill = update["canonical_skill_sha256"]
        require(isinstance(skill, str) and re.fullmatch(r"[0-9a-f]{64}", skill), "release_skill_digest_invalid")
        require(doc["validation"]["status"] == "passed", "release_not_validated")
        return {"commit": sha, "version": ver, "tag": tag, "epoch": EPOCH,
                "skill_sha256": skill, "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                "prerelease": bool(release.get("prerelease"))}
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise UpdateError("release_manifest_invalid") from exc


def publication_time(value):
    """Optional display clock; never part of release acceptance or selection."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        return value if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None else None
    except ValueError:
        return None


def discover(channel, *, download=None, observation=None):
    require(channel in {"stable", "preview"}, "invalid_channel")
    download = fetch if download is None else download
    try:
        releases = json.loads(download(API))
        require(isinstance(releases, list) and len(releases) <= 30, "release_list_invalid")
        candidates = []
        for item in releases:
            if item.get("draft") or not item.get("published_at") or (channel == "stable" and item.get("prerelease")):
                continue
            tag = item.get("tag_name", "")
            if not re.fullmatch(r"v[0-9][A-Za-z0-9.+-]{0,95}", tag):
                continue
            assets = [a for a in item.get("assets", []) if a.get("name") == "release-manifest.json"]
            if len(assets) != 1:
                continue
            asset = assets[0]
            require(isinstance(asset.get("size"), int) and 0 < asset["size"] <= 1024 * 1024,
                    "release_manifest_size_invalid")
            raw = download(REPOSITORY + "/releases/download/" + urllib.parse.quote(tag, safe="")
                           + "/release-manifest.json")
            require(len(raw) == asset["size"], "release_manifest_size_mismatch")
            require(asset.get("digest") == "sha256:" + hashlib.sha256(raw).hexdigest(),
                    "release_manifest_digest_mismatch")
            doc = json.loads(raw)
            if "connector_update" not in doc:
                continue  # Older releases did not opt in to unattended installation.
            candidates.append((manifest(item, raw), item))
        candidates.sort(key=lambda entry: version(entry[0]["version"]), reverse=True)
        if candidates:
            best, item = candidates[0]
            require(all(r["commit"] == best["commit"] for r, _ in candidates if r["version"] == best["version"]),
                    "release_version_republished")
            if observation is not None:
                observation["published_at"] = publication_time(item.get("published_at"))
            return best
        if observation is not None:
            observation["published_at"] = None
        return None
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise UpdateError("release_list_invalid") from exc
