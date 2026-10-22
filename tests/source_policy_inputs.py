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
    for ident, host in [
        ("host-nfkc-solidus", "evil.test／good.test"),
        ("host-nfkc-colon", "10.0.0.5：9876"),
        ("host-nfkc-at", "user﹫evil.test"),
        ("host-ascii-control", "good.test"),
    ]:
        add(ident, [{**remote, "remoteHost": host}, {**remote, "id": "other-complete"}])
    rows.append({"id": "raw-utf8-chinese-name", "index": json.dumps({"profiles": [
        {**remote, "name": "測試連線"}]}, ensure_ascii=False)})
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


def format_error_store_inputs():
    inner = {
        "truncated-after-duplicate": '{"tokens":{"remote-1":"t"},"TOKENS":{},',
        "overflow-before-duplicate": '{"x":1e999,"tokens":{"remote-1":"t"},"TOKENS":{}}',
        "overflow-after-duplicate": '{"tokens":{},"TOKENS":{},"x":1e999}',
        "surrogate-before-duplicate": '{"x":"\\ud800","tokens":{"remote-1":"t"},"TOKENS":{}}',
        "deep-before-duplicate": '{"x":' + '[' * 130 + ']' * 130 + ',"tokens":{"remote-1":"t"},"TOKENS":{}}',
        "overwritten-surrogate": '{"x":"\\ud800","x":0,"tokens":{"remote-1":"t"}}',
        "overwritten-depth": '{"x":' + '[' * 127 + ']' * 127 + ',"x":0,"tokens":{"remote-1":"t"}}',
    }
    rows = [{"id": "format-precedence-" + name, "profile_id": "remote-1",
             "store": json.dumps({"enc": False, "data": value})} for name, value in inner.items()]
    rows.append({"id": "format-precedence-outer-overflow", "profile_id": "remote-1",
                 "store": '{"x":1e999,"enc":false,"ENC":false,"data":"{}"}'})
    return rows


def token_file_inputs():
    store = json.dumps({"enc": False, "data": json.dumps({"tokens": {"remote-1": "synthetic-token"}})}).encode()
    return [
        {"id": "raw-invalid-utf8", "store_bytes": list(b'{"enc":false,"data":"\xff"}'), "mode": 0o600},
        *[{"id": f"posix-mode-{mode:04o}", "store_bytes": list(store), "mode": mode, "posix_only": True}
          for mode in (0o600, 0o644, 0o640, 0o604)],
    ]


def profile_file_inputs():
    return [{"id": name, "posix_only": name == "linked-profiles"} for name in (
        "missing-root", "missing-profiles", "missing-index", "linked-profiles", "valid-index", "invalid-index")]


def prepare_profile_files(root, row):
    """One disk layout shared by the producer and Python reader test."""
    directory = root / "BetterAgentTerminal" / "profiles"
    if row["id"] == "missing-root":
        return directory
    directory.parent.mkdir(parents=True)
    if row["id"] == "missing-profiles":
        return directory
    if row["id"] == "linked-profiles":
        actual = root / "actual-profiles"
        actual.mkdir()
        directory.symlink_to(actual, target_is_directory=True)
    else:
        directory.mkdir()
    if row["id"] != "missing-index":
        contents = next(r for r in profile_index_inputs() if r["id"] == "raw-utf8-chinese-name")["index"]
        (directory / "index.json").write_bytes(("{" if row["id"] == "invalid-index" else contents).encode("utf-8"))
    return directory
