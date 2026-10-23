"""Explicit fixed-history references: local effects, receipts and current access."""
from __future__ import annotations

import json
import time

from . import pm_evidence, pm_source_lineage, pm_workspace, work_items
from . import pm_external_history as refs
from .operations import ActionDef, OperationError

ACTIONS = ('pm.history.link', 'pm.history.unlink')


def _changed():
    return OperationError('PM_HISTORY_LINK_CHANGED', 'Read the current history relationship', 409)


def install(ops):
    with ops.journal.tx():
        for sql in (
            '''CREATE TABLE IF NOT EXISTS pm_external_excerpts (
                excerpt_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, source_id TEXT NOT NULL,
                entry_id TEXT NOT NULL, original_binding TEXT NOT NULL, canonical_ref TEXT NOT NULL UNIQUE,
                excerpt_digest TEXT NOT NULL, creation_operation_id TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS pm_external_links (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, link_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL, work_item_id TEXT NOT NULL, attempt_id TEXT,
                excerpt_id TEXT NOT NULL REFERENCES pm_external_excerpts(excerpt_id),
                purpose TEXT NOT NULL CHECK(purpose IN ('reference','derived_origin')),
                version INTEGER NOT NULL CHECK(version>0), state TEXT NOT NULL CHECK(state IN ('active','withdrawn')),
                created_by TEXT NOT NULL, created_at REAL NOT NULL, creation_operation_id TEXT NOT NULL,
                last_operation_id TEXT NOT NULL)''',
            '''CREATE UNIQUE INDEX IF NOT EXISTS pm_external_links_active ON pm_external_links
                (workspace_id,work_item_id,excerpt_id,COALESCE(attempt_id,'')) WHERE state='active' ''',
            '''CREATE TABLE IF NOT EXISTS pm_external_link_events (
                link_id TEXT NOT NULL, version INTEGER NOT NULL, operation_id TEXT NOT NULL,
                actor TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('created','withdrawn','replaced')),
                created_at REAL NOT NULL, replacement_id TEXT, PRIMARY KEY(link_id,version))''',
            '''CREATE TRIGGER IF NOT EXISTS pm_external_links_identity BEFORE UPDATE ON pm_external_links
                WHEN NEW.link_id<>OLD.link_id OR NEW.workspace_id<>OLD.workspace_id
                OR NEW.work_item_id<>OLD.work_item_id OR NEW.attempt_id IS NOT OLD.attempt_id
                OR NEW.excerpt_id<>OLD.excerpt_id OR NEW.purpose<>OLD.purpose
                OR NEW.created_by<>OLD.created_by OR NEW.created_at<>OLD.created_at
                OR NEW.creation_operation_id<>OLD.creation_operation_id
                OR NEW.version<>OLD.version+1 OR OLD.state<>'active' OR NEW.state<>'withdrawn'
                BEGIN SELECT RAISE(ABORT,'history relationship identity is immutable'); END''',
        ):
            ops.db.execute(sql)
        for table in ('pm_external_excerpts', 'pm_external_link_events', 'pm_external_links'):
            for verb in (('DELETE',) if table == 'pm_external_links' else ('UPDATE', 'DELETE')):
                ops.db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_immutable_{verb.lower()}
                    BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'history relationship is immutable'); END''')
    for action in ACTIONS:
        if action in ops.actions:
            continue
        keys = ('workspace_id', 'work_item_id') + (('link_id',) if action == ACTIONS[1] else ())
        ops.register(ActionDef(action, 'manage', 'Record an external reference without execution', _run,
            _admitter(action), keys, authorize_existing=_authorize_existing))


def _member(db, principal, wid, *, write=False):
    # A global administrator is not an explicit workspace member or source reader.
    row = db.execute('SELECT role FROM pm_workspace_members WHERE workspace_id=? AND actor=?', (wid, principal.actor)).fetchone()
    if (not principal.allows('observe') or write and not principal.allows('manage') or row is None
            or write and row['role'] == 'reader'):
        raise refs.unavailable()
    pm_workspace.require_member(db, principal, wid)
    return row['role']


def _work(db, principal, wid, wi):
    _member(db, principal, wid)
    if not isinstance(wi, str):
        raise refs.unavailable()
    pm_source_lineage.require(db, principal, 'work_item', wi)
    binding = pm_evidence._binding(db, wid, wi)
    if binding is None:
        raise refs.unavailable()
    item = work_items._get_item(db, wi)
    if binding['project_id'] != item['project_id']:
        raise refs.unavailable()
    return item, binding


def _link(db, wid, identity):
    if not isinstance(identity, str):
        raise refs.unavailable()
    row = db.execute('SELECT * FROM pm_external_links WHERE workspace_id=? AND link_id=?', (wid, identity)).fetchone()
    if row is None:
        raise refs.unavailable()
    return dict(row)


def _cleanup(db, principal, wid, identity):
    row = _link(db, wid, identity)
    role = _member(db, principal, wid)
    _work(db, principal, wid, row['work_item_id'])
    if role != 'owner' and row['created_by'] != principal.actor:
        raise refs.unavailable()
    return row


def _source_binding(db, principal, ref):
    refs.resolve(db, principal, ref)
    with refs.current_grants(db, principal, ref['workspace_id']) as grants:
        source = next((g for g in grants if g['source_id'] == ref['source_id']), None)
        if source is None:
            raise refs.unavailable()
        return source['binding']


def _check(ops, principal, action, target, params, pre):
    db, wid, wi = ops.db, target['workspace_id'], target['work_item_id']
    unlink = action == ACTIONS[1]
    if set(target) != ({'workspace_id', 'work_item_id', 'link_id'} if unlink else {'workspace_id', 'work_item_id'}):
        raise refs.invalid()
    _member(db, principal, wid, write=not unlink)
    item, binding = _work(db, principal, wid, wi)
    if unlink:
        if params or set(pre) != {'expected_link_version'}:
            raise refs.invalid()
        old = _cleanup(db, principal, wid, target['link_id'])
        if old['work_item_id'] != wi:
            raise refs.unavailable()
    else:
        if (set(params) - {'fixed_ref', 'attempt_id', 'replaces_link_id', 'expected_old_link_version'}
                or set(pre) != {'expected_binding_id', 'expected_work_version'}):
            raise refs.invalid()
        ref = params.get('fixed_ref')
        if not isinstance(ref, dict) or ref.get('workspace_id') != wid:
            raise refs.unavailable()
        original = _source_binding(db, principal, ref)
        saved = db.execute('SELECT original_binding FROM pm_external_excerpts WHERE canonical_ref=?', (refs.canonical(ref),)).fetchone()
        if saved and saved[0] != original:
            raise refs.unavailable()
        if (type(pre['expected_work_version']) is not int or pre['expected_work_version'] != item['version']
                or pre['expected_binding_id'] != binding['binding_id']):
            raise _changed()
        if 'attempt_id' in params:
            from .pm_work_start import get_attempt
            try:
                attempt = get_attempt(ops, principal, wid, params['attempt_id'])['attempt']
            except OperationError:
                raise refs.unavailable() from None
            if attempt['workspace_id'] != wid or attempt['work_item_id'] != wi:
                raise OperationError('PM_HISTORY_ATTEMPT_MISMATCH', 'Select an Attempt from this Work', 409)
        if ('replaces_link_id' in params) != ('expected_old_link_version' in params):
            raise refs.invalid()
        old = _cleanup(db, principal, wid, params['replaces_link_id']) if 'replaces_link_id' in params else None
    if old:
        version = pre['expected_link_version'] if unlink else params['expected_old_link_version']
        if type(version) is not int or old['version'] != version or old['state'] != 'active':
            raise _changed()
    return binding, old


def _admitter(action):
    def admit(ops, principal, target, params, pre):
        binding, old = _check(ops, principal, action, target, params, pre)
        return {'principal': pm_workspace.principal_binding(principal), 'history_binding': binding['binding_id'],
            'history_source_binding': _source_binding(ops.db, principal, params['fixed_ref']) if action == ACTIONS[0] else None,
            'history_old_binding': pm_evidence._binding(ops.db, old['workspace_id'], old['work_item_id'])['binding_id'] if old else None}
    return admit


def _authorize_existing(ops, principal, op, verb):
    if (not principal.allows('manage') or not operation_visible(ops.db, principal, op)
            or verb != 'replay' and op['actor'] != principal.actor):
        raise refs.unavailable()


def operation_visible(db, principal, op):
    row = db.execute('SELECT action,target,params,external_refs FROM operations WHERE operation_id=?', (op.get('operation_id'),)).fetchone()
    if row is None:
        return False
    target, params = json.loads(row['target']), json.loads(row['params'])
    try:
        wid = target['workspace_id']
        _work(db, principal, wid, target['work_item_id'])
        if row['action'] == ACTIONS[1]:
            _cleanup(db, principal, wid, target['link_id'])
        else:
            original = _source_binding(db, principal, params['fixed_ref'])
            if original != json.loads(row['external_refs'])['admission_binding']['history_source_binding']:
                return False
            if params.get('replaces_link_id'):
                _cleanup(db, principal, wid, params['replaces_link_id'])
            if params.get('attempt_id'):
                from .pm_access import visible
                attempt = db.execute('SELECT start_operation_id,origin_id FROM work_attempts WHERE workspace_id=? AND attempt_id=?',
                    (wid, params['attempt_id'])).fetchone()
                if attempt is None:
                    return False
                original = db.execute('SELECT * FROM operations WHERE operation_id=?', (attempt[0] or attempt[1],)).fetchone()
                if original is None or not visible(db, principal, {**dict(original), 'target': json.loads(original['target'])}):
                    return False
        return True
    except OperationError:
        return False


def _receipt(row):
    return {key: row[key] for key in ('link_id', 'state', 'version')}


async def _run(ctx):
    def apply():
        ops, db, principal = ctx.service, ctx.service.db, pm_workspace.admitted_principal(ctx)
        wid, wi = ctx.target['workspace_id'], ctx.target['work_item_id']
        binding, old = _check(ops, principal, ctx.op['action'], ctx.target, ctx.params, ctx.preconditions)
        if (binding['binding_id'] != ctx.admission_binding['history_binding'] or old and
                pm_evidence._binding(db, wid, old['work_item_id'])['binding_id'] != ctx.admission_binding['history_old_binding']):
            raise _changed()
        if ctx.op['action'] == ACTIONS[0] and _source_binding(db, principal, ctx.params['fixed_ref']) != ctx.admission_binding['history_source_binding']:
            raise refs.unavailable()
        now = time.time()
        def event(identity, version, kind, replacement=None):
            db.execute('INSERT INTO pm_external_link_events VALUES(?,?,?,?,?,?,?)',
                (identity, version, ctx.operation_id, ctx.actor, kind, now, replacement))
        if ctx.op['action'] == ACTIONS[0]:
            ref = ctx.params['fixed_ref']
            excerpt = 'hxe_' + refs._digest(ref)
            original = db.execute('SELECT binding FROM temp.session_history_current_grants WHERE source_id=? AND actor=?',
                (ref['source_id'], principal.actor)).fetchone()[0]
            db.execute('INSERT OR IGNORE INTO pm_external_excerpts VALUES(?,?,?,?,?,?,?,?)',
                (excerpt, wid, ref['source_id'], ref['entry_id'], original, refs.canonical(ref), ref['excerpt_digest'], ctx.operation_id))
            found = db.execute("SELECT * FROM pm_external_links WHERE workspace_id=? AND work_item_id=? AND excerpt_id=? AND attempt_id IS ? AND state='active'",
                (wid, wi, excerpt, ctx.params.get('attempt_id'))).fetchone()
            identity = found['link_id'] if found else 'hxl_' + ctx.operation_id[3:]
            if not found:
                db.execute('''INSERT INTO pm_external_links(link_id,workspace_id,work_item_id,attempt_id,excerpt_id,purpose,
                    version,state,created_by,created_at,creation_operation_id,last_operation_id) VALUES(?,?,?,?,?,'reference',1,'active',?,?,?,?)''',
                    (identity, wid, wi, ctx.params.get('attempt_id'), excerpt, ctx.actor, now, ctx.operation_id, ctx.operation_id))
                event(identity, 1, 'created')
        else:
            identity = None
        if old and old['link_id'] != identity:
            updated = db.execute("UPDATE pm_external_links SET state='withdrawn',version=version+1,last_operation_id=? WHERE link_id=? AND version=? AND state='active'",
                (ctx.operation_id, old['link_id'], old['version']))
            if updated.rowcount != 1:
                raise _changed()
            event(old['link_id'], old['version'] + 1, 'replaced' if identity else 'withdrawn', identity)
        return _receipt(_link(db, wid, identity or old['link_id']))
    return ctx.effect('pm.history.apply', apply)


def excerpt_get(ops, principal, workspace_id, excerpt_id):
    row = ops.db.execute('SELECT * FROM pm_external_excerpts WHERE workspace_id=? AND excerpt_id=?', (workspace_id, excerpt_id)).fetchone()
    _member(ops.db, principal, workspace_id)
    if row is None:
        raise refs.unavailable()
    with refs.current_grants(ops.db, principal, workspace_id) as grants:
        if not any(g['source_id'] == row['source_id'] and g['binding'] == row['original_binding'] for g in grants):
            raise refs.unavailable()
        result = refs.resolve(ops.db, principal, json.loads(row['canonical_ref']))
        return {'excerpt_id': excerpt_id, **result}


def link_get(ops, principal, workspace_id, link_id):
    row = _link(ops.db, workspace_id, link_id)
    _work(ops.db, principal, workspace_id, row['work_item_id'])
    excerpt = excerpt_get(ops, principal, workspace_id, row['excerpt_id'])
    if row['attempt_id']:
        from .pm_work_start import get_attempt
        get_attempt(ops, principal, workspace_id, row['attempt_id'])
    return {'link': {k: v for k, v in row.items() if k != 'sequence'} | {'fixed_ref': excerpt['ref']}}


READS = {
    'pm_history_excerpt_preview': ('workspace_id', 'source_id', 'entry_id', 'expected_content_digest', 'selection'),
    'pm_history_excerpt_get': ('workspace_id', 'excerpt_id'),
    'pm_history_links_list': ('workspace_id', 'work_item_id', 'after', 'limit'),
    'pm_history_link_get': ('workspace_id', 'link_id'),
    'pm_history_associations_list': ('workspace_id', 'source_id', 'entry_id', 'after', 'limit'),
}


def read(ops, principal, method, params):
    wid = params.get('workspace_id')
    if any(not isinstance(v, str) for k, v in params.items() if k.endswith('_id')):
        raise refs.invalid()
    _member(ops.db, principal, wid)
    with refs.current_grants(ops.db, principal, wid) as grants:
        if method == 'pm_history_excerpt_preview':
            return refs.preview(ops.db, principal, **{key: params.get(key) for key in READS[method]})
        if method == 'pm_history_excerpt_get':
            return excerpt_get(ops, principal, wid, params.get('excerpt_id'))
        if method == 'pm_history_link_get':
            return link_get(ops, principal, wid, params.get('link_id'))
        after, limit = params.get('after', 0), params.get('limit', 50)
        if type(after) is not int or not 0 <= after < 2**63 or type(limit) is not int or not 1 <= limit <= 50:
            raise refs.invalid()
        source = method == 'pm_history_associations_list'
        if source:
            if not any(g['source_id'] == params.get('source_id') for g in grants):
                raise refs.unavailable()
        else:
            _work(ops.db, principal, wid, params.get('work_item_id'))
        rows = ops.db.execute('''SELECT l.link_id,l.sequence FROM pm_external_links l
            JOIN pm_external_excerpts e USING(excerpt_id)
            JOIN temp.session_history_current_grants g ON g.source_id=e.source_id AND g.workspace_id=e.workspace_id
                AND g.binding=e.original_binding AND g.actor=?
            WHERE l.workspace_id=? AND l.sequence>?
            AND l.state='active' AND ((? AND e.source_id=? AND e.entry_id=?) OR (NOT ? AND l.work_item_id=?))
            ORDER BY l.sequence LIMIT ?''', (principal.actor, wid, after, source, params.get('source_id'), params.get('entry_id'),
                source, params.get('work_item_id'), limit + 1)).fetchall()
        links = []
        for row in rows[:limit]:
            try:
                links.append(link_get(ops, principal, wid, row['link_id'])['link'])
            except OperationError:
                continue
        return {'links': links, 'next_after': rows[limit - 1]['sequence'] if len(rows) > limit else None}


def capabilities():
    return {'version': 1, 'refs': [refs.SCHEMA], 'max_codepoints': refs.MAX_CODEPOINTS,
        'max_preview_bytes': refs.MAX_PREVIEW_BYTES, 'actions': list(ACTIONS), 'derive_ready': False}
