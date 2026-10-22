"""The local GitHub fake must not depend on runner reverse DNS."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


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
