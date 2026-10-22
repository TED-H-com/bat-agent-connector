"""Synthetic index inputs only; expectations come from the real listing reader."""
from __future__ import annotations

import json


def profile_index_inputs():
    remote = {"id": "remote-1", "name": "Fixture BAT", "type": "remote",
              "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
              "remoteHost": "example.test", "remotePort": 9876,
              "remoteFingerprint": "AA" * 32, "remoteProfileId": "workspace-1",
              "remoteProfileName": "Fixture workspace"}
    rows = []

    def add(ident, profiles, **root):
        rows.append({"id": ident, "index": json.dumps({"profiles": profiles, "activeProfileIds": [], **root})})

    add("complete-remote", [remote])
    for key in ("remoteProfileId", "name", "id", "remoteHost", "remotePort", "remoteFingerprint", "type"):
        add("missing-" + key, [{k: v for k, v in remote.items() if k != key}])
    for ident, changes in [
        ("ipv6", {"remoteHost": "2001:db8::7"}),
        ("ipv6-bracketed", {"remoteHost": "[2001:db8::7]"}),
        ("ipv6-invalid", {"remoteHost": "2001:::7"}),
        ("ipv6-scope", {"remoteHost": "fe80::1%eth0"}),
        ("host-empty-brackets", {"remoteHost": "[]"}),
        ("host-bracketed-domain", {"remoteHost": "[example.test]"}),
        ("host-userinfo", {"remoteHost": "user@example.test"}),
        ("host-path", {"remoteHost": "example.test/path"}),
        ("host-whitespace", {"remoteHost": "example. test"}),
        ("port-min", {"remotePort": 1}), ("port-max", {"remotePort": 65535}),
        ("port-zero", {"remotePort": 0}), ("port-over", {"remotePort": 65536}),
        ("port-negative", {"remotePort": -1}), ("port-string", {"remotePort": "9876"}),
        ("port-bool", {"remotePort": True}), ("port-float", {"remotePort": 9876.5}),
        ("port-null", {"remotePort": None}),
        ("port-text-native-divergence", {"remotePort": "0" * 5000 + "1"}),
        ("port-text-at-bound", {"remotePort": "0" * 255 + "1"}),
        ("port-text-over-bound", {"remotePort": "0" * 256 + "1"}),
        ("fingerprint-colons", {"remoteFingerprint": ":".join(["aB"] * 32)}),
        ("fingerprint-prefix-spaces", {"remoteFingerprint": "sha256:" + " ".join(["ab"] * 32)}),
        ("fingerprint-invalid", {"remoteFingerprint": "zz" * 32}),
        ("fingerprint-overlong", {"remoteFingerprint": "a" * 201}),
        ("non-remote", {"type": "local"}),
        ("name-fallback", {"name": ""}), ("remote-id-fallback", {"remoteProfileId": None}),
        ("unicode-name", {"name": "繁體🔐" * 128}),
        ("overlong-id", {"id": "x" * 257}),
        ("overlong-name", {"name": "x" * 257}),
        ("overlong-host", {"remoteHost": "x" * 257}),
        ("overlong-remote-id", {"remoteProfileId": "x" * 257}),
        ("control-name", {"name": "unsafe\nname"}),
        ("control-host", {"remoteHost": "unsafe\x00host"}),
        ("control-del", {"name": "unsafe\x7fname"}),
        ("control-c1", {"remoteProfileId": "unsafe\x85profile"}),
        ("unknown-profile-field", {"futureDisplayOption": {"enabled": True}}),
    ]:
        add(ident, [{**remote, **changes}])
    add("partial-keeps-valid", [{"id": "incomplete", "type": "remote"}, remote, {"id": "local-1", "type": "local"}])
    add("duplicate-ids", [remote, {**remote, "name": "Other"}, {**remote, "id": "unique"}])
    add("case-competing-ids", [remote, {**remote, "id": "REMOTE-1"}])
    add("unknown-root-field", [remote], futureSetting={"enabled": True})
    add("empty", [])
    add("non-object-entry", [None, 1, "entry", remote])
    add("count-at-bound", [{**remote, "id": f"remote-{i}"} for i in range(200)])
    add("count-over-bound", [{**remote, "id": f"remote-{i}"} for i in range(201)])
    for ident, contents in [
        ("non-object-root", "[]"), ("null-root", "null"), ("missing-profiles", "{}"),
        ("non-array-profiles", '{"profiles":{}}'), ("invalid-json", "{"),
        ("bom", '\ufeff{"profiles":[]}'),
        ("duplicate-json-key", '{"profiles":[],"profiles":[]}'),
        ("case-competing-json-key", '{"profiles":[],"Profiles":[]}'),
        ("depth-bound", '{"profiles":[],"extra":' + "[" * 127 + "0" + "]" * 127 + "}"),
    ]:
        rows.append({"id": ident, "index": contents})
    return rows
