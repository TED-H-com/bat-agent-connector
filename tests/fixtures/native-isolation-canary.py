"""Runs only inside the coding sandbox, against synthetic test-owned paths."""
import ctypes
import json
import os
import pathlib
import socket
import sys

paths, inherited, host_pid, port = json.loads(sys.argv[1]), int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
results = {}


def attempt(name, action):
    try:
        action()
    except (OSError, ValueError):
        results[name] = False
    else:
        results[name] = True


for index, path in enumerate(paths):
    attempt(f'read-{index}', lambda: pathlib.Path(path).read_bytes())
    attempt(f'write-{index}', lambda: pathlib.Path(path).write_bytes(b'escaped'))
    link = pathlib.Path(f'symlink-{index}')
    link.symlink_to(path)
    attempt(f'symlink-read-{index}', link.read_bytes)
    attempt(f'symlink-write-{index}', lambda: link.write_bytes(b'escaped'))
    attempt(f'hardlink-{index}', lambda: os.link(path, f'hardlink-{index}'))
    for pid in ('1', host_pid):
        escape = pathlib.Path(f'/proc/{pid}/root' + path)
        attempt(f'proc-read-{pid}-{index}', escape.read_bytes)
        attempt(f'proc-write-{pid}-{index}', lambda: escape.write_bytes(b'escaped'))
attempt('inherited-read', lambda: os.read(inherited, 1))
attempt('inherited-write', lambda: os.write(inherited, b'escaped'))
with socket.socket() as client:
    client.settimeout(1)
    attempt('network', lambda: client.connect(('127.0.0.1', port)))
libc = ctypes.CDLL(None, use_errno=True)
results['new-mount-namespace'] = libc.unshare(0x00020000) == 0
results['new-user-namespace'] = libc.unshare(0x10000000) == 0
pathlib.Path('canary-edit').write_text('owned')
print(json.dumps(results), flush=True)
