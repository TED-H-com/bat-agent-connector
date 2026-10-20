"""Durable, one-way GitHub to Connector project boards. See docs/design/board-github-sync.md."""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time

from . import api_auth, pm_evidence, pm_workspace, work_items
from .board_github_provider import INTERVAL, BoardGitHubError, BoardGitHubProvider
from .operations import ActionDef, OperationError

URL = re.compile(r'https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9._-]+)/(?:issues|pull)/([1-9][0-9]{0,8})(?![0-9A-Za-z_/])', re.I)


def repository(value):
    if not isinstance(value, str):
        raise OperationError('INVALID_PARAMS', 'Use a GitHub repository such as owner/repo', 422)
    value = value.strip()
    for prefix in ('https://github.com/', 'git@github.com:', 'ssh://git@github.com/'):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    value = value.removesuffix('/').removesuffix('.git')
    if not work_items._REPO.fullmatch(value) or value.endswith(('/.', '/..')):
        raise OperationError('INVALID_PARAMS', 'Use a GitHub repository such as owner/repo', 422)
    return value.lower()


def suggestion(values):
    found = set()
    for value in values:
        try:
            found.add(repository(value))
        except OperationError:
            pass
    return next(iter(found)) if len(found) == 1 else None


def install(ops):
    with ops.journal.tx():
        if 'github_external' not in {r[1] for r in ops.db.execute('PRAGMA table_info(work_items)')}:
            ops.db.execute('ALTER TABLE work_items ADD COLUMN github_external TEXT')
        ops.db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS work_items_github_identity ON work_items(
            project_id,json_extract(github_external,'$.repository'),json_extract(github_external,'$.number'))
            WHERE github_external IS NOT NULL''')
        ops.db.execute('''CREATE TABLE IF NOT EXISTS board_github_sync (
            project_id TEXT PRIMARY KEY REFERENCES projects(project_id), repository TEXT NOT NULL,
            workspace_id TEXT, revision INTEGER NOT NULL, enabled INTEGER NOT NULL,
            principal TEXT NOT NULL, credential_id TEXT, last_sync REAL, next_sync REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'not_synced', failures INTEGER NOT NULL DEFAULT 0,
            authenticated INTEGER, pages TEXT NOT NULL DEFAULT '{}', operation_id TEXT)''')
    ops.context['board_github_provider'] = BoardGitHubProvider(lambda: ops.context['github_config'])
    ops.context['board_github_lock'] = asyncio.Lock()
    for name, run, keys in (
        ('project.github.configure', configure, ('project_id',)),
        ('project.github.import', sync, ('project_id',)),
        ('project.github.sync', sync, ('project_id',)),
        ('pm.github.import', sync, ('workspace_id',)),
    ):
        if name not in ops.actions:
            ops.register(ActionDef(name, 'manage', 'Import or synchronize a GitHub project board', run,
                admitter(name), keys, authorize_existing=authorize_existing))


def settings(db, pid, ops=None):
    row = db.execute('SELECT * FROM board_github_sync WHERE project_id=?', (pid,)).fetchone()
    if row:
        return {k: row[k] for k in ('project_id', 'repository', 'workspace_id', 'revision', 'status', 'last_sync', 'next_sync')} | {
            'enabled': bool(row['enabled']), 'authenticated': None if row['authenticated'] is None else bool(row['authenticated'])}
    project = work_items._get_project(db, pid)
    values = list(project['repositories'])
    if ops is not None and project['task_project']:
        daemon = ops.context.get('daemon')
        if daemon is not None:
            values.append(daemon.adapter.verifier.settings.repo_urls.get(project['task_project']))
    return {'project_id': pid, 'repository': suggestion(values), 'workspace_id': None,
        'revision': 0, 'enabled': False, 'status': 'not_synced', 'last_sync': None, 'next_sync': 0, 'authenticated': None}


def workspace_settings(ops, principal, wid):
    workspace = pm_workspace.require_member(ops.db, principal, wid)
    rows = ops.db.execute('''SELECT p.* FROM projects p JOIN pm_project_bindings b USING(project_id)
        WHERE b.workspace_id=? AND p.archived_at IS NULL ORDER BY b.sequence''', (wid,)).fetchall()
    return {'projects': [{'project_id': row['project_id'], 'name': row['name'],
        **settings(ops.db, row['project_id'], ops)} for row in rows],
        'suggested_repository': suggestion(workspace['repositories'])}


def _check(ops, principal, action, target, params, pre):
    if not principal.allows('manage'):
        raise OperationError('FORBIDDEN', 'Board import requires project management access', 403)
    imported = action == 'pm.github.import'
    if set(target) != ({'workspace_id'} if imported else {'project_id'}):
        raise OperationError('INVALID_TARGET', 'Supply the exact project or workspace', 422)
    work_items._only(params, ('repository', 'enabled', 'project_id') if imported else
        ('repository', 'enabled') if action.endswith(('configure', 'import')) else ())
    pid = params.get('project_id') if imported else target['project_id']
    workspace = None
    if imported:
        workspace = pm_workspace.require_member(ops.db, principal, target['workspace_id'], write=True, decide=True)
        if pid is not None and not ops.db.execute('SELECT 1 FROM pm_project_bindings WHERE workspace_id=? AND project_id=?',
                (target['workspace_id'], pid)).fetchone():
            raise OperationError('PM_PROJECT_BINDING_REQUIRED', 'Choose an associated project', 409)
    if imported and pid is None:
        requested = repository(params.get('repository'))
        candidates = [r[0] for r in ops.db.execute('SELECT project_id FROM pm_project_bindings WHERE workspace_id=?', (target['workspace_id'],))
            if settings(ops.db, r[0], ops).get('repository') == requested]
        if len(candidates) > 1:
            raise OperationError('INVALID_PARAMS', 'Choose the project to synchronize', 422)
        if candidates:
            pid = candidates[0]
    current = settings(ops.db, pid, ops) if pid else {'revision': 0, 'repository': None, 'workspace_id': None}
    if pid:
        work_items._get_project(ops.db, pid, active=True)
    if imported and current.get('workspace_id') not in (None, target['workspace_id']):
        raise OperationError('VERSION_CONFLICT', 'This project already synchronizes another workspace board', 409)
    if current.get('workspace_id'):
        pm_workspace.require_member(ops.db, principal, current['workspace_id'], write=True, decide=True)
    if set(pre) != {'expected_revision'} or type(pre['expected_revision']) is not int:
        raise OperationError('PRECONDITION_REQUIRED', 'Read the current GitHub sync revision', 422)
    if pre['expected_revision'] != current['revision']:
        raise OperationError('VERSION_CONFLICT', 'GitHub sync settings changed; read them again', 409)
    repo = repository(params.get('repository', current['repository']))
    if 'enabled' in params and type(params['enabled']) is not bool:
        raise OperationError('INVALID_PARAMS', 'enabled must be true or false', 422)
    return pid, repo, current, workspace


def admitter(action):
    def admit(ops, principal, target, params, pre):
        _, _, current, workspace = _check(ops, principal, action, target, params, pre)
        wid = workspace['workspace_id'] if workspace else current.get('workspace_id')
        return {'principal': pm_workspace.principal_binding(principal), 'credential_id': principal.credential_id,
            **({'pm_lineage': {'workspace_id': wid}} if wid else {})}
    return admit


def authorize_existing(ops, principal, op, control):
    if op['target'].get('workspace_id'):
        pm_workspace.require_member(ops.db, principal, op['target']['workspace_id'], write=True, decide=True)
    elif op['target'].get('project_id'):
        current = settings(ops.db, op['target']['project_id'])
        if current['workspace_id']:
            pm_workspace.require_member(ops.db, principal, current['workspace_id'], write=True, decide=True)


def _save(ctx, principal, pid, repo, current, wid, *, imported=False):
    enabled = ctx.params.get('enabled', True if imported and current['revision'] == 0 else current.get('enabled', False))
    credential = (ctx.admission_binding or {}).get('credential_id')
    changed_repo = repo != current.get('repository')
    ctx.service.db.execute('''INSERT INTO board_github_sync(project_id,repository,workspace_id,revision,enabled,
        principal,credential_id,status,next_sync,operation_id) VALUES(?,?,?,?,?,?,?,'not_synced',0,?)
        ON CONFLICT(project_id) DO UPDATE SET repository=excluded.repository,workspace_id=excluded.workspace_id,
        revision=excluded.revision,enabled=excluded.enabled,principal=excluded.principal,credential_id=excluded.credential_id,
        status=CASE WHEN ? THEN 'not_synced' ELSE status END,
        last_sync=CASE WHEN ? THEN NULL ELSE last_sync END,
        pages=CASE WHEN ? THEN '{}' ELSE pages END,operation_id=excluded.operation_id''',
        (pid, repo, wid, current['revision'] + 1, int(enabled), json.dumps(pm_workspace.principal_binding(principal)),
        credential, ctx.operation_id, changed_repo, changed_repo, changed_repo))


async def configure(ctx):
    def change(db, now):
        principal = pm_workspace.admitted_principal(ctx)
        pid, repo, current, _ = _check(ctx.service, principal, ctx.op['action'], ctx.target, ctx.params, ctx.preconditions)
        _save(ctx, principal, pid, repo, current, current['workspace_id'])
        work_items._event(ctx, 'project', pid, 'project.github.configured', {})
        return settings(db, pid)
    return work_items._once(ctx, change)


async def _local_action(ctx, principal, suffix, action, target, params, pre=None):
    # This fixed set consists solely of synchronous journal handlers wrapped in
    # async functions. They contain no suspension or provider I/O: the parent
    # transaction cannot be interleaved with another worker. Preserve ordinary
    # admission, controller/lifecycle gates, original keys and real receipts.
    if action not in {'project.create', 'pm.project.bind', 'work_item.create', 'work_item.update', 'pm.work.bind'}:
        raise ValueError('Only registered local board actions may join the transaction')
    op, _ = ctx.service.create(principal, action=action, target=target, params=params,
        preconditions=pre or {},
        idempotency_key=f'board.github.{ctx.operation_id}.{hashlib.sha256(suffix.encode()).hexdigest()}', entry=ctx.op['entry'])
    ctx.service._merge_refs(op['operation_id'], {'board_github_import_operation_id': ctx.operation_id})
    if op['status'] != 'succeeded':
        await ctx.service._execute(op['operation_id'])
    saved = ctx.service.get(op['operation_id'])
    if saved['status'] != 'succeeded':
        raise OperationError(saved['error_code'] or 'BOARD_IMPORT_FAILED', 'A local board action could not be applied', 409)
    return saved['result']


async def _create_project(ctx, principal, workspace, repo):
    name = (workspace['name'][:48] + ' · GitHub ' + ctx.operation_id[-8:])[:80]
    result = await _local_action(ctx, principal, 'project', 'project.create', {}, {'name': name, 'repositories': [repo]})
    pid = result['project_id']
    await _local_action(ctx, principal, 'project.bind', 'pm.project.bind',
        {'workspace_id': workspace['workspace_id'], 'project_id': pid}, {'rationale': 'GitHub board import'},
        {'expected_workspace_version': workspace['version'], 'expected_project_version': result['version']})
    return pid


async def _associate(ctx, principal, wid, iid):
    if not wid or pm_evidence._binding(ctx.service.db, wid, iid):
        return
    item = work_items._get_item(ctx.service.db, iid)
    if item['archived']:
        return
    await _local_action(ctx, principal, f'bind.{iid}', 'pm.work.bind',
        {'workspace_id': wid, 'work_item_id': iid}, {'rationale': 'GitHub board import; no execution or quality acceptance'},
        {'expected_work_version': item['version'], 'expected_binding_id': None})


def _legacy_index(db, pid):
    index = {}
    for row in db.execute('SELECT * FROM work_items WHERE project_id=? AND github_external IS NULL', (pid,)):
        values = [row[k] for k in ('title', 'goal', 'request', 'acceptance')]
        values.extend((link['ref'] or '') + ' ' + (link['note'] or '') for link in db.execute(
            'SELECT ref,note FROM work_item_links WHERE work_item_id=? AND removed_at IS NULL', (row['work_item_id'],)))
        for value in values:
            for match in URL.finditer(value):
                key = (match[1].lower(), int(match[2]))
                index.setdefault(key, set()).add(row['work_item_id'])
        # Existing pull_request links use owner/repo#number, rather than a URL.
        for link in db.execute("SELECT ref FROM work_item_links WHERE work_item_id=? AND kind='pull_request' AND removed_at IS NULL", (row['work_item_id'],)):
            if match := work_items._PULL.fullmatch(link[0]):
                index.setdefault((match[1].lower(), int(match[2])), set()).add(row['work_item_id'])
    return index


async def _apply(ctx, principal, pid, repo, wid, values, now):
    db, counts = ctx.service.db, {'created': 0, 'adopted': 0, 'updated': 0, 'conflicts': 0}
    legacy = _legacy_index(db, pid)
    # New boards should show actionable work before historical Done cards.
    # Existing item/order and association history are never reordered.
    for value in sorted(values, key=lambda entry: entry['state'] != 'open'):
        number = value['number']
        row = db.execute('''SELECT * FROM work_items WHERE project_id=? AND
            json_extract(github_external,'$.repository')=? AND json_extract(github_external,'$.number')=?''', (pid, repo, number)).fetchone()
        old = json.loads(row['github_external']) if row else None
        if old and old['node_id'] != value['node_id']:
            counts['conflicts'] += 1  # identity replacement never silently takes over an existing card
            continue
        adopted = False
        if row is None:
            candidates = legacy.get((repo, number), set())
            if len(candidates) > 1:
                counts['conflicts'] += 1
                continue  # don't guess between duplicate legacy cards
            if candidates:
                row = db.execute('SELECT * FROM work_items WHERE work_item_id=?', (next(iter(candidates)),)).fetchone()
                if row['github_external'] is not None:
                    counts['conflicts'] += 1
                    continue
                adopted = True
                counts['adopted'] += 1
        if row is None:
            created = await _local_action(ctx, principal, f'create.{repo}.{number}', 'work_item.create',
                {'project_id': pid}, {'title': value['title'], 'goal': value['url'],
                    'state': value['lane'] if value['state'] == 'open' else 'todo'})
            row = db.execute('SELECT * FROM work_items WHERE work_item_id=?', (created['work_item_id'],)).fetchone()
            counts['created'] += 1
        iid = row['work_item_id']
        # Source observations never mutate the local work state or its completion
        # claim. Adoption preserves the complete human lane, even for closed history.
        manual = old.get('manual', False) if old else adopted
        if old and row['state'] != old['applied_lane']:
            manual = True
        transition = old is not None and old['state'] != value['state']
        board_lane = value['lane'] if not manual or transition and value['state'] != 'open' else None
        if row['archived_at'] is not None:
            board_lane = None
        external = {'repository': repo, 'number': number, 'node_id': value['node_id'],
            'key': f'{repo}#{number}:{value["node_id"]}', 'url': value['url'], 'kind': value['kind'],
            'source': 'github', 'state': value['state'], 'state_reason': value.get('state_reason'),
            'draft': value.get('draft', False), 'applied_lane': row['state'], 'board_lane': board_lane, 'manual': manual}
        if old != external:
            db.execute('UPDATE work_items SET github_external=?,version=version+1,updated_at=? WHERE work_item_id=?',
                (json.dumps(external), now, iid))
            counts['updated'] += 1
            work_items._event(ctx, 'work_item', iid, 'work_item.github.synced',
                {'project_id': pid, 'source': 'github', 'state': value['state'], 'state_reason': value.get('state_reason')})
        await _associate(ctx, principal, wid, iid)
    return counts


async def sync(ctx):
    ops = ctx.service
    async with ops.context['board_github_lock']:
        saved = ops.db.execute('SELECT result FROM management_applied WHERE operation_id=?', (ctx.operation_id,)).fetchone()
        if saved:
            return json.loads(saved[0])
        ctx.check_cancel()
        principal = pm_workspace.admitted_principal(ctx)
        pid, repo, current, workspace = _check(ops, principal, ctx.op['action'], ctx.target, ctx.params, ctx.preconditions)
        row = ops.db.execute('SELECT * FROM board_github_sync WHERE project_id=?', (pid,)).fetchone() if pid else None
        if row and row['repository'] == repo and row['next_sync'] > time.time() and row['status'] in ('rate_limited', 'unreachable', 'auth_required', 'too_large'):
            return {'project_id': pid, 'status': row['status'], 'next_sync': row['next_sync'], 'deferred': True}
        cached = json.loads(row['pages']) if row and row['repository'] == repo else {}
        collected, failure = None, None
        try:
            collected = await ops.context['board_github_provider'].collect(repo, cached)
        except BoardGitHubError as exc:
            failure = exc
        async def change(db, now):
            nonlocal pid
            # Membership, revision and lifecycle are checked again after the read.
            _, _, fresh, fresh_workspace = _check(ops, principal, ctx.op['action'], ctx.target, ctx.params, ctx.preconditions)
            wid = fresh_workspace['workspace_id'] if fresh_workspace else fresh['workspace_id']
            if pid is None:
                pid = await _create_project(ctx, principal, workspace, repo)
            importing = ctx.op['action'].endswith('import') or not row or row['last_sync'] is None
            _save(ctx, principal, pid, repo, fresh, wid, imported=importing)
            if failure:
                failures = row['failures'] + 1 if row else 1
                delay = max(failure.retry_after, min(3600, INTERVAL * 2 ** min(failures - 1, 4)))
                db.execute('UPDATE board_github_sync SET status=?,failures=?,next_sync=? WHERE project_id=?',
                    (failure.code, failures, now + delay + random.uniform(0, 30), pid))
                result = {'project_id': pid, 'status': failure.code}
            else:
                counts = await _apply(ctx, principal, pid, repo, wid, collected['items'], now)
                status = ('conflicts' if counts['conflicts'] else 'empty' if not collected['items'] else
                    'no_open' if all(v['state'] in ('closed', 'merged') for v in collected['items']) else 'synced')
                db.execute('''UPDATE board_github_sync SET status=?,last_sync=?,next_sync=?,failures=0,
                    pages=?,authenticated=? WHERE project_id=?''',
                    (status, now, now + INTERVAL + random.uniform(0, 30), json.dumps(collected['pages']), int(collected['authenticated']), pid))
                result = {'project_id': pid, 'status': status, **counts}
            work_items._event(ctx, 'project', pid, 'project.github.synced', result)
            if wid:
                work_items._event(ctx, 'pm_evidence', wid, 'pm.github.imported', result)
            return {**result, 'github_sync': settings(db, pid)}
        # The provider traversal has completed before opening this transaction.
        # Only the fixed, non-suspending local action set above runs within it.
        with ops.journal.tx():
            ctx.check_cancel()
            from .pm_lifecycle_gates import require_local
            from .pm_source_lineage import require_new_effect
            require_new_effect(ops, ctx.operation_id)
            require_local(ops.db, ctx.operation_id)
            now = time.time()
            result = await change(ops.db, now)
            ops.db.execute('INSERT INTO management_applied(operation_id,result,applied_at) VALUES(?,?,?)',
                (ctx.operation_id, json.dumps(result), now))
            return result


def scheduled_principal(ops, row):
    db = ops.db
    saved = json.loads(row['principal'])
    if row['credential_id'] and saved['admin']:
        daemon = ops.context.get('daemon')
        if daemon is None or row['credential_id'] != hashlib.sha256(daemon._admin_token.encode()).hexdigest():
            return None
    if row['credential_id'] and not saved['admin']:
        token = db.execute('SELECT * FROM api_principals WHERE token_hash=?', (row['credential_id'],)).fetchone()
        if (not token or token['revoked_at'] is not None or token['expires_at'] is not None and token['expires_at'] <= time.time()
                or token['actor'] != saved['actor'] or json.loads(token['scopes']) != saved['scopes']):
            return None
    return api_auth.Principal(saved['actor'], frozenset(saved['scopes']), admin=saved['admin'], credential_id=row['credential_id'])


async def tick(ops):
    for row in ops.db.execute('''SELECT s.* FROM board_github_sync s JOIN projects p USING(project_id)
            WHERE s.enabled=1 AND s.next_sync<=? AND p.archived_at IS NULL''', (time.time(),)).fetchall():
        target = {'project_id': row['project_id']}
        if ops.db.execute("""SELECT 1 FROM operations WHERE action IN ('project.github.sync','project.github.import','pm.github.import')
                AND status NOT IN ('succeeded','failed','cancelled') AND
                (json_extract(target,'$.project_id')=? OR json_extract(target,'$.workspace_id')=?)""",
                (row['project_id'], row['workspace_id'])).fetchone():
            continue
        principal = scheduled_principal(ops, row)
        try:
            if principal is None:
                raise OperationError('FORBIDDEN', 'Original sync authorization expired', 403)
            ops.create(principal, action='project.github.sync', target=target, params={},
                preconditions={'expected_revision': row['revision']},
                idempotency_key=f'board.github.{row["project_id"]}.{row["revision"]}.{row["next_sync"]}', entry='scheduler')
        except OperationError:
            with ops.journal.tx():
                ops.db.execute("UPDATE board_github_sync SET enabled=0,status='authorization_required' WHERE project_id=?", (row['project_id'],))


async def loop(ops):
    while True:
        if ops.journal.owner_valid():
            await tick(ops)
        await asyncio.sleep(30)
