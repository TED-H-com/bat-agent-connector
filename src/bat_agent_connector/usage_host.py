"""Read-only usage over the existing configured, pinned SSH host-helper transport."""
from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path

from .usage_adapters import UsageInvalid
from .usage_host_helper import ERRORS, REPLY_BYTES, REQUEST_BYTES, VERSION, validate


def helper_source(source):
    # Load the exact shared parser/reader code into an isolated in-memory package.
    # No remote installation, import search, duplicated parser, or file writes.
    modules = ("iso_time", "usage_adapters", "usage_collector", "usage_host_helper")
    code = "import sys, types\np=types.ModuleType('usage_bundle');p.__path__=[];sys.modules[p.__name__]=p\n"
    for name in modules:
        content = Path(__file__).with_name(name + ".py").read_text()
        code += (f"m=types.ModuleType('usage_bundle.{name}');m.__package__='usage_bundle';"
                 f"sys.modules[m.__name__]=m;exec(compile({content!r},'<usage-helper>','exec'),m.__dict__)\n")
    binding = {"path": str(source.path), "adapter": source.adapter, "session_id": source.session_id}
    return code + f"m.main({binding!r})\n"


class UsageHost:
    def __init__(self, aliases, timeout_s=30):
        self.aliases, self.timeout_s = dict(aliases), timeout_s

    def binding(self, host):
        return self.aliases.get(host)

    def argv(self, source):
        alias = self.binding(source.host)
        if not isinstance(alias, str) or not alias or alias.startswith("-") or any(c.isspace() for c in alias):
            raise UsageInvalid("host_unreachable")
        return ("ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", alias,
                "python3 -I -S -B -c " + shlex.quote(helper_source(source)))

    async def read(self, source, cursor, parser):
        cursor = {k: v for k, v in cursor.items() if k != "coverage_at"}
        request = json.dumps({"version": VERSION, "cursor": cursor, "parser": parser}).encode()
        if len(request) > REQUEST_BYTES:
            raise UsageInvalid("invalid_reply")
        process = None
        try:
            process = await asyncio.create_subprocess_exec(*self.argv(source), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

            async def bounded(stream, limit):
                out = bytearray()
                while chunk := await stream.read(65536):
                    out.extend(chunk)
                    if len(out) > limit:
                        raise UsageInvalid("invalid_reply")
                return bytes(out)

            async def exchange():
                process.stdin.write(request)
                await process.stdin.drain()
                process.stdin.close()
                readers = [asyncio.create_task(bounded(process.stdout, REPLY_BYTES)),
                           asyncio.create_task(bounded(process.stderr, 8192))]
                try:
                    out, _ = await asyncio.gather(*readers)
                finally:
                    for reader in readers:
                        reader.cancel()
                    await asyncio.gather(*readers, return_exceptions=True)
                await process.wait()
                if process.returncode:
                    raise UsageInvalid("host_unreachable")
                return out

            out = await asyncio.wait_for(exchange(), self.timeout_s)
            try:
                result = json.loads(out)
                if isinstance(result, dict) and set(result) == {"version", "error"}:
                    if type(result["version"]) is int and result["version"] == VERSION and result["error"] in ERRORS:
                        raise UsageInvalid(result["error"])
                    raise ValueError
                return validate(result)
            except UsageInvalid:
                raise
            except (ValueError, TypeError, KeyError, RecursionError):
                raise UsageInvalid("invalid_reply") from None
        except (OSError, asyncio.TimeoutError):
            raise UsageInvalid("host_unreachable") from None
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
