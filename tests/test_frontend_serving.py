"""The served identity follows the exact stored dashboard bytes, without a cache."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from bat_agent_connector import api_auth, api_v1
from bat_agent_connector.config import Config
from bat_agent_connector.task_daemon import TaskDaemon

PLACEHOLDER = b'__BATC_FRONTEND_DIGEST_PLACEHOLDER______________________________'


@pytest.fixture
async def frontend(tmp_path, monkeypatch):
    root = tmp_path / "package"
    directory = root / "dashboard"
    directory.mkdir(parents=True)
    files = {"index.html": b"<!doctype html><script src='/dashboard/app.js'></script>",
             "app.js": b'const loaded="' + PLACEHOLDER + b'";',
             "app.css": b"body { color: black; }\n", "i18n.js": b"// compatibility\n"}
    for name, body in files.items():
        (directory / name).write_bytes(body)
    # Replace this module's resolver, not the shared stdlib module/function.
    monkeypatch.setattr(api_v1, "resources", SimpleNamespace(files=lambda _: root))
    daemon = TaskDaemon(Config(hosts={}), tmp_path / "journal.db")
    try:
        yield daemon.api, directory, files
    finally:
        await daemon.fleet.close()
        await daemon.inventory.close()
        daemon.journal.close()


def expected_digest(files):
    digest = hashlib.sha256(b"batc.frontend.files.v1\0")
    for name in sorted(files):
        body = files[name]
        digest.update(f"{len(name.encode())}:{name}\0{len(body)}\0".encode())
        digest.update(body)
    return digest.hexdigest()


async def response(api, name="app.js", method="GET"):
    class Writer:
        data = b""

        def write(self, value):
            self.data += value

        async def drain(self):
            pass

    writer = Writer()
    await api.dashboard(method, "/dashboard/" + name, {"host": "localhost"}, writer)
    head, _, body = writer.data.partition(b"\r\n\r\n")
    headers = dict(line.lower().split(b": ", 1) for line in head.split(b"\r\n")[1:])
    assert head.startswith(b"HTTP/1.1 200")
    return headers, body


async def capabilities(api):
    status, caps = await api.capabilities(api_auth.Principal("fixture-operator", frozenset({"observe"})))
    assert status == 200
    return caps


async def test_substitution_matches_capabilities_and_head_length(frontend):
    api, _, files = frontend
    expected = expected_digest(files)
    headers, body = await response(api)
    assert len(PLACEHOLDER) == 64
    assert body == files["app.js"].replace(PLACEHOLDER, expected.encode(), 1)
    assert body.count(expected.encode()) == 1 and PLACEHOLDER not in body
    assert len(body) == len(files["app.js"]) == int(headers[b"content-length"])
    head_headers, head_body = await response(api, method="HEAD")
    assert head_headers[b"content-length"] == headers[b"content-length"] and head_body == b""
    assert (await capabilities(api))["frontend"] == {"digest": expected}


@pytest.mark.parametrize("count", [0, 2])
async def test_missing_or_duplicated_placeholder_is_unchanged_and_unknown(frontend, count):
    api, directory, _ = frontend
    stored = b'const values="' + b";".join([PLACEHOLDER] * count) + b'";'
    (directory / "app.js").write_bytes(stored)
    headers, body = await response(api)
    assert body == stored and int(headers[b"content-length"]) == len(stored)
    head_headers, head_body = await response(api, method="HEAD")
    assert head_headers[b"content-length"] == headers[b"content-length"] and head_body == b""
    assert (await capabilities(api))["frontend"] == {"digest": None}


@pytest.mark.parametrize("changed", ["app.css", "app.js", "index.html", "i18n.js"])
async def test_in_place_file_change_updates_the_next_response_and_capabilities(frontend, changed):
    api, directory, files = frontend
    _, first = await response(api)
    initial = (await capabilities(api))["frontend"]["digest"]
    files[changed] += b"\n// fixture change\n"
    (directory / changed).write_bytes(files[changed])
    expected = expected_digest(files)
    _, second = await response(api)
    assert expected != initial and expected.encode() in second and initial.encode() in first
    assert (await capabilities(api))["frontend"]["digest"] == expected


@pytest.mark.parametrize("name", ["index.html", "app.css", "i18n.js"])
async def test_other_files_are_byte_identical_to_disk(frontend, name):
    api, directory, _ = frontend
    _, body = await response(api, name)
    assert body == (directory / name).read_bytes()
