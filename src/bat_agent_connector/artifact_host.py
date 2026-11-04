"""Restricted SSH byte adapter. It only runs our versioned helper, never caller scripts."""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path

from bat_agent_connector import hidden_process

from .operations import AmbiguousOutcome, OperationError

HELPER = Path(__file__).with_name("artifact_host_helper.py").read_text()
CAPTURE_HELPER = Path(__file__).with_name("artifact_capture_helper.py").read_text()


class ArtifactHost:
    def __init__(self, aliases: dict[str, str], timeout_s=300):
        self.aliases, self.timeout_s = dict(aliases), timeout_s
        self.readiness: dict[str, dict] = {}

    def available(self, host):
        alias = self.aliases.get(host)
        return bool(alias and not alias.startswith("-") and not any(c.isspace() for c in alias))

    def argv(self, host):
        if not self.available(host):
            raise AmbiguousOutcome("no configured artifact SSH alias")
        return ("ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", self.aliases[host],
                "python3 -c " + shlex.quote(HELPER))

    async def call(self, host, request, content=b""):
        process = await hidden_process.create_subprocess_exec(*self.argv(host), stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

        async def write():
            process.stdin.write(json.dumps(request, separators=(",", ":")).encode() + b"\n")
            await process.stdin.drain()
            try:
                for offset in range(0, len(content), 65536):
                    process.stdin.write(content[offset:offset + 65536])
                    await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass  # helper may already have a final result; read it before deciding
            process.stdin.close()

        async def bounded(stream):
            out = bytearray()
            while True:
                chunk = await stream.read(1024)
                if not chunk:
                    return bytes(out)
                out.extend(chunk)
                if len(out) > 8192:
                    raise AmbiguousOutcome("artifact helper response exceeded its limit")

        try:
            _, out, _err = await asyncio.wait_for(asyncio.gather(write(), bounded(process.stdout), bounded(process.stderr)), self.timeout_s)
            await asyncio.wait_for(process.wait(), self.timeout_s)
            if process.returncode != 0:
                raise AmbiguousOutcome("artifact SSH helper reply was lost")
            result = json.loads(out)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (asyncio.TimeoutError, OSError, ValueError) as exc:
            raise AmbiguousOutcome(f"artifact helper outcome is unknown ({type(exc).__name__})") from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def probe(self, host):
        if not self.available(host):
            return {"ok": False, "code": "ARTIFACT_ADAPTER_UNAVAILABLE"}
        try:
            result = await self.call(host, {"mode": "probe"})
        except AmbiguousOutcome:
            result = {"ok": False, "code": "ARTIFACT_ADAPTER_UNAVAILABLE"}
        self.readiness[host] = result
        return result

    def capture_argv(self, host):
        if not self.available(host):
            raise OperationError("ARTIFACT_ADAPTER_UNAVAILABLE", "capture requires a configured SSH alias", 409)
        return ("ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", self.aliases[host],
                "python3 -I -S -B -c " + shlex.quote(CAPTURE_HELPER))

    async def capture(self, host, request):
        """Only the fixed read helper; bounded JSON evidence followed by bounded binary content."""
        process = await hidden_process.create_subprocess_exec(*self.capture_argv(host), stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

        async def exchange():
            process.stdin.write(json.dumps(request, separators=(",", ":")).encode() + b"\n")
            await process.stdin.drain()
            process.stdin.close()

            async def read_output():
                line = await process.stdout.readline()
                if len(line) > 8192:
                    raise ValueError()
                result = json.loads(line)
                if not isinstance(result, dict):
                    raise ValueError()
                limit = request["max_file_bytes"] if request["mode"] == "capture" else 0
                data = bytearray()
                while True:
                    chunk = await process.stdout.read(min(65536, limit - len(data) + 1))
                    if not chunk:
                        return result, bytes(data)
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ValueError()

            async def read_error():
                size = 0
                while chunk := await process.stderr.read(1024):
                    size += len(chunk)
                    if size > 8192:
                        raise ValueError()

            (result, data), _ = await asyncio.gather(read_output(), read_error())
            await process.wait()
            if process.returncode != 0:
                raise ValueError()
            return result, data

        try:
            return await asyncio.wait_for(exchange(), self.timeout_s)
        except (asyncio.TimeoutError, OSError, ValueError):
            raise AmbiguousOutcome("capture source read did not complete") from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
