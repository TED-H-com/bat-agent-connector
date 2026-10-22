"""The real preview producer is the shared browser contract fixture."""
from __future__ import annotations

import json
import os
from pathlib import Path

from bat_agent_connector import managed_setup as setup
from bat_agent_connector import platform_files
from tests import test_managed_setup as producer

installed = producer.installed
GOLDEN = Path(__file__).parent / "fixtures/managed-setup-preview.json"


async def test_setup_preview_matches_shared_golden(installed):
    daemon, principal, root = installed
    ref = setup.stage_secret(daemon, principal, {"kind": "bat", "value": "synthetic-bat-secret"})["secret_ref"]
    directory = root / "profiles"
    directory.mkdir(mode=0o700)
    (directory / "index.json").write_text(json.dumps({"profiles": [{"id": "profile-demo", "name": "host-a",
        "type": "remote", "remoteHost": "host-a.example.invalid", "remotePort": 9876,
        "remoteFingerprint": "A" * 64, "remoteProfileId": "default"}]}))
    platform_files.atomic_write(directory / "remote-tokens.enc.json", json.dumps({"enc": False,
        "data": json.dumps({"tokens": {"profile-demo": "synthetic-bat-secret"}})}).encode())
    path = root / "config/hosts.toml"
    document = setup.tomllib.loads(path.read_text())
    document["bat"] = {"profiles_dir": str(directory)}
    platform_files.atomic_write(path, setup._encode(document))
    revision = setup._configuration(daemon)[3]
    result = {}
    for kind, connection in {"staged": {"url": "wss://host-a.example.invalid:9876", "fingerprint": "A" * 64,
            "profile_id": "default", "secret_ref": ref}, "profile": {"import_profile_id": "profile-demo"}}.items():
        request = {"action": "setup.host", "target": {"host": "bat"}, "params": {**connection,
            "writes": False, "orchestrate": False, "shared_clone_worktrees": False, "managed_roots": []},
            "preconditions": {"config_revision": revision}}
        response = setup.preview(daemon, principal, request)
        preview = response["preview"]
        normalized = json.dumps({"request": request, **response}).replace(ref, "setup_" + "b" * 32)
        for actual, example in [(revision, "a" * 64), (preview["after_revision"], "b" * 64), (preview["review_digest"], "c" * 64)]:
            normalized = normalized.replace(actual, example)
        result[kind] = json.loads(normalized)
    if os.environ.get("BATC_UPDATE_SETUP_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    assert result == json.loads(GOLDEN.read_text())
