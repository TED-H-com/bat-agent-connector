"""Local consistency of producer claims and their covered bytes, not release approval.

Receipts are observations, not signatures. Source receipts cover named source
bytes; they cannot stand in for an installer, running process or signed feed.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from importlib import metadata
from pathlib import Path, PurePosixPath

from bat_agent_connector.build_identity import _commit
from bat_agent_connector.update_install import digest_files, tomllib, verify_source_package
from bat_agent_connector.update_release import UpdateError
from scripts.check_product_site import read_screenshot_manifest, screenshot_errors, source_entry

COMPONENTS = ("wheel", "sdist", "dashboard", "managed_runtime", "screenshots", "readme", "site", "desktop", "site_demo")
MAX_BYTES = 512 * 1024 * 1024
MAX_TREE = 128 * 1024 * 1024
READ_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError,
               zipfile.BadZipFile, tarfile.TarError, subprocess.SubprocessError)


class EvidenceError(ValueError):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


def _require(condition, reason, status="unknown"):
    if not condition:
        raise EvidenceError(status, reason)


def _digest(path):
    _require(path.is_file() and not path.is_symlink(), "ARTIFACT_UNAVAILABLE")
    value, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            _require(size <= MAX_BYTES, "ARTIFACT_SIZE_LIMIT")
            value.update(block)
    return value.hexdigest()


def _safe_name(name):
    path = PurePosixPath(name)
    _require(bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name,
             "ARCHIVE_PATH_INVALID")
    return name


def _archive(data, *, wheel=False):
    """Bounded member reads, no extractall, links or executable imports."""
    files, size = {}, 0
    with zipfile.ZipFile(io.BytesIO(data)) if wheel else tarfile.open(fileobj=io.BytesIO(data)) as archive:
        entries = archive.infolist() if wheel else archive.getmembers()
        _require(len(entries) <= 10000, "ARCHIVE_SIZE_LIMIT")
        for item in entries:
            name = _safe_name(item.filename if wheel else item.name)
            if item.is_dir() if wheel else item.isdir():
                continue
            regular = stat.S_IFMT(item.external_attr >> 16) != stat.S_IFLNK if wheel else item.isfile()
            _require(regular and name not in files, "ARCHIVE_MEMBER_INVALID")
            size += item.file_size if wheel else item.size
            _require(size <= MAX_TREE, "ARCHIVE_SIZE_LIMIT")
            files[name] = archive.read(item) if wheel else archive.extractfile(item).read()
    return files


def _git(source, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    git = shutil.which("git")
    _require(git is not None, "CANDIDATE_SOURCE_UNAVAILABLE")
    with tempfile.TemporaryFile(dir=os.environ.get("TMPDIR")) as output:
        result = subprocess.run([git, "-C", str(source), *args], env=env, stdout=output,  # noqa: S603 -- fixed read-only Git verbs
                                stderr=subprocess.DEVNULL, timeout=30)
        _require(result.returncode == 0, "CANDIDATE_SOURCE_UNAVAILABLE")
        _require(os.fstat(output.fileno()).st_size <= MAX_TREE, "SOURCE_SIZE_LIMIT")
        output.seek(0)
        return output.read()


def _json(data):
    document = json.loads(data)
    _require(isinstance(document, dict), "PRODUCER_RECORD_INVALID")
    return document


def _runtime_document(path):
    _require(path.is_file() and not path.is_symlink(), "ARTIFACT_UNAVAILABLE")
    with path.open("rb") as stream:
        data = stream.read(16385)
    _require(len(data) <= 16384, "PRODUCER_RECORD_INVALID")
    return _json(data)


def _hashes(files):
    return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}


def _package(path, wheel):
    _digest(path)
    with path.open("rb") as stream:
        data = stream.read(MAX_TREE + 1)
    _require(len(data) <= MAX_TREE, "ARCHIVE_SIZE_LIMIT")
    files = _archive(data, wheel=wheel)
    if wheel:
        package = "bat_agent_connector/"
        roots = {name.split("/")[0] for name in files if name.split("/")[0].endswith(".dist-info")}
        _require(len(roots) == 1, "PACKAGE_METADATA_UNAVAILABLE")
        meta = next(iter(roots)) + "/METADATA"
    else:
        roots = {name.split("/")[0] for name in files}
        _require(len(roots) == 1, "PACKAGE_METADATA_UNAVAILABLE")
        base = next(iter(roots)) + "/"
        package, meta = base + "src/bat_agent_connector/", base + "PKG-INFO"
    # Reuse importlib's distribution metadata reader without importing the package.
    class ArchiveDistribution(metadata.Distribution):
        def read_text(self, filename):
            return files[meta].decode("utf-8") if filename in {"METADATA", "PKG-INFO"} else None

        def locate_file(self, filename):
            raise NotImplementedError  # No filesystem or executable imports.

    version = ArchiveDistribution().version
    info = _json(files[package + "build-info.json"]) if package + "build-info.json" in files else {}
    _require(set(info) <= {"commit"}, "PRODUCER_RECORD_INVALID")
    return files, package, version, _commit(info.get("commit"))


def _source_files(component, root):
    if component == "readme":
        names = ("README.md", "README.zh-TW.md")
    else:
        tracked = _git(root, "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", "site").decode().split("\0")
        names = [name for name in tracked if name and name.split("/")[1] != "images"]
    _require(names, "ARTIFACT_UNAVAILABLE")
    files, size = {}, 0
    _require(len(names) <= 10000, "SOURCE_SIZE_LIMIT")
    for name in names:
        path = root / name
        _require(path.is_file() and not path.is_symlink(), "ARTIFACT_UNAVAILABLE")
        with (root / name).open("rb") as stream:
            data = stream.read(MAX_TREE - size + 1)
        size += len(data)
        _require(size <= MAX_TREE, "SOURCE_SIZE_LIMIT")
        files[name] = data
    return files


def _inspect(component, artifact):
    if component in {"wheel", "sdist", "dashboard"}:
        files, prefix, version, commit = _package(artifact, component != "sdist")
        dashboard = {name[len(prefix + "dashboard/"):]: data for name, data in files.items()
                     if name.startswith(prefix + "dashboard/")}
        digest = digest_files(_hashes(dashboard)) if component == "dashboard" else _digest(artifact)
        coverage = "wheel dashboard bytes" if component == "dashboard" else "package archive bytes"
    elif component == "managed_runtime":
        doc = _runtime_document(artifact)
        _require(type(doc.get("protocol")) is int and doc["protocol"] == 1, "PRODUCER_RECORD_INVALID")
        _require(type(doc.get("size")) is int and 0 < doc["size"] <= MAX_BYTES, "PRODUCER_RECORD_INVALID")
        version, commit = doc.get("runtime_version"), _commit(doc.get("build_commit"))
        payload = artifact.parent / "runtime.gz"
        payload_digest = _digest(payload)
        _require(payload_digest == doc.get("payload_sha256"), "RUNTIME_PAYLOAD_DIFFERS", "differs")
        value, size = hashlib.sha256(), 0
        with gzip.open(payload, "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                _require(size <= MAX_BYTES, "ARTIFACT_SIZE_LIMIT")
                value.update(block)
        _require(value.hexdigest() == doc.get("sha256") and size == doc.get("size"), "RUNTIME_CONTENT_DIFFERS", "differs")
        digest = digest_files({"manifest.json": _digest(artifact), "runtime.gz": payload_digest})
        coverage = "runtime manifest and compressed payload"
    elif component == "screenshots":
        doc = read_screenshot_manifest(artifact)
        commit, version = _commit(doc.get("frontend_source_commit")), doc["schema"]
        _require(commit is not None, "CLAIM_UNAVAILABLE")
        _require(not screenshot_errors(doc, artifact.parent), "SCREENSHOT_CONTENT_DIFFERS", "differs")
        digest = digest_files({"manifest.json": _digest(artifact),
                               **{row["file"]: _digest(artifact.parent / row["file"]) for row in doc["images"]}})
        coverage = "capture manifest and listed PNG bytes"
    elif component in {"readme", "site"}:
        commit = _commit(_git(artifact, "rev-parse", "HEAD").decode().strip())
        version = tomllib.loads((artifact / "pyproject.toml").read_text())["project"]["version"]
        digest = digest_files(_hashes(_source_files(component, artifact)))
        coverage = "README source bytes" if component == "readme" else "tracked site source bytes excluding images"
    else:
        raise EvidenceError("unknown", "OFFLINE_PRODUCER_CLAIM_UNAVAILABLE")
    return {"version": version, "commit": commit, "sha256": digest, "coverage": coverage}


def receipt(component, artifact):
    """Read a producer's claim and bind its exact, explicitly scoped bytes."""
    artifact = Path(artifact).absolute()
    try:
        observed = _inspect(component, artifact)
    except EvidenceError:
        raise
    except READ_ERRORS:
        raise EvidenceError("unknown", "ARTIFACT_OR_PRODUCER_RECORD_UNAVAILABLE") from None
    return {"format": 1, "component": component, "artifact": str(artifact), **observed}


