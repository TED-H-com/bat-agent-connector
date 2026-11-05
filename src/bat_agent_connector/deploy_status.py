"""On-demand, bounded SSH deployment observations; no remote installation or writes."""
from __future__ import annotations

import asyncio
import json
import re
import shlex
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import hidden_process
from .deploy_status_helper import ERRORS, LIMIT, VERSION
from .errors import ConfigError
from .redact import redact, redact_secrets

ALIAS = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')


def parse_deploy_status(data, hosts):
    section = data.get('deploy_status', {})
    if not isinstance(section, dict) or set(section) - {'hosts'}:
        raise ConfigError('[deploy_status] requires hosts only')
    entries = section.get('hosts', [])
    mappings = data.get('managed_setup', {})
    mappings = mappings.get('ssh_hosts', {}) if isinstance(mappings, dict) else None
    if not isinstance(entries, list) or len(entries) > 64 or not isinstance(mappings, dict):
        raise ConfigError('invalid deploy status hosts or SSH mappings')
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {'host', 'deploy_dirs', 'unit_patterns'}:
            raise ConfigError('invalid deploy status host entry')
        host = entry.get('host')
        if not isinstance(host, str) or host not in hosts or host in result:
            raise ConfigError('deploy status requires unique configured hosts')
        alias = mappings.get(host)
        if not isinstance(alias, str) or not ALIAS.fullmatch(alias):
            raise ConfigError('deploy status requires a valid managed_setup SSH alias')
        dirs, patterns = entry.get('deploy_dirs', []), entry.get('unit_patterns', [])
        if (not isinstance(dirs, list) or len(dirs) > 20 or any(not isinstance(p, str) or
                not p.startswith('/') or p.startswith('//') or '..' in PurePosixPath(p).parts or
                len(p) > 1024 or any(ord(c) < 32 for c in p) for p in dirs) or len(set(dirs)) != len(dirs)):
            raise ConfigError('deploy_dirs requires up to 20 unique absolute directories')
        if (not isinstance(patterns, list) or len(patterns) > 20 or any(not isinstance(p, str) or
                not re.fullmatch(r'[A-Za-z0-9_.@*?\[\]-]{1,128}', p) for p in patterns)):
            raise ConfigError('invalid deploy status unit_patterns')
        result[host] = dict(alias=alias, deploy_dirs=dirs, unit_patterns=patterns)
    return result


def helper_source(binding):
    code = "import sys,types\np=types.ModuleType('deploy_bundle');p.__path__=[];sys.modules[p.__name__]=p\n"
    for name in ('hidden_process', 'redact', 'deploy_status_helper'):
        content = Path(__file__).with_name(name + '.py').read_text()
        code += (f"m=types.ModuleType('deploy_bundle.{name}');m.__package__='deploy_bundle';"
                 f"sys.modules[m.__name__]=m;exec(compile({content!r},'<deploy-helper>','exec'),m.__dict__)\n")
    return code + f"m.main({{ 'deploy_dirs': {binding['deploy_dirs']!r}, 'unit_patterns': {binding['unit_patterns']!r} }})\n"


def validate(reply, binding):
    def exact(value, keys):
        if not isinstance(value, dict) or set(value) != set(keys):
            raise ValueError('invalid_reply')

    def string(value):
        if (not isinstance(value, str) or len(value) > 256 or any(not c.isprintable() for c in value)
                or redact_secrets(redact(value)) != value):
            raise ValueError('invalid_reply')

    exact(reply, ('version', 'collected_at', 'hostname', 'containers', 'compose_projects', 'systemd', 'deploys', 'errors'))
    if type(reply['version']) is not int or reply['version'] != VERSION:
        raise ValueError('invalid_reply')
    for key in ('collected_at', 'hostname'):
        string(reply[key])
    if datetime.fromisoformat(reply['collected_at']).tzinfo is None:
        raise ValueError('invalid_reply')
    schemas = {
        'containers': (500, 'engine name image state status health created ports project service working_dir config_files'),
        'compose_projects': (500, 'engine name working_dir config_files counts'),
        'systemd': (200, 'scope name load active sub description'),
        'deploys': (20, 'index name sha branch commit_time subject dirty_count mtime error'),
        'errors': (4, 'source code')}
    for key, (limit, fields) in schemas.items():
        rows = reply[key]
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError('invalid_reply')
        for row in rows:
            exact(row, fields.split())
            for field, value in row.items():
                if field in {'index', 'dirty_count'}:
                    if type(value) is not int or not 0 <= value <= (19 if field == 'index' else 100000):
                        raise ValueError('invalid_reply')
                elif field == 'counts':
                    if not isinstance(value, dict) or len(value) > 500:
                        raise ValueError('invalid_reply')
                    for state, count in value.items():
                        string(state)
                        if type(count) is not int or not 0 <= count <= 500:
                            raise ValueError('invalid_reply')
                else:
                    string(value)
            for field in ('working_dir', 'config_files'):
                if field in row and ('/' in row[field] or '\\' in row[field]):
                    raise ValueError('invalid_reply')
            if 'engine' in row and row['engine'] not in {'docker', 'podman'}:
                raise ValueError('invalid_reply')
            if key == 'systemd' and row['scope'] not in {'system', 'user'}:
                raise ValueError('invalid_reply')
            if key == 'errors' and (row['code'] not in ERRORS or row['source'] not in {'docker', 'podman', 'systemd_system', 'systemd_user'}):
                raise ValueError('invalid_reply')
            if key == 'deploys' and row['error'] not in ERRORS | {''}:
                raise ValueError('invalid_reply')
    if [r['index'] for r in reply['deploys']] != list(range(len(binding['deploy_dirs']))):
        raise ValueError('invalid_reply')
    return reply


class DeployStatus:
    def __init__(self, bindings, timeout_s=30):
        self.bindings, self.timeout_s = bindings, timeout_s
        self.lock = asyncio.Lock()

    def argv(self, binding):
        if not isinstance(binding['alias'], str) or not ALIAS.fullmatch(binding['alias']):
            raise ValueError('invalid_reply')
        return ('ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', binding['alias'],
                'python3 -I -S -B -c ' + shlex.quote(helper_source(binding)))

    async def read(self, binding):
        process = None
        readers = []
        try:
            process = await hidden_process.create_subprocess_exec(*self.argv(binding), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

            async def bounded(stream, limit):
                result = bytearray()
                while chunk := await stream.read(65536):
                    result.extend(chunk)
                    if len(result) > limit:
                        raise ValueError('invalid_reply')
                return bytes(result)

            async def exchange():
                readers.extend([asyncio.create_task(bounded(process.stdout, LIMIT)),
                                asyncio.create_task(bounded(process.stderr, 8192))])
                out, _ = await asyncio.gather(*readers)
                await process.wait()
                if process.returncode:
                    return {'error': 'host_unreachable'}
                return validate(json.loads(out), binding)

            return await asyncio.wait_for(exchange(), self.timeout_s)
        except (OSError, asyncio.TimeoutError):
            return {'error': 'host_unreachable'}
        except (ValueError, TypeError, KeyError, RecursionError):
            return {'error': 'invalid_reply'}
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

    async def document(self, host=None):
        if host is not None and host not in self.bindings:
            raise ConfigError('host deployment status is not configured for this host')
        bindings = self.bindings
        selected = [host] if host is not None else list(bindings)
        # Each local daemon serializes its own queries to bound concurrent SSH load.
        async with self.lock:
            rows = []
            for name in selected:
                rows.append(dict(host=name, **await self.read(bindings[name])))
        return {'version': VERSION, 'hosts': rows}
