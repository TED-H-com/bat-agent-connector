"""Read-only audit/fixture timeline for the old-source publication seed."""
from __future__ import annotations

import base64
import contextlib
import contextvars
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import sys
import threading
import time
import zlib
from pathlib import Path

_started = None
_root = None
_fixture = contextvars.ContextVar('seed_fixture', default=None)
_inside = threading.local()
_output = threading.Lock()


def fixture_log_text(text, root):
    """Shared with artifact preservation; no credentials or machine identity."""
    for name, value in os.environ.items():
        if any(part in name for part in ('TOKEN', 'PASSWORD', 'SECRET')) and len(value) >= 4:
            text = text.replace(value, '<credential>')
    text = re.sub(r'\bbatc_[A-Za-z0-9_-]+', '<credential>', text)
    text = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer <credential>', text)
    for path, label in ((root, '<fixture>'), (Path(__file__).resolve().parents[2], '<checkout>')):
        text = text.replace(str(path), label)
    text = re.sub(r'''/(?:Users|home)/[^/\s'"\]]+''', '<home>', text)
    return text.replace(socket.gethostname(), '<runner>')


def _write(event, **fields):
    now = time.monotonic()
    payload = {'event': event, 'monotonic_s': round(now, 6),
               'elapsed_s': round(now - _started, 6), 'fixture': _fixture.get(), **fields}
    with _output:
        print('[seed-timeline] ' + fixture_log_text(json.dumps(payload, sort_keys=True), _root), flush=True)


def record(event, **fields):
    if _started is None or getattr(_inside, 'active', False):
        return
    _inside.active = True
    try:
        # An observer must never prevent the observed operation.
        with contextlib.suppress(Exception):
            _write(event, **fields)
    finally:
        _inside.active = False


def _argv(arguments):
    values = [arguments] if isinstance(arguments, (str, bytes)) else list(arguments)
    result = []
    for index, value in enumerate(values):
        text = fixture_log_text(os.fsdecode(value), _root)
        if len(text) > 256 or index and values[index - 1] in ('-c', b'-c'):
            # Host scripts contain encoded request bodies. Keep their identity,
            # never a raw source/request prefix that could bypass redaction.
            text = f'<code/argument chars={len(text)} sha256={hashlib.sha256(text.encode()).hexdigest()}>'
        result.append(text)
    return result


def _phase(arguments):
    """Identify transported host requests without printing their payload."""
    for value in arguments:
        text = os.fsdecode(value)
        marker = re.match(r'# batc-int:(\S+)', text)
        if marker:
            return {'phase': marker[1]}
        if not text.startswith('python3 -c '):
            continue
        try:
            parts = shlex.split(text)
            data = base64.b64decode(parts[-1], validate=True)
            try:
                data = zlib.decompress(data)
            except zlib.error:
                pass
            request = json.loads(data)
            return {'host_executable': parts[0], 'phase': request['phase']}
        except (ValueError, KeyError, TypeError, UnicodeError):
            pass
    return {}


def _audit(event, args):
    if event not in {'subprocess.Popen', 'os.posix_spawn', 'os.exec',
                     'socket.gethostname', 'socket.gethostbyaddr', 'socket.getaddrinfo', 'socket.gethostbyname'}:
        return
    if getattr(_inside, 'active', False):
        return
    _inside.active = True
    try:
        if event.startswith('socket.'):
            _write(event, query=repr(args), cwd=os.getcwd())
        else:
            executable, argv = args[:2]
            cwd, env = (args[2], args[3]) if event == 'subprocess.Popen' else (None, args[2])
            _write(event, executable=os.fsdecode(executable), argv=_argv(argv), cwd=os.fsdecode(cwd or os.getcwd()),
                   env={'provided': env is not None, 'values': 'omitted'}, **_phase(argv))
    except Exception as error:
        with contextlib.suppress(Exception):
            _write('observer.error', observed_event=event, error_type=type(error).__name__)
    finally:
        _inside.active = False


def pytest_configure(config):
    if os.environ.get('BATC_UPGRADE_SEED_MODE') != '1':
        return
    import pytest

    global _started, _root
    _started = time.monotonic()
    _root = Path(os.environ['BATC_UPGRADE_SEED_DATA']).parent
    sys.addaudithook(_audit)
    record('observer.start', plugin=__file__, executable=sys.executable,
           tools={name: shutil.which(name) for name in ('sh', 'git', 'python3')})

    class Fixtures:
        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_fixture_setup(self, fixturedef, request):
            token = _fixture.set(fixturedef.argname)
            started = time.monotonic()
            record('fixture.setup.start')
            try:
                outcome = yield
                record('fixture.setup.end', duration_s=round(time.monotonic() - started, 6),
                       ok=outcome.excinfo is None)
            finally:
                _fixture.reset(token)

    config.pluginmanager.register(Fixtures())