def _write_files(directory, files):
    for name, data in files.items():
        path = directory / _safe_name(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _check_source(component, artifact, source_files, candidate, source_repo):
    if component in {"wheel", "sdist", "dashboard"}:
        files, prefix, version, _ = _package(artifact, component != "sdist")
        expected_prefix = "src/bat_agent_connector/"
        expected = {name[len(expected_prefix):]: data for name, data in source_files.items() if name.startswith(expected_prefix)}
        actual = {name[len(prefix):]: data for name, data in files.items() if name.startswith(prefix)}
        if component == "dashboard":
            expected = {k: v for k, v in expected.items() if k.startswith("dashboard/")}
            actual = {k: v for k, v in actual.items() if k.startswith("dashboard/")}
            _require(expected and expected == actual, "DASHBOARD_SOURCE_DIFFERS", "differs")
        else:
            _require(version == tomllib.loads(source_files["pyproject.toml"].decode())["project"]["version"],
                     "PACKAGE_VERSION_DIFFERS", "differs")
            with tempfile.TemporaryDirectory(prefix="release-source-", dir=os.environ.get("TMPDIR")) as scratch:
                source, package = Path(scratch) / "source", Path(scratch) / "package"
                _write_files(source, expected)
                _write_files(package, actual)
                verify_source_package(source, package, candidate)
            skill = (prefix + "skill/SKILL.md") if component == "wheel" else prefix.split("src/")[0] + "skills/bat-agent-connector/SKILL.md"
            _require(files.get(skill) == source_files["skills/bat-agent-connector/SKILL.md"], "PACKAGE_SKILL_DIFFERS", "differs")
    elif component == "screenshots":
        doc = read_screenshot_manifest(artifact)
        tree = _git(source_repo, "rev-parse", candidate + ":desktop/src").decode().strip()
        _require(doc.get("frontend_source_tree") == tree, "SCREENSHOT_TREE_DIFFERS", "differs")
        for row in doc["source_files"]:
            name = _safe_name(row["file"])
            _require(name in source_files and source_entry(name, source_files[name]) == row,
                     "SCREENSHOT_SOURCE_DIFFERS", "differs")
    elif component == "managed_runtime":
        doc = _runtime_document(artifact)
        _require(doc["runtime_version"] == tomllib.loads(source_files["pyproject.toml"].decode())["project"]["version"],
                 "RUNTIME_VERSION_DIFFERS", "differs")
    elif component in {"readme", "site"}:
        actual = _source_files(component, artifact)
        if component == "readme":
            expected = {name: source_files[name] for name in ("README.md", "README.zh-TW.md")}
        else:
            expected = {name: data for name, data in source_files.items() if name.startswith("site/")
                        and name.split("/")[1] != "images"}
        _require(actual == expected, "DOCUMENT_SOURCE_DIFFERS", "differs")


def validate(candidate, receipts, *, source, required=COMPONENTS):
    """Every required component must match; missing/unsupported claims never pass."""
    required = list(dict.fromkeys(required))
    _require(required and all(name in COMPONENTS for name in required), "REQUIRED_COMPONENTS_INVALID")
    source_files, source_error = None, None
    try:
        _require(_commit(candidate) is not None, "CANDIDATE_INVALID")
        source_files = _archive(_git(Path(source), "archive", "--format=tar", candidate))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        source_error = exc.reason if isinstance(exc, EvidenceError) else "CANDIDATE_SOURCE_UNAVAILABLE"
    rows = []
    for name in required:
        row = {"component": name, "status": "unknown", "reason": "MISSING_RECEIPT"}
        try:
            _require(source_error != "CANDIDATE_INVALID", "CANDIDATE_INVALID")
            matches = [r for r in receipts if isinstance(r, dict) and r.get("component") == name]
            _require(matches, "MISSING_RECEIPT")
            _require(len(matches) == 1, "DUPLICATE_RECEIPT")
            claim = matches[0]
            _require(name not in {"desktop", "site_demo"}, "OFFLINE_PRODUCER_CLAIM_UNAVAILABLE")
            _require(type(claim.get("format")) is int and claim["format"] == 1, "RECEIPT_FORMAT_INVALID")
            _require(_commit(claim.get("commit")) is not None, "CLAIM_UNAVAILABLE")
            _require(isinstance(claim.get("version"), str) and 0 < len(claim["version"]) <= 128
                     and claim["version"].strip() == claim["version"], "VERSION_UNAVAILABLE")
            _require(isinstance(claim.get("sha256"), str) and len(claim["sha256"]) == 64
                     and _commit(claim["sha256"]) is not None, "DIGEST_UNAVAILABLE")
            _require(isinstance(claim.get("artifact"), str), "ARTIFACT_UNAVAILABLE")
            artifact = Path(claim["artifact"])
            if name in {"wheel", "sdist"}:
                _require(_digest(artifact) == claim["sha256"], "ARTIFACT_DIGEST_DIFFERS", "differs")
            observed = _inspect(name, artifact)
            row.update(observed)
            _require(observed["commit"] is not None, "CLAIM_UNAVAILABLE")
            _require(observed["sha256"] == claim["sha256"], "ARTIFACT_DIGEST_DIFFERS", "differs")
            _require(observed == {key: claim.get(key) for key in observed}, "RECEIPT_CLAIM_DIFFERS", "differs")
            _require(observed["commit"] == candidate, "COMMIT_DIFFERS", "differs")
            _require(source_files is not None, source_error)
            _check_source(name, artifact, source_files, candidate, Path(source))
            row.update(status="matches", reason="CLAIM_AND_BYTES_MATCH")
        except EvidenceError as exc:
            row.update(status=exc.status, reason=exc.reason)
        except UpdateError:
            row.update(status="differs", reason="PACKAGE_SOURCE_DIFFERS")
        except READ_ERRORS:
            row.update(status="unknown", reason="ARTIFACT_OR_PRODUCER_RECORD_UNAVAILABLE")
        rows.append(row)
    refusals = [{k: row[k] for k in ("component", "status", "reason")} for row in rows if row["status"] != "matches"]
    return {"format": 1, "candidate": candidate, "required": required, "scope": "required_receipts",
            "one_candidate": not refusals, "components": rows, "refusals": refusals}
