"""Fixed, read-only deployment observation helper (stdlib plus bundled hidden_process)."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import socket
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from .hidden_process import popen
from .redact import redact_secrets

VERSION = 1
LIMIT = 2 * 1024 * 1024
ERRORS = {'missing', 'permission_denied', 'command_failed', 'timeout', 'output_limit', 'invalid_output'}
PATTERNS = ('bat*', 'docker*', 'podman*', '*compose*', 'caddy.service', 'nginx.service', 'cloudflared.service')
LABELS = ('com.docker.compose.project', 'com.docker.compose.project.working_dir', 'com.docker.compose.project.config_files', 'com.docker.compose.service')


class ReadError(Exception):
    pass


def clean(value):
    return ''.join(c for c in redact_secrets(str(value)) if c.isprintable())[:256]


def allowed(argv):
    a = tuple(argv)
    if a in {(e, 'ps', '-a', '--no-trunc', '--format', '{{json .}}') for e in ('docker', 'podman')}:
        return True
    if a in {('systemctl', *scope, 'list-units', '--type=service', '--all', '--no-pager', '--plain', *output)
             for scope in ((), ('--user',)) for output in (('--output=json',), ())}:
        return True
    return (len(a) >= 9 and a[:6] == ('git', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false', '-C') and os.path.isabs(a[6]) and a[7:] in {
        ('rev-parse', '--short=12', 'HEAD'), ('rev-parse', '--abbrev-ref', 'HEAD'),
        ('log', '-1', '--format=%cI%n%s'), ('status', '--porcelain', '--untracked-files=normal')})


def run(argv):
    if not allowed(argv):
        raise ValueError('non-read-only command')
    env = dict({k: v for k, v in os.environ.items() if not k.startswith('GIT_')}, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', LC_ALL='C', SYSTEMD_PAGER='')
    try:
        p = popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                  shell=False, env=env)
    except FileNotFoundError:
        raise ReadError('missing') from None
    except PermissionError:
        raise ReadError('permission_denied') from None
    except OSError:
        raise ReadError('command_failed') from None
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()

    def drain(stream, buffer, limit):
        while chunk := stream.read(65536):
            if len(buffer) + len(chunk) > limit:
                overflow.set()
                p.kill()
                break
            buffer.extend(chunk)

    threads = [threading.Thread(target=drain, args=(s, b, n))
               for s, b, n in zip((p.stdout, p.stderr), buffers, (LIMIT, 8192), strict=True)]
    for t in threads:
        t.start()
    error = None
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        error = 'timeout'
        p.kill()
        p.wait()
    finally:
        for t in threads:
            t.join()
        p.stdout.close()
        p.stderr.close()
    if overflow.is_set():
        raise ReadError('output_limit')
    if error:
        raise ReadError(error)
    if p.returncode:
        diagnostic = bytes(buffers[1]).lower()
        raise ReadError('permission_denied' if b'permission denied' in diagnostic or b'access denied' in diagnostic else 'command_failed')
    return bytes(buffers[0]).decode('utf-8', errors='replace')


def labels(value):
    if isinstance(value, dict):
        return {k: value.get(k, '') for k in LABELS}
    pairs = dict(part.partition('=')[::2] for part in str(value).split(',') if '=' in part)
    return {k: pairs.get(k, '') for k in LABELS}


def containers(engine, execute):
    rows = []
    for line in execute([engine, 'ps', '-a', '--no-trunc', '--format', '{{json .}}']).splitlines()[:500]:
        item = json.loads(line)
        lab = labels(item.get('Labels', {}))
        status = clean(item.get('Status', ''))
        health = re.search(r'\((healthy|unhealthy|health: starting)\)', status)
        rows.append(dict(engine=engine, name=clean(item.get('Names', '')), image=clean(item.get('Image', '')),
            state=clean(item.get('State', 'unknown')), status=status, health=health.group(1) if health else '',
            created=clean(item.get('CreatedAt', item.get('Created', ''))), ports=clean(item.get('Ports', '')),
            project=clean(lab[LABELS[0]]), service=clean(lab[LABELS[3]]),
            working_dir=clean(os.path.basename(str(lab[LABELS[1]]).rstrip('/'))),
            config_files=clean(','.join(os.path.basename(x) for x in str(lab[LABELS[2]]).split(',')))))
    return rows


def units(scope, patterns, execute):
    prefix = ['systemctl'] + (['--user'] if scope == 'user' else [])
    args = ['list-units', '--type=service', '--all', '--no-pager', '--plain']
    try:
        items = json.loads(execute(prefix + args + ['--output=json']))
        if not isinstance(items, list):
            raise ValueError
    except (ReadError, ValueError):
        items = []
        for line in execute(prefix + args).splitlines():
            parts = line.split(None, 4)
            if len(parts) == 5 and parts[0].endswith('.service'):
                items.append(dict(zip(('unit', 'load', 'active', 'sub', 'description'), parts, strict=True)))
    return [dict(scope=scope, name=clean(x.get('unit', '')), **{k: clean(x.get(k, '')) for k in ('load', 'active', 'sub', 'description')})
            for x in items if isinstance(x, dict) and any(fnmatch.fnmatchcase(str(x.get('unit', '')), p) for p in (*PATTERNS, *patterns))][:200]


def collect(binding, execute=run):
    result = dict(version=VERSION, collected_at=datetime.now(timezone.utc).isoformat(), hostname=clean(socket.gethostname()),
                  containers=[], compose_projects=[], systemd=[], deploys=[], errors=[])
    for engine in ('docker', 'podman'):
        try:
            result['containers'].extend(containers(engine, execute))
        except (ReadError, ValueError, TypeError, AttributeError) as exc:
            result['errors'].append(dict(source=engine, code=str(exc) if isinstance(exc, ReadError) else 'invalid_output'))
    result['containers'] = result['containers'][:500]
    projects = {}
    for c in result['containers']:
        if c['project']:
            key = (c['engine'], c['project'], c['working_dir'], c['config_files'])
            p = projects.setdefault(key, dict(engine=c['engine'], name=c['project'], working_dir=c['working_dir'], config_files=c['config_files'], counts={}))
            p['counts'][c['state']] = p['counts'].get(c['state'], 0) + 1
    result['compose_projects'] = list(projects.values())
    for scope in ('system', 'user'):
        try:
            result['systemd'].extend(units(scope, binding['unit_patterns'], execute))
        except (ReadError, ValueError, TypeError, AttributeError) as exc:
            result['errors'].append(dict(source='systemd_' + scope, code=str(exc) if isinstance(exc, ReadError) else 'invalid_output'))
    result['systemd'] = result['systemd'][:200]
    for index, directory in enumerate(binding['deploy_dirs']):
        row = dict(index=index, name=clean(Path(directory).name), sha='', branch='', commit_time='', subject='', dirty_count=0, mtime='', error='')
        try:
            prefix = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false', '-C', directory]
            row['mtime'] = datetime.fromtimestamp(Path(directory).stat().st_mtime, timezone.utc).isoformat()
            row['sha'] = clean(execute(prefix + ['rev-parse', '--short=12', 'HEAD']).strip())
            row['branch'] = clean(execute(prefix + ['rev-parse', '--abbrev-ref', 'HEAD']).strip())
            log = execute(prefix + ['log', '-1', '--format=%cI%n%s']).splitlines()
            row['commit_time'] = clean(log[0]) if log else ''
            row['subject'] = clean(log[1]) if len(log) > 1 else ''
            row['dirty_count'] = min(100000, len(execute(prefix + ['status', '--porcelain', '--untracked-files=normal']).splitlines()))
        except (OSError, ReadError) as exc:
            row['error'] = str(exc) if isinstance(exc, ReadError) else 'missing'
        result['deploys'].append(row)
    return result


def main(binding):
    print(json.dumps(collect(binding), separators=(',', ':')))
