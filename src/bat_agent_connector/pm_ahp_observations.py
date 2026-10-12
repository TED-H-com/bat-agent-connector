"""Workspace-granted, durable AHP read receipts. No AHP ownership adoption."""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter

from . import ahp_observer as provider
from . import api_auth, pm_lifecycle_gates, pm_workspace
from .ahp_config import ADAPTER_ID
from .operations import ActionDef, OperationError, OperationService
from .pm_release_baselines import _json, _snapshot, _snapshot_read

ACTION = 'pm.ahp.observe'
ID = re.compile(r'pmah_[0-9a-f]{32}')
MAX_READ_BYTES = 2_000_000


def _bad(code, status=409):
    return OperationError(code, 'AHP observation is unavailable under the original source and authority', status)


def _configured(ops):
    fleet = ops.context.get('fleet')
    return getattr(getattr(fleet, 'config', None), 'ahp', {})


def schema(journal):
    pm_workspace.schema(journal)
    with journal.tx():
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_ahp_adapter_grants (
            adapter_id TEXT NOT NULL, workspace_id TEXT NOT NULL, binding_digest TEXT NOT NULL,
            PRIMARY KEY(adapter_id,workspace_id))''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_ahp_observations (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, observation_id TEXT NOT NULL UNIQUE,
            workspace_id TEXT NOT NULL, adapter_id TEXT NOT NULL, binding_digest TEXT NOT NULL,
            document TEXT NOT NULL, document_digest TEXT NOT NULL, created_at REAL NOT NULL,
            actor TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE)''')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS pm_ahp_resource_access (
            adapter_id TEXT NOT NULL, binding_digest TEXT NOT NULL, resource TEXT NOT NULL,
            source_identity TEXT NOT NULL, available INTEGER NOT NULL, source_kind TEXT,
            PRIMARY KEY(adapter_id,binding_digest,resource,source_identity))''')
        columns = {row[1] for row in journal.db.execute('PRAGMA table_info(pm_ahp_resource_access)')}
        if 'source_kind' not in columns:
            journal.db.execute('ALTER TABLE pm_ahp_resource_access ADD COLUMN source_kind TEXT')
            # Upgrade the current access projection from original immutable facts,
            # not URI prefixes. No historical document/digest or ID is rewritten.
            for row in journal.db.execute('SELECT adapter_id,binding_digest,document,document_digest FROM pm_ahp_observations'):
                try:
                    document = json.loads(row[2])
                    if provider.digest(document) != row[3]:
                        continue
                    sources = _covered_sources(document)
                except (ValueError, KeyError, TypeError, OperationError):
                    continue
                for source in sources:
                    journal.db.execute('''UPDATE pm_ahp_resource_access SET source_kind=?
                        WHERE adapter_id=? AND binding_digest=? AND resource=? AND source_identity=?''',
                        (source['source_kind'], row[0], row[1], source['resource'], source['source_identity']))
            journal.db.execute('UPDATE pm_ahp_resource_access SET available=0 WHERE source_kind IS NULL')
        for verb in ('UPDATE', 'DELETE'):
            journal.db.execute(f'CREATE TRIGGER IF NOT EXISTS pm_ahp_observations_{verb.lower()}_immutable '
                f'BEFORE {verb} ON pm_ahp_observations '
                "BEGIN SELECT RAISE(ABORT, 'AHP observations are immutable'); END")


def sync_config(ops):
    """Called on installation/config reload before serving global reads.

    The current nonsecret projection supports the shared db-only event/operation
    visibility contract. It contains neither an endpoint nor a credential ref.
    """
    with ops.journal.tx():
        ops.db.execute('DELETE FROM pm_ahp_adapter_grants')
        for adapter in _configured(ops).values():
            ops.db.executemany('INSERT INTO pm_ahp_adapter_grants VALUES(?,?,?)',
                [(adapter.adapter_id, wid, adapter.binding_digest()) for wid in adapter.workspace_ids])


def install(ops):
    schema(ops.journal)
    sync_config(ops)
    if ACTION not in ops.actions:
        ops.register(ActionDef(ACTION, 'observe', 'Read a bounded configured AHP catalog and selected snapshots',
            _run, _admit, ('workspace_id', 'adapter_id'), authorize_existing=_authorize_existing))


