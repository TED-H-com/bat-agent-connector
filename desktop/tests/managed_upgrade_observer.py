"""Read-only audit/fixture timeline for the old-source publication seed."""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

_started = None
_root = None
_fixture = contextvars.ContextVar('seed_fixture', default=None)
_inside = threading.local()
_output = threading.Lock()
_active_fixture = None
_sampler_stop = threading.Event()
_sampler = None


def _stack(frame):
    """Bounded frame locations only: never locals or source/request contents."""
    frames = []
    while frame is not None and len(frames) < 12:
        frames.append({'file': frame.f_code.co_filename, 'line': frame.f_lineno,
                       'function': frame.f_code.co_name})
        frame = frame.f_back
    return frames


def _sample_setup():
    while not _sampler_stop.wait(1):
        active = _active_fixture
        if active is not None:
            record('fixture.setup.sample', fixture=active,
                   stack=_stack(sys._current_frames().get(threading.main_thread().ident)))


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
        if len(value) > 256 or index and values[index - 1] in ('-c', b'-c'):
            # Host scripts contain encoded request bodies. Keep their identity,
            # never a raw source/request prefix that could bypass redaction.
            digest = hashlib.sha256(os.fsencode(value)).hexdigest()
            text = f'<code/argument chars={len(value)} sha256={digest}>'
        else:
            text = os.fsdecode(value)
        result.append(text)
    return result


def _phase(arguments):
    """Only inspect a bounded plain-text marker, never parse transported code.

    Large argv values stay opaque: length and SHA-256 identify a host call.
    Tokenizing/decoding its 42 KB request changed the timing being measured.
    """
    for value in arguments:
        text = os.fsdecode(value[:128])
        marker = re.match(r'# batc-int:([\w.-]{1,64})(?:\s|$)', text)
        if marker:
            return {'phase': marker[1]}
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
            fields = {'stack': _stack(sys._getframe(1))} if event == 'socket.gethostbyaddr' else {}
            _write(event, query=repr(args), cwd=os.getcwd(), **fields)
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

    global _started, _root, _sampler
    _started = time.monotonic()
    _root = Path(os.environ['BATC_UPGRADE_SEED_DATA']).parent
    sys.addaudithook(_audit)
    record('observer.start', plugin=__file__, executable=sys.executable,
           tools={name: shutil.which(name) for name in ('sh', 'git', 'python3')})
    _sampler_stop.clear()
    _sampler = threading.Thread(target=_sample_setup, name='seed-setup-sampler', daemon=True)
    _sampler.start()

    class Fixtures:
        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_fixture_setup(self, fixturedef, request):
            global _active_fixture
            token = _fixture.set(fixturedef.argname)
            previous, _active_fixture = _active_fixture, fixturedef.argname
            started = time.monotonic()
            record('fixture.setup.start')
            if fixturedef.argname == 'world':
                names = ('tests.fakegithub', 'tests.test_repository_sync', 'tests.test_pm_worktree',
                         'tests.test_checkpoints', 'bat_agent_connector')
                record('seed.imports', modules={name: getattr(sys.modules.get(name), '__file__', None)
                                               for name in names})
            try:
                outcome = yield
                record('fixture.setup.end', duration_s=round(time.monotonic() - started, 6),
                       ok=outcome.excinfo is None)
            finally:
                _active_fixture = previous
                _fixture.reset(token)

        def pytest_unconfigure(self, config):
            _sampler_stop.set()
            _sampler.join(timeout=2)

    config.pluginmanager.register(Fixtures())
