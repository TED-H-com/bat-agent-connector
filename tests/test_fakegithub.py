"""GitHub fake routing and runner-independent loopback binding."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(("path", "expected"), [
    ("/repos/o/r/compare/main...feature", ("main", "feature")),
    ("/repos/o/r/compare/release%2Fv1...feature%2Ffix", ("release%2Fv1", "feature%2Ffix")),
    ("/repos/o/r/compare/v1.2...v1.3", ("v1.2", "v1.3")),
    ("/repos/o/r/compare/one...two...three", ("one...two", "three")),
    ("/repos/o/r/compare/base...head...", ("base", "head...")),
    ("/repos/o/r/compare/...head", None),
    ("/repos/o/r/compare/base...", None),
    ("/repos/o/r/compare/...", None),
    ("/repos/o/r/compare/base", None),
    ("/other/compare/base...head", None),
    ("/repos/o/r/compare/base...head\n", None),
    ("/repos/o/r/compare/" + "." * 50_000, ("." * 49_996, ".")),
    ("/repos/o/r/compare/" + "." * 50_000 + "\n", None),
], ids=["normal", "quoted", "single-dots", "last-separator", "trailing-separators", "empty-base",
        "empty-head", "empty-both", "no-separator", "wrong-prefix", "newline", "many-dots", "many-dots-newline"])
def test_compare_route(path, expected):
    from tests.fakegithub import parse_compare_route

    assert parse_compare_route(path) == expected


def test_bind_never_looks_up_the_loopback_hostname(tmp_path):
    # Audit in a child: hooks cannot be removed, and no stdlib function is replaced.
    program = '''
import json, sys
from tests.fakegithub import FakeGitHub
lookups = []
def audit(event, args):
    if event == 'socket.gethostbyaddr':
        frames = []
        frame = sys._getframe(1)
        while frame is not None and len(frames) < 8:
            frames.append({'file': frame.f_code.co_filename, 'function': frame.f_code.co_name})
            frame = frame.f_back
        lookups.append({'query': args, 'frames': frames})
sys.addaudithook(audit)
fake = FakeGitHub()
try:
    print(json.dumps({'lookups': lookups, 'server_name': fake._server.server_name,
                      'port': fake._server.server_port}))
finally:
    fake._server.server_close()
'''
    repo = Path(__file__).resolve().parents[1]
    child = subprocess.run([sys.executable, '-c', program], cwd=repo,
                           env={**os.environ, 'PYTHONPATH': str(repo), 'TMPDIR': str(tmp_path)},
                           capture_output=True, text=True, timeout=20)
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result['lookups'] == [], result['lookups']
    assert result['server_name'] == '127.0.0.1' and result['port'] > 0