def capabilities():
    return {'contract': 'ahp_observer_v1', 'protocol_version': provider.VERSION,
        'protocol_versions': list(provider.PROFILES), 'decoder_profiles': dict(provider.PROFILES),
        'spec_commit': '5f16d81bb7045b66d7bc768d244feab75002d943',
        'offline_wire_sources': [{'distribution': 'microsoft/vscode', 'version': '1.141.0',
            'commit': '2a59476c9bfcb90b3ddc372c36762471b7dfad1c', 'protocol_version': '0.10.0'}],
        'implemented_subset': sorted(provider.METHODS), 'observation': 'supported',
        'create_own': 'unsupported', 'send': 'unsupported', 'cancel': 'unsupported',
        'close': 'unsupported', 'pm_writer': 'unsupported', 'adapter_switch': 'unsupported',
        'manual_foreign_access': 'read_only', 'tested_host_versions': [], 'live_host_verified': False,
        'changesets': 'static_advertised_snapshots_only', 'transcript': 'unsupported',
        'limits': {'seconds': provider.MAX_SECONDS, 'bytes': provider.MAX_BYTES,
            'sessions': provider.MAX_SESSIONS, 'selected_sessions': provider.MAX_SELECTED,
            'changesets': provider.MAX_CHANGESETS, 'reconnects': 1}}


def _member(db, principal, wid):
    if not principal.allows('observe'):
        raise _bad('FORBIDDEN', 403)
    return pm_workspace.require_member(db, principal, wid)


def _adapter(ops, principal, wid, adapter_id):
    _member(ops.db, principal, wid)
    adapter = _configured(ops).get(adapter_id) if isinstance(adapter_id, str) else None
    if not adapter or wid not in adapter.workspace_ids:
        raise _bad('NOT_FOUND', 404)
    return adapter


def _grant(db, principal, wid, adapter_id, binding_digest):
    _member(db, principal, wid)
    row = db.execute('SELECT binding_digest FROM pm_ahp_adapter_grants WHERE adapter_id=? AND workspace_id=?',
        (adapter_id, wid)).fetchone()
    if not row or row[0] != binding_digest:
        raise _bad('NOT_FOUND', 404)


def _sources_access(db, adapter_id, binding_digest, sources):
    for source in sources:
        row = db.execute('''SELECT available,source_kind FROM pm_ahp_resource_access
            WHERE adapter_id=? AND binding_digest=? AND resource=? AND source_identity=?''',
            (adapter_id, binding_digest, source['resource'], source['source_identity'])).fetchone()
        if (not row or row[0] != 1 or row[1] not in {'session', 'changeset'}
                or source.get('source_kind', row[1]) != row[1]):
            raise _bad('NOT_FOUND', 404)


def _project_access(db, adapter_id, binding_digest, evidence):
    """Only positive original URI identities, complete catalogs or explicit missing.

    A session title/path is never an identity. A reused URI with a different
    provider/createdAt does not revive the old record. Failed/truncated catalogs
    leave absent resources' previous access untouched.
    """
    catalog = evidence['positive_catalog']
    if evidence['catalog_complete']:
        db.execute("UPDATE pm_ahp_resource_access SET available=0 WHERE adapter_id=? AND binding_digest=? AND source_kind='session'",
            (adapter_id, binding_digest))
    proofs = list(catalog)
    for source in _document_sources(evidence):
        if (source['source_kind'] == 'changeset' and source['resource'] in evidence['positive_snapshots']
                and any(item['resource'] == evidence['changeset_owners'].get(source['resource']) for item in catalog)):
            proofs.append(source)
    for item in proofs:
        db.execute('''UPDATE pm_ahp_resource_access SET available=0
            WHERE adapter_id=? AND binding_digest=? AND resource=? AND source_identity<>?''',
            (adapter_id, binding_digest, item['resource'], item['source_identity']))
        db.execute('''INSERT INTO pm_ahp_resource_access
            (adapter_id,binding_digest,resource,source_identity,available,source_kind) VALUES(?,?,?,?,1,?)
            ON CONFLICT(adapter_id,binding_digest,resource,source_identity)
            DO UPDATE SET available=1,source_kind=excluded.source_kind''',
            (adapter_id, binding_digest, item['resource'], item['source_identity'], item['source_kind']))
    for resource in evidence['missing']:
        db.execute('UPDATE pm_ahp_resource_access SET available=0 WHERE adapter_id=? AND binding_digest=? AND resource=?',
            (adapter_id, binding_digest, resource))


