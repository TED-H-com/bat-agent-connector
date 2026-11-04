"""Suppress Windows console allocation without changing child IO or lifecycle."""
from __future__ import annotations

import asyncio
import os
import subprocess


def hidden_options(options):
    options = dict(options)
    if os.name == "nt":
        options["creationflags"] = options.get("creationflags", 0) | 0x08000000
    return options


def run(*args, **kwargs):
    return subprocess.run(*args, **hidden_options(kwargs))  # noqa: S603


def popen(*args, **kwargs):
    return subprocess.Popen(*args, **hidden_options(kwargs))  # noqa: S603


async def create_subprocess_exec(*args, **kwargs):
    return await asyncio.create_subprocess_exec(*args, **hidden_options(kwargs))


async def create_subprocess_shell(*args, **kwargs):
    return await asyncio.create_subprocess_shell(*args, **hidden_options(kwargs))
