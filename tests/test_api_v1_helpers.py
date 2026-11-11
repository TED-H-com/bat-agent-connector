"""Exact authority grammar and its API/static handler boundaries."""

import asyncio
import json

import pytest

from bat_agent_connector import api_v1
from tests import test_api_v1 as api_tests

daemon = api_tests.daemon
served = api_tests.served

VALID_AUTHORITIES = (
    "localhost", "LOCALHOST", "LocalHost:8080", "localhost:1", "localhost:65535",
    "localhost:00001", "127.0.0.1", "127.0.0.1:9090", "127.0.0.0", "127.255.255.255:65535",
    "127.1.20.255", "[::1]", "[::1]:80", "[::1]:1", "[::1]:65535",
)
INVALID_AUTHORITIES = (
    "", "example.test", "192.0.2.1", "126.255.255.255", "128.0.0.0", "localhost.localdomain",
    "localhost.", "LOCALHOST.", "127.0.0.1.", "[::1]suffix", "[::1]evil:80", "[::1",
    "[::1:80", "::1", "::1:80", "[0:0:0:0:0:0:0:1]", "[::01]", "[::ffff:127.0.0.1]",
    "[::1%lo]", "[[::1]]", "[localhost]", "[127.0.0.1]", "localhost:80:90", "[::1]::80",
    "localhost:", "[::1]:", "127.0.0.1:", "localhost:abc", "[::1]:abc",
    "localhost:3000.evil.test", "127.0.0.1:9090.evil.test", "localhost:0", "localhost:00000",
    "localhost:65536", "[::1]:99999", "127.0.0.1:100000", "localhost:000001",
    "localhost:+80", "localhost:-1", "localhost:８０", "localhost:٨٠",
    "127.999.999.999", "127.256.0.1", "127.0.256.1", "127.0.0.256", "127.00.0.1",
    "127.0.01.1", "127.0.0.001", "0127.0.0.1", "127.1", "127.0.1", "127.0.0.0.1",
    "2130706433", "0x7f000001", "0x7f.0.0.1", "0177.0.0.1", "127.٠.0.1", "127.０.0.1",
    "user@localhost", "user:pass@[::1]", "localhost@evil.test", "%6cocalhost", "127%2e0.0.1",
    "localhoſt", " localhost", "localhost ", "local host", "local\thost", "localhost\n",
    "\r\nlocalhost", "localhost\x00", "localhost\x7f", "localhost\u00a0", "localhost: 80",
    "localhost/", "localhost?", "localhost#", "localhost\\",
)
INVALID_ORIGINS = (
    "", "null", "localhost", "//localhost", "http:localhost", "http:///localhost", "ftp://localhost",
    "ws://localhost", "https://example.test", "http://localhost/", "http://localhost/path",
    "http://localhost?", "http://localhost?x=1", "http://localhost#", "http://localhost#fragment",
    "http://user:pass@localhost", "http://[", "http://[not-ipv6]", "http://[v1.invalid]",
    " http://localhost", "\x00http://localhost", "ht\ttp://localhost", "http://local\nhost",
    "http://local\rhost", "http://localhost\n", "http://localhost\uff0fexample.test",
    "http://localhost http://127.0.0.1",
)


@pytest.mark.parametrize("authority", VALID_AUTHORITIES)
def test_host_is_loopback(authority):
    assert api_v1._host_is_loopback(authority)


@pytest.mark.parametrize("authority", INVALID_AUTHORITIES)
def test_host_refuses_invalid_authority(authority):
    assert not api_v1._host_is_loopback(authority)


@pytest.mark.parametrize("scheme", ("http", "https", "HTTP", "HtTpS"))
@pytest.mark.parametrize("authority", VALID_AUTHORITIES)
def test_origin_accepts_loopback(scheme, authority):
    assert api_v1._origin_ok(f"{scheme}://{authority}", ())


@pytest.mark.parametrize("origin", INVALID_ORIGINS + tuple(f"http://{host}" for host in INVALID_AUTHORITIES))
def test_origin_refuses_invalid_syntax(origin):
    assert not api_v1._origin_ok(origin, ())


@pytest.mark.parametrize("origin", ("https://hub.example.test", "tauri://localhost", "http://["))
def test_origin_exact_configured_exception_is_preserved(origin):
    assert api_v1._origin_ok(origin, (origin,))
    assert not api_v1._origin_ok(origin + "/", (origin,))