def _document_sources(evidence):
    receipts = [*evidence['snapshots'], *evidence['events']]
    if any('original_sources' not in receipt for receipt in receipts):
        return _early_sources(evidence)
    sources = [{key: item[key] for key in ('resource', 'source_identity')} | {'source_kind': 'session'}
        for item in evidence['catalog']]
    # Reconnect can fail before a fresh catalog arrives. Retained snapshots and
    # event metadata still belong to their original identities, independently of
    # the final (possibly empty or replaced) catalog. Never orphan those sources.
    for receipt in receipts:
        resource = receipt.get('resource', receipt.get('channel'))
        parent = evidence['changeset_owners'].get(resource, resource)
        refs = receipt['original_sources']
        expected = set() if resource == provider.ROOT else {resource, parent}
        if len(refs) != len(expected) or {source['resource'] for source in refs} != expected:
            raise _bad('AHP_OBSERVATION_CORRUPT')
        identities = {source['resource']: source['source_identity'] for source in refs}
        if resource != parent and identities[resource] != provider.digest({
                'resource': resource, 'session_identity': identities[parent]}):
            raise _bad('AHP_OBSERVATION_CORRUPT')
        for source in receipt['original_sources']:
            # Also reconstruct pre-profile records without modifying their bytes.
            # A source is the original receipt channel or its advertised parent,
            # never a classification inferred from an arbitrary URI scheme.
            kind = ('session' if source['resource'] == parent and parent != provider.ROOT
                else 'changeset' if source['resource'] == resource and resource != parent else None)
            if kind is None or source.get('source_kind', kind) != kind:
                raise _bad('AHP_OBSERVATION_CORRUPT')
            sources.append({**source, 'source_kind': kind})
    return [dict(resource=resource, source_identity=identity, source_kind=kind) for resource, identity, kind in
        sorted({(source['resource'], source['source_identity'], source['source_kind']) for source in sources})]


def _early_sources(evidence):
    """Narrow reconstruction of pre-7a receipts, solely from their frozen facts."""
    receipts = [*evidence['snapshots'], *evidence['events']]
    if (evidence['protocol_version'] != '1.0.0' or evidence['reconnect'] != 'not_needed'
            or 'negotiated_version' in evidence or 'decoder_profile' in evidence
            or evidence['gaps'] or evidence['missing'] or evidence['notifications']
            or evidence['status'] != 'observed' or evidence['catalog_complete'] is not True
            or any('original_sources' in receipt for receipt in receipts)):
        raise _bad('AHP_OBSERVATION_CORRUPT')
    catalog = {item['resource']: item for item in evidence['catalog']}
    snapshots = {item['resource']: item for item in evidence['snapshots']}
    if len(catalog) != len(evidence['catalog']) or len(snapshots) != len(evidence['snapshots']):
        raise _bad('AHP_OBSERVATION_CORRUPT')
    sources = {}
    for resource, item in catalog.items():
        identity = provider.digest({key: item[key] for key in ('resource', 'provider', 'createdAt')})
        if identity != item['source_identity']:
            raise _bad('AHP_OBSERVATION_CORRUPT')
        sources[resource] = {'resource': resource, 'source_identity': identity, 'source_kind': 'session'}
    for receipt in receipts:
        resource = receipt.get('resource', receipt.get('channel'))
        if resource == provider.ROOT:
            continue
        parent = evidence['changeset_owners'].get(resource, resource)
        state, item = snapshots[parent]['state'], catalog[parent]
        # Earliest records did not bind each receipt to a catalog incarnation.
        # Only a saved state that independently carries that identity can recover.
        if any(state.get(key) != item[key] for key in ('provider', 'createdAt')):
            raise _bad('AHP_OBSERVATION_CORRUPT')
        if resource != parent:
            if (resource not in snapshots or not any(entry['uriTemplate'] == resource
                    and entry['read_supported'] is True for entry in state['changesets'])):
                raise _bad('AHP_OBSERVATION_CORRUPT')
            sources[resource] = {'resource': resource, 'source_kind': 'changeset',
                'source_identity': provider.digest({'resource': resource,
                    'session_identity': sources[parent]['source_identity']})}
    return [sources[key] for key in sorted(sources)]


