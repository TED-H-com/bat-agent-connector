"""Best-effort local error records. One process lock serializes append/rotation/collapse.

The active file plus four numbered backups form the 10 MiB x5 spool. Repeats
update the original timestamp/count in place within a fixed 60 second window.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .redact import redact as registered_redact

_PATTERNS = json.loads(Path(__file__).with_name('errlog_patterns.json').read_text())
_LOCK = threading.Lock()
LIMIT = 10 * 1024 * 1024


def redact(value: object) -> str:
    text = registered_redact(value)
    for pattern in _PATTERNS['redaction']:
        text = re.sub(pattern, '[REDACTED]', text)
    return text


def fingerprint(component: str, code: str, msg: str) -> str:
    msg = redact(msg)
    for pattern, replacement in _PATTERNS['normalization']:
        msg = re.sub(pattern, replacement, msg)
    return hashlib.sha256(f'{component}|{code}|{msg}'.encode()).hexdigest()[:16]


def _clean(value):
    if isinstance(value, dict):
        return {redact(k): ('[REDACTED]' if re.search(
            r'token|password|passwd|secret|authorization|api.?key', str(k), re.I
        ) else _clean(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return redact(value) if isinstance(value, str) else value


def log_error(component: str, code: str, msg: object, *, level: str = 'error', context=None) -> None:
    """Logging failures never replace the caller's original outcome."""
    try:
        with _LOCK:
            override = os.environ.get('BAT_ERROR_LOG')
            if override:
                path = Path(override)
            else:
                base = Path(os.environ['LOCALAPPDATA']) / 'BAT' if os.name == 'nt' else Path.home() / '.bat'
                path = base / 'logs' / 'errors.jsonl'
            path.parent.mkdir(parents=True, exist_ok=True)
            with _file_lock(path):
                if path.is_symlink() or (path.exists() and not stat.S_ISREG(path.stat().st_mode)):
                    return
                text = redact(msg)
                record = dict(ts=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                              machine_id=socket.gethostname(), component=redact(component),
                              version=__version__, level=level, code=redact(code), msg=text,
                              fingerprint=fingerprint(component, code, text), count=1,
                              context=_clean(context or {}))
                # Bounded file scan also collapses across independently started writers.
                lines = path.read_bytes().splitlines() if path.exists() and path.stat().st_size <= LIMIT else []
                for index in range(len(lines) - 1, -1, -1):
                    try:
                        old = json.loads(lines[index])
                        age = time.time() - datetime.fromisoformat(old['ts'].replace('Z', '+00:00')).timestamp()
                    except (ValueError, KeyError, TypeError):
                        continue
                    if 0 <= age < 60 and old['fingerprint'] == record['fingerprint']:
                        old['count'] += 1
                        lines[index] = json.dumps(old, ensure_ascii=False).encode()
                        _replace(path, b'\n'.join(lines) + b'\n')
                        return
                data = (json.dumps(record, ensure_ascii=False) + '\n').encode()
                if path.exists() and path.stat().st_size + len(data) > LIMIT:
                    for i in range(4, 0, -1):
                        source = path if i == 1 else Path(f'{path}.{i - 1}')
                        if source.exists():
                            source.replace(Path(f'{path}.{i}'))
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                with os.fdopen(fd, 'ab') as stream:
                    stream.write(data)
    except Exception:
        return  # Logging must not mask the original failure.


@contextmanager
def _file_lock(path):
    """Same lock file/range as fleet-core, including across processes."""
    lock_path = path.with_name('.' + path.name + '.lock')
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'r+b') as stream:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _replace(path, data):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