@pytest.mark.parametrize(("path", "expected"), (
    ("/", True), ("/dashboard", True), ("/dashboard/", True), ("/dashboard/something", True),
    ("/api/v1/sessions", False), ("/api/v2/something", False), ("/api/", False), ("/index.html", False),
))
def test_is_dashboard_path(path, expected):
    assert api_v1.is_dashboard_path(path) is expected


class ResponseWriter:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass

    def response(self):
        head, _, body = bytes(self.data).partition(b"\r\n\r\n")
        return int(head.split(b" ")[1]), head, body


@pytest.mark.parametrize("dashboard", (False, True))
async def test_handlers_validate_complete_host_values(daemon, dashboard):
    # Direct handler calls preserve controls/Unicode and surrounding whitespace;
    # the daemon HTTP decoder rejects non-ASCII and trims header framing whitespace.
    for authority in VALID_AUTHORITIES + INVALID_AUTHORITIES:
        writer = ResponseWriter()
        headers = {"host": authority}
        if dashboard:
            await daemon.api.dashboard("GET", "/dashboard/", headers, writer)
        else:
            await daemon.api.handle("GET", "/api/v1/version", headers, asyncio.StreamReader(), writer)
        status, _, body = writer.response()
        assert status == (200 if authority in VALID_AUTHORITIES else 400), repr(authority)
        if status == 400:
            if dashboard:
                assert body == b"Host must be a loopback address\n"
            else:
                assert json.loads(body)["error"]["code"] == "BAD_HOST"


async def test_api_handler_refuses_malformed_origins(daemon):
    # Empty Origin keeps the existing absent-header semantics in the handler.
    origins = INVALID_ORIGINS[1:] + tuple(f"http://{host}" for host in INVALID_AUTHORITIES)
    for origin in origins:
        writer = ResponseWriter()
        await daemon.api.handle("GET", "/api/v1/version", {"host": "localhost", "origin": origin},
                                asyncio.StreamReader(), writer)
        status, head, body = writer.response()
        assert status == 403, repr(origin)
        assert json.loads(body)["error"]["code"] == "BAD_ORIGIN"
        assert b"Access-Control-Allow-Origin" not in head


@pytest.mark.parametrize("path", ("/api/v1/version", "/dashboard/"))
async def test_http_host_authority_boundaries(served, path):
    _, port = served
    for authority in VALID_AUTHORITIES + INVALID_AUTHORITIES:
        if not authority or not authority.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in authority):
            continue  # Covered directly above, without HTTP framing/decoding transformations.
        status, _, body = await api_tests.raw_get(port, path, host=authority)
        assert status == (200 if authority in VALID_AUTHORITIES else 400), repr(authority)
        if status == 400 and path.startswith("/api/"):
            assert json.loads(body)["error"]["code"] == "BAD_HOST"


async def test_http_origin_authority_boundaries(served):
    daemon, port = served
    daemon.api.allowed_origins = ("https://hub.example.test",)
    for origin in ("https://hub.example.test",) + tuple(f"https://{host}" for host in VALID_AUTHORITIES):
        status, _ = await api_tests.http(port, "GET", "/api/v1/version", headers={"Origin": origin})
        assert status == 200, origin
    for origin in INVALID_ORIGINS[1:] + tuple(f"http://{host}" for host in INVALID_AUTHORITIES):
        if not origin.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in origin):
            continue
        status, body = await api_tests.http(port, "GET", "/api/v1/version", headers={"Origin": origin})
        assert status == 403 and body["error"]["code"] == "BAD_ORIGIN", repr(origin)


async def test_http_authority_controls_are_not_normalized_away(served):
    _, port = served
    for control in ("\r", "\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x1f"):
        for authority in (control + "localhost", "localhost" + control):
            for path in ("/api/v1/version", "/dashboard/"):
                status, _, body = await api_tests.raw_get(port, path, host=authority)
                assert status == 400, (path, repr(authority))
                if path.startswith("/api/"):
                    assert json.loads(body)["error"]["code"] == "BAD_HOST"
        for origin in (control + "http://localhost", "http://localhost" + control):
            status, body = await api_tests.http(port, "GET", "/api/v1/version", headers={"Origin": origin})
            assert status == 403 and body["error"]["code"] == "BAD_ORIGIN", repr(origin)
    # Ordinary HTTP field framing is not part of the authority value.
    for path in ("/api/v1/version", "/dashboard/"):
        assert (await api_tests.raw_get(port, path, host=" \tlocalhost\t "))[0] == 200
    assert (await api_tests.http(port, "GET", "/api/v1/version",
                                 headers={"Origin": " \thttp://localhost\t "}))[0] == 200