def _covered_sources(document):
    """An empty/incomplete saved reference list must never orphan private facts.

    This gate precedes every historical read, including db-only global visibility.
    It neither changes old bytes nor obtains replacement identities from live state.
    """
    try:
        required = _document_sources(document['evidence'])
        kinds = {(item['resource'], item['source_identity']): item['source_kind'] for item in required}
        original = document['original_sources']
        if len(original) != len(kinds) or len(required) != len(kinds):
            raise ValueError('source coverage differs')
        actual = set()
        for source in original:
            key = (source['resource'], source['source_identity'])
            if key not in kinds or source.get('source_kind', kinds[key]) != kinds[key]:
                raise ValueError('source identity differs')
            actual.add(key)
        if actual != set(kinds):
            raise ValueError('source coverage differs')
        return required
    except (KeyError, TypeError, ValueError, AttributeError, OperationError):
        raise _bad('NOT_FOUND', 404) from None


def _live(ops, binding):
    original = binding.get('principal', {})
    cid = binding.get('credential_id')
    if not isinstance(cid, str) or not re.fullmatch(r'[0-9a-f]{64}', cid):
        raise _bad('AHP_AUTHORITY_UNAVAILABLE', 403)
    if original.get('admin') is True:
        daemon = ops.context.get('daemon')
        token = vars(daemon).get('_admin_token') if daemon is not None else None
        if not isinstance(token, str) or api_auth._digest(token) != cid:
            raise _bad('AHP_AUTHORITY_UNAVAILABLE', 403)
        principal = api_auth.authenticate(ops.db, token, token)
    else:
        row = ops.db.execute('SELECT actor,scopes,expires_at,revoked_at FROM api_principals WHERE token_hash=?', (cid,)).fetchone()
        if not row or row['revoked_at'] is not None or row['expires_at'] is not None and row['expires_at'] <= time.time():
            raise _bad('AHP_AUTHORITY_UNAVAILABLE', 403)
        principal = api_auth.Principal(row['actor'], frozenset(json.loads(row['scopes'])), credential_id=cid)
    if principal.actor != original.get('actor') or not principal.allows('observe'):
        raise _bad('AHP_AUTHORITY_UNAVAILABLE', 403)
    return principal


def _validate(target, params, pre):
    if (set(target) != {'workspace_id', 'adapter_id'} or set(params) - {'session_uris', 'include_changesets'} or pre
            or not isinstance(target['adapter_id'], str) or not ADAPTER_ID.fullmatch(target['adapter_id'])):
        raise _bad('INVALID_PARAMS', 422)
    selected = params.get('session_uris', [])
    if (not isinstance(selected, list) or len(selected) > provider.MAX_SELECTED
            or any(not provider.session_uri(uri) for uri in selected)
            or len(set(selected)) != len(selected) or type(params.get('include_changesets', False)) is not bool):
        raise _bad('INVALID_PARAMS', 422)


def _admit(ops, principal, target, params, pre):
    _validate(target, params, pre)
    adapter = _adapter(ops, principal, target['workspace_id'], target['adapter_id'])
    binding = {'principal': pm_workspace.principal_binding(principal), 'credential_id': principal.credential_id,
        'ahp_binding_digest': adapter.binding_digest(),
        'lifecycle': pm_lifecycle_gates.head(ops.db, target['workspace_id'])}
    _live(ops, binding)
    return binding


