"""Administrator-owned AHP observer bindings; callers never supply endpoints."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

ADAPTER_ID = re.compile(r'[a-z][a-z0-9_-]{0,63}')
WORKSPACE_ID = re.compile(r'ws_[0-9a-f]{32}')


@dataclass(frozen=True)
class LocalAhpProfile:
    """Private central binding, separate from TCP adapters; never carries a token."""

    profile_id: str
    user_data_root: str
    installation_root: str  # Explicit resources/app directory, not an executable to launch.
    entry_id: str
    provider: str
    session_uris: tuple[str, ...] = ()
    wire_version: str = '0.10.0'

    def __post_init__(self):
        if (not ADAPTER_ID.fullmatch(self.profile_id) or not re.fullmatch('[0-9a-f]{64}', self.entry_id)
                or self.wire_version != '0.10.0' or not ADAPTER_ID.fullmatch(self.provider)
                or not isinstance(self.session_uris, tuple) or len(self.session_uris) > 8):
            raise ValueError('Invalid local AHP profile')
        for path in (self.user_data_root, self.installation_root):
            if not path.startswith('/') or str(PurePosixPath(path)) != path or '..' in PurePosixPath(path).parts:
                raise ValueError('Local AHP needs explicit absolute directories')


@dataclass(frozen=True)
class AhpAdapter:
    adapter_id: str
    workspace_ids: tuple[str, ...]
    endpoint: str
    credential_ref: str
    credential_transport: str = 'connection_token_query'

    def binding_digest(self):
        return hashlib.sha256(json.dumps(vars(self), sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def token(self):
        from .config import _resolve_token_ref
        from .redact import register_secret
        token = _resolve_token_ref(self.credential_ref, '')
        if len(token) > 65536 or any(ord(c) < 32 or ord(c) == 127 for c in token):
            raise ValueError('Invalid AHP credential')
        register_secret(token)
        return token


def parse_ahp(data):
    from .config import ConfigError
    section = data.get('ahp', {})
    if not isinstance(section, dict) or set(section) - {'adapters'}:
        raise ConfigError('[ahp] supports only explicitly configured adapters')
    adapters = section.get('adapters', {})
    if not isinstance(adapters, dict) or len(adapters) > 32:
        raise ConfigError('AHP supports at most 32 configured adapters')
    result = {}
    for key, value in adapters.items():
        def bad():
            raise ConfigError('AHP adapter needs an ID, explicit PM workspace IDs, endpoint and credential_ref')
        if (not isinstance(key, str) or not ADAPTER_ID.fullmatch(key) or not isinstance(value, dict)
                or set(value) - {'workspace_ids', 'endpoint', 'credential_ref', 'credential_transport'}):
            bad()
        ids = value.get('workspace_ids')
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 100
                or any(not isinstance(w, str) or not WORKSPACE_ID.fullmatch(w) for w in ids)
                or len(set(ids)) != len(ids)):
            bad()
        endpoint, ref = value.get('endpoint'), value.get('credential_ref')
        if (not isinstance(endpoint, str) or len(endpoint) > 2048
                or any(ord(c) < 33 or ord(c) == 127 for c in endpoint)
                or not isinstance(ref, str) or len(ref) > 2048
                or not re.fullmatch(r'(?:env:[A-Za-z_][A-Za-z0-9_]*|file:/[^\r\n]+)', ref)):
            bad()
        try:
            url = urlsplit(endpoint)
            if (url.scheme not in {'ws', 'wss'} or not url.hostname or url.username is not None
                    or url.password is not None or url.query or url.fragment or url.port == 0):
                bad()
            if url.scheme == 'ws' and not ipaddress.ip_address(url.hostname).is_loopback:
                bad()
        except ValueError:
            bad()
        transport = value.get('credential_transport', 'connection_token_query')
        if transport not in {'connection_token_query', 'bearer_header'}:
            bad()
        result[key] = AhpAdapter(key, tuple(sorted(ids)), endpoint, ref, transport)
    return result