def operation_visible(db, principal, operation):
    with _snapshot(db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='pm_ahp_adapter_grants'").fetchone():
            return False
        row = db.execute('SELECT * FROM operations WHERE operation_id=?', (operation.get('operation_id'),)).fetchone()
        if not row or row['action'] != ACTION:
            return False
        op = OperationService._decode(row)
        binding = (op.get('external_refs') or {}).get('admission_binding') or {}
        try:
            _grant(db, principal, op['target']['workspace_id'], op['target']['adapter_id'], binding['ahp_binding_digest'])
            saved = db.execute('SELECT document,document_digest FROM pm_ahp_observations WHERE operation_id=?', (op['operation_id'],)).fetchone()
            if saved:
                document = json.loads(saved['document'])
                if provider.digest(document) != saved['document_digest']:
                    return False
                _sources_access(db, op['target']['adapter_id'], binding['ahp_binding_digest'], _covered_sources(document))
            else:
                for resource in op['params'].get('session_uris', []):
                    rows = db.execute('SELECT available FROM pm_ahp_resource_access WHERE adapter_id=? AND binding_digest=? AND resource=?',
                        (op['target']['adapter_id'], binding['ahp_binding_digest'], resource)).fetchall()
                    if rows and not any(row[0] == 1 for row in rows):
                        return False
            return True
        except (OperationError, KeyError, TypeError, ValueError):
            return False


def _authorize_existing(ops, principal, operation, verb):
    if not operation_visible(ops.db, principal, operation):
        raise _bad('NOT_FOUND', 404)
    adapter = _adapter(ops, principal, operation['target']['workspace_id'], operation['target']['adapter_id'])
    if adapter.binding_digest() != operation['external_refs']['admission_binding']['ahp_binding_digest']:
        raise _bad('NOT_FOUND', 404)


def _bounded(value):
    if len(_json(value).encode()) > MAX_READ_BYTES:
        raise _bad('AHP_READ_TOO_LARGE', 413)
    return value


async def _run(ctx):
    name, ops = 'pm.ahp.record', ctx.service
    saved = ops.db.execute('SELECT status FROM operation_steps WHERE operation_id=? AND name=?', (ctx.operation_id, name)).fetchone()
    if saved and saved[0] == 'succeeded':
        return ctx.effect(name, lambda: {})
    binding = ctx.admission_binding or {}

    def check():
        ctx.check_cancel()
        principal = _live(ops, binding)
        adapter = _adapter(ops, principal, ctx.target['workspace_id'], ctx.target['adapter_id'])
        current = pm_lifecycle_gates.head(ops.db, ctx.target['workspace_id'])
        if (adapter.binding_digest() != binding.get('ahp_binding_digest')
                or current['revision'] != binding['lifecycle']['revision']
                or current['execution_epoch'] != binding['lifecycle']['execution_epoch']):
            raise _bad('AHP_SOURCE_OR_LIFECYCLE_CHANGED')
        return adapter

    adapter = check()
    started = ctx.effect('pm.ahp.started', lambda: {'started_at': time.time()})
    remaining = started['started_at'] + provider.MAX_SECONDS - time.time()
    if not 0 < remaining <= provider.MAX_SECONDS:
        raise _bad('AHP_ORIGINAL_DEADLINE_EXPIRED')
    # Per-service bounds; no second daemon, background poller or borrowed socket.
    active = ops.context.setdefault('ahp_active', Counter())
    actor_key, adapter_key = ('actor', ctx.actor), ('adapter', adapter.adapter_id)
    if active['total'] >= 16 or active[actor_key] >= 2 or active[adapter_key] >= 4:
        raise _bad('AHP_OBSERVER_BUSY', 429)
    for key in ('total', actor_key, adapter_key):
        active[key] += 1
    try:
        observer = provider.Observer(adapter, client_id='batc-' + ctx.operation_id,
            selected=ctx.params.get('session_uris', []), changesets=ctx.params.get('include_changesets', False), check=check)
        try:
            evidence = await asyncio.wait_for(observer.collect(), remaining)
        except asyncio.TimeoutError:
            raise _bad('AHP_ORIGINAL_DEADLINE_EXPIRED') from None
        except provider.ObserverError:
            # Final secret minimization must refuse a structural/identity echo,
            # not publish a redacted URI as positive native-source evidence.
            raise _bad('AHP_PRIVATE_IDENTITY_REFUSED') from None
    finally:
        for key in ('total', actor_key, adapter_key):
            active[key] -= 1
            if active[key] == 0:
                del active[key]

    def record():
        check()
        now, oid = time.time(), 'pmah_' + ctx.operation_id[3:]
        document = {'schema': 'ahp_observation_v1', 'observation_id': oid, 'workspace_id': ctx.target['workspace_id'],
            'adapter_id': adapter.adapter_id, 'binding_digest': binding['ahp_binding_digest'],
            'started_at': started['started_at'], 'finished_at': now, 'evidence': evidence}
        document['original_sources'] = _document_sources(evidence)
        _bounded(document)
        doc_digest = provider.digest(document)
        _project_access(ops.db, adapter.adapter_id, binding['ahp_binding_digest'], evidence)
        ops.db.execute('''INSERT INTO pm_ahp_observations(observation_id,workspace_id,adapter_id,binding_digest,
            document,document_digest,created_at,actor,operation_id) VALUES(?,?,?,?,?,?,?,?,?)''',
            (oid, ctx.target['workspace_id'], adapter.adapter_id, binding['ahp_binding_digest'], _json(document),
             doc_digest, now, ctx.actor, ctx.operation_id))
        result = {'workspace_id': ctx.target['workspace_id'], 'adapter_id': adapter.adapter_id,
            'observation_id': oid, 'document_digest': doc_digest, 'coverage_status': evidence['status']}
        ops.journal.api_event('pm_ahp_observation', oid, 'pm.ahp.observed', {**result, 'operation_id': ctx.operation_id}, actor=ctx.actor)
        return result
    return ctx.effect(name, record)


@_snapshot_read
def adapters(ops, principal, wid):
    _member(ops.db, principal, wid)
    return {'workspace_id': wid, 'adapters': [{'adapter_id': adapter.adapter_id,
        'binding_digest': adapter.binding_digest(), **capabilities()} for adapter in _configured(ops).values()
        if wid in adapter.workspace_ids]}


def _view(ops, principal, wid, row, *, detail):
    adapter = _adapter(ops, principal, wid, row['adapter_id'])
    _grant(ops.db, principal, wid, row['adapter_id'], row['binding_digest'])
    if adapter.binding_digest() != row['binding_digest']:
        raise _bad('NOT_FOUND', 404)
    doc = json.loads(row['document'])
    if (provider.digest(doc) != row['document_digest'] or doc['workspace_id'] != wid
            or doc['observation_id'] != row['observation_id'] or doc['binding_digest'] != row['binding_digest']):
        raise _bad('AHP_OBSERVATION_CORRUPT')
    _sources_access(ops.db, row['adapter_id'], row['binding_digest'], _covered_sources(doc))
    out = {key: row[key] for key in ('sequence', 'observation_id', 'workspace_id', 'adapter_id',
        'document_digest', 'created_at', 'actor', 'operation_id')}
    out['coverage_status'] = doc['evidence']['status']
    if detail:
        out['document'] = doc
    return out


@_snapshot_read
def get_observation(ops, principal, wid, oid):
    _member(ops.db, principal, wid)
    if not isinstance(oid, str) or not ID.fullmatch(oid):
        raise _bad('NOT_FOUND', 404)
    row = ops.db.execute('SELECT * FROM pm_ahp_observations WHERE workspace_id=? AND observation_id=?', (wid, oid)).fetchone()
    if not row:
        raise _bad('NOT_FOUND', 404)
    return _bounded({'workspace_id': wid, 'observation': _view(ops, principal, wid, row, detail=True)})


@_snapshot_read
def list_observations(ops, principal, wid, *, after=0, limit=20):
    _member(ops.db, principal, wid)
    if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 50:
        raise _bad('INVALID_PARAMS', 422)
    rows = ops.db.execute('SELECT * FROM pm_ahp_observations WHERE workspace_id=? AND sequence>? ORDER BY sequence LIMIT ?',
        (wid, after, limit + 1)).fetchall()
    items, filtered = [], False
    for row in rows[:limit]:
        try:
            items.append(_view(ops, principal, wid, row, detail=False))
        except OperationError as exc:
            if exc.status != 404:
                raise
            filtered = True
    return _bounded({'workspace_id': wid, 'observations': items, 'visibility_filtered': filtered,
        'next_cursor': rows[limit - 1]['sequence'] if len(rows) > limit else None})


@_snapshot_read
def current_observation(ops, principal, wid):
    _member(ops.db, principal, wid)
    row = ops.db.execute('SELECT * FROM pm_ahp_observations WHERE workspace_id=? ORDER BY sequence DESC LIMIT 1', (wid,)).fetchone()
    try:
        value = _view(ops, principal, wid, row, detail=True) if row else None
        return _bounded({'workspace_id': wid, 'observation': value, 'visibility_filtered': False})
    except OperationError as exc:
        if exc.status != 404:
            raise
        return {'workspace_id': wid, 'observation': None, 'visibility_filtered': True}
