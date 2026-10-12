"""Bounded, journal-only workload observation. No adapter commands or provider IO.

Absence is meaningful only inside the policy's explicitly observed scope. In
particular, linked GitHub receipts cannot prove a repository-wide enumeration.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math

from . import pm_evidence, work_items

MAX_ROWS = 512
MAX_REFS = 4096


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def body(value):
    return json.loads(value) if isinstance(value, str) else value or {}


def epoch(value):
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            value = parsed.timestamp() if parsed.tzinfo else None
        except ValueError:
            return None
    return value if type(value) in (int, float) and math.isfinite(value) else None


def has_table(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def visible(ops, principal, wid, ref):
    kind, ident = ref['kind'], ref['id']
    if kind == 'github_observation':
        from . import pm_github_observation
        try:
            row = pm_github_observation._record(ops.db, wid, ident)
            pm_github_observation._access(ops, principal, wid, row)
            return True
        except pm_github_observation.OperationError:
            return False
    if kind == 'repair_runtime_completion':
        from . import pm_maintenance_repair_runtime as runtime
        try:
            op = runtime.repairs._operation(ops, ident)
            parent = runtime._original(ops, op)
            value = runtime.proof(ops, principal, parent)
            return bool(parent['target']['workspace_id'] == wid and value
                and value['observation_operation_id'] == ident and value['document_digest'] == ref.get('document_digest'))
        except (runtime.OperationError, KeyError, ValueError, TypeError):
            return False
    if kind == 'operation':
        return pm_evidence._read_operation(ops, principal, wid, ident) is not None
    if kind == 'task':
        return pm_evidence._task_visible(ops, principal, wid, ident)
    if kind == 'command':
        return pm_evidence._command_visible(ops, principal, wid, ident)
    if kind == 'session':
        return pm_evidence._session_visible(ops, principal, wid, ident)
    if kind == 'work':
        row = ops.db.execute('SELECT project_id FROM work_items WHERE work_item_id=?', (ident,)).fetchone()
        return bool(row and row[0] == ref['project_id'])
    if kind == 'sprint':
        # Only identity/status is projected, not Goal, How or a quoted plan.
        return bool(ops.db.execute('SELECT 1 FROM pm_sprints WHERE workspace_id=? AND sprint_id=?', (wid, ident)).fetchone())
    return False


class Collector:
    def __init__(self, ops, principal, wid, policy, now):
        self.ops, self.db, self.principal, self.wid = ops, ops.db, principal, wid
        self.policy, self.scope, self.now = policy, policy['scope'], now
        self.max_age = policy['observation']['max_age_seconds']
        self.coverage, self.reasons, self.exclusions, self.refs, self.tokens = [], [], [], {}, []
        self.expiries = [now + self.max_age]
        self.verified_sessions = set()
        self.repair_sessions, self.repair_executions, self.repair_work = {}, {}, {}
        self.projects = self.scope['project_binding']['project_id']

    def reason(self, code, severity='unknown', source=None):
        value = {'code': code, 'severity': severity}
        if source:
            value['source'] = source
        if value not in self.reasons:
            self.reasons.append(value)

    def rows(self, sql, params, label):
        rows = self.db.execute(sql + ' LIMIT ?', (*params, MAX_ROWS + 1)).fetchall()
        if len(rows) > MAX_ROWS:
            self.reason('source_row_limit', source={'kind': label})
        return rows[:MAX_ROWS]

    def record(self, kind, ident, value):
        self.tokens.append([kind, ident, digest(value)])

    def ref(self, kind, ident, **extra):
        ref = {'kind': kind, 'id': ident, **extra}
        if len(self.refs) >= MAX_REFS:
            self.reason('source_reference_limit')
            return False
        if not visible(self.ops, self.principal, self.wid, ref):
            # Neither source identity nor the hidden blocker is projected.
            self.reason('source_not_visible')
            return False
        self.refs[(kind, ident)] = ref
        return True

    def fresh(self, at, label):
        at = epoch(at)
        if at is None or at > self.now:
            self.reason('source_time_unproven', source=label)
            return False
        self.expiries.append(at + self.max_age)
        if self.now >= at + self.max_age:
            self.reason('source_expired', source=label)
            return False
        return True

    def deferred_task(self, tid, links):
        """A terminal failed attempt may be explicitly returned to backlog.

        Match real management receipts/events, not merely a pre-existing todo
        field. A later task event or unknown command still prevents exclusion.
        """
        work_ids = {link['work_item_id'] for link in links if link['kind'] == 'task' and link['ref'] == tid}
        if not work_ids:
            return False
        last_task = self.db.execute("SELECT MAX(seq) FROM api_events WHERE resource_type='task' AND resource_id=?", (tid,)).fetchone()[0] or 0
        for work_id in sorted(work_ids):
            item = self.db.execute('SELECT state FROM work_items WHERE work_item_id=?', (work_id,)).fetchone()
            event = self.db.execute('''SELECT * FROM api_events WHERE resource_type='work_item'
                AND resource_id=? AND kind='work_item.state' ORDER BY seq DESC LIMIT 1''', (work_id,)).fetchone()
            if not item or item[0] != 'todo' or not event or event['seq'] <= last_task:
                return False
            state = body(event['body'])
            oid = state.get('operation_id')
            operation = self.db.execute('''SELECT o.*,m.result AS applied_result FROM operations o
                JOIN management_applied m USING(operation_id) WHERE o.operation_id=?''', (oid,)).fetchone()
            if (state.get('to') != 'todo' or state.get('from') == 'todo' or not operation
                    or operation['action'] != 'work_item.update' or body(operation['params']).get('state') != 'todo'
                    or body(operation['target']).get('work_item_id') != work_id
                    or 'state' not in body(operation['applied_result']).get('fields', [])
                    or not self.ref('operation', oid)):
                return False
            self.record('defer', oid, dict(operation))
        return True

    def tasks(self):
        project = work_items._get_project(self.db, self.projects)
        self.record('project', self.projects, project)
        pairs = self.scope['bat_workspaces']
        ids = set()
        links = self.rows('''SELECT l.* FROM work_item_links l JOIN work_items w USING(work_item_id)
            WHERE w.project_id=? AND l.removed_at IS NULL ORDER BY l.link_id''', (self.projects,), 'work_links')
        self.links = links
        for link in links:
            if link['kind'] == 'session':
                host, _, sid = link['ref'].partition('/')
                row = self.db.execute('SELECT body FROM sessions_observed WHERE host=? AND session_id=?', (host, sid)).fetchone()
                pair = (host, body(row[0]).get('workspace_id')) if row else None
                if not row or pair not in {(p['host'], p['workspace_id']) for p in pairs}:
                    self.reason('linked_session_not_covered')
            elif link['kind'] == 'checkpoint':
                # A saved Git checkpoint says nothing about an ongoing attempt.
                self.exclusions.append({'kind': 'work', 'id': link['work_item_id'], 'reason': 'checkpoint_is_not_activity'})
        for link in links:
            if link['kind'] == 'task':
                ids.add(link['ref'])
        # A project-to-Task-Service association is explicit; a matching name is not.
        if project.get('task_project'):
            for pair in pairs:
                rows = self.rows('SELECT task_id FROM tasks WHERE project=? AND host=? AND workspace=? ORDER BY task_id',
                    (project['task_project'], pair['host'], pair['workspace_id']), 'tasks')
                ids.update(row[0] for row in rows)
        for tid in sorted(ids)[:MAX_ROWS]:
            row = self.db.execute('SELECT * FROM tasks WHERE task_id=?', (tid,)).fetchone()
            if row is None or not self.ref('task', tid):
                self.reason('linked_task_unavailable')
                continue
            self.record('task', tid, dict(row))
            verification = self.db.execute('SELECT * FROM observed_verifications WHERE task_id=? ORDER BY verification_id DESC LIMIT 1', (tid,)).fetchone()
            commands = self.rows('SELECT * FROM commands WHERE task_id=? ORDER BY created_at,command_id', (tid,), 'commands')
            checked = (row['state'] == 'done' and row['review_passed'] == 1
                and isinstance(row['verification_commit'], str) and len(row['verification_commit']) == 40
                and row['review_commit'] == row['verification_commit']
                and row['verification_tree'] and row['review_tree'] == row['verification_tree']
                and verification and verification['exit_code'] == 0
                and verification['candidate_commit'] == row['verification_commit']
                and verification['tree_hash'] == row['verification_tree']
                and not any(c['status'] in {'intent', 'needs_review', 'uncertain'} for c in commands))
            if verification:
                self.record('verification', str(verification['verification_id']), dict(verification))
            if checked:
                for sid in (row['session_id'], row['reviewer_session_id']):
                    if sid:
                        self.verified_sessions.add((row['host'], sid))
            elif (row['state'] == 'failed' and not any(c['status'] in {'intent', 'needs_review', 'uncertain', 'accepted', 'running'}
                    for c in commands) and self.deferred_task(tid, links)):
                self.exclusions.append({'kind': 'task', 'id': tid, 'reason': 'failed_attempt_explicitly_deferred'})
            else:
                self.reason('task_failed_unresolved' if row['state'] == 'failed' else 'task_not_verified' if row['state'] == 'done'
                    else 'task_waiting' if row['paused'] or row['state'] in {'blocked', 'waiting', 'needs_ted', 'review'}
                    else 'task_committed', 'development', {'kind': 'task', 'id': tid})
            for command in commands:
                if not self.ref('command', command['command_id']):
                    continue
                self.record('command', command['command_id'], dict(command))
                if (command['status'] in {'intent', 'uncertain', 'needs_review'}
                        or command['status'] in {'accepted', 'running'} and not checked):
                    self.reason('command_waiting_or_unknown', 'development', {'kind': 'command', 'id': command['command_id']})
        if len(ids) > MAX_ROWS:
            self.reason('source_row_limit', source={'kind': 'tasks'})

    def completed_repairs(self):
        # Every exclusion keeps the full immutable source chain in the snapshot.
        # No Task identity or work-item approval is synthesized from completion.
        if not has_table(self.db, 'pm_repair_runtime_proofs'):
            return
        from . import pm_maintenance_repair_runtime as runtime
        from . import pm_package_handoff
        rows = self.rows('''SELECT p.* FROM pm_repair_runtime_proofs p
            JOIN pm_maintenance_repairs r ON r.operation_id=p.parent_id
            WHERE r.workspace_id=? ORDER BY p.parent_id''', (self.wid,), 'repair_completions')
        for row in rows:
            if not self.ref('repair_runtime_completion', row['operation_id'], document_digest=row['document_digest']):
                continue
            parent = runtime.repairs._operation(self.ops, row['parent_id'])
            proof = runtime.proof(self.ops, self.principal, parent)
            origin = runtime.origin(self.ops, parent)
            self.record('repair_runtime_completion', row['operation_id'], proof)
            session = self.db.execute('SELECT * FROM sessions_observed WHERE host=? AND session_id=?',
                (proof['host'], proof['session_id'])).fetchone()
            if not self.current_repair_session(session, proof, origin):
                continue
            child = runtime.repairs._child(self.ops, parent)
            if child['operation_id'] != proof['execution_operation_id']:
                continue
            self.repair_sessions[(proof['host'], proof['session_id'])] = proof
            self.repair_executions[child['operation_id']] = proof
            binding = pm_package_handoff.binding(child)
            if binding:
                work = binding['contract']['package']['context']['work_ref']
                self.repair_work[work['work_item_id']] = (work, proof)

    def current_repair_session(self, row, proof, origin):
        from . import pm_maintenance_repair_runtime as runtime
        if row is None or row['missing_count'] or row['gone_at']:
            return False
        data = body(row['body'])
        pair = next((p for p in self.scope['bat_workspaces'] if p['host'] == row['host']
            and p['workspace_id'] == data.get('workspace_id') and p['profile_id'] == data.get('profile_id')), None)
        at = epoch(row['last_seen_at'])
        fleet = self.ops.context.get('fleet')
        hc = fleet.config.hosts.get(row['host']) if fleet else None
        if not pair or hc is None or digest([hc.url, hc.fingerprint, hc.profile_id]) != pair['binding_digest']:
            return False
        # Native counters are provided only by current authenticated metadata;
        # the inventory must not substitute a tab SDK or a retained stale field.
        if (pair is None or at is None or not proof['recorded_at'] <= at <= self.now < at + self.max_age
                or data.get('fields_stale') or data.get('specific_stale_reason')
                or data.get('loaded') is not True or data.get('streaming') is not False or data.get('pending')
                or data.get('runtime_status') not in {None, 'idle', 'completed', 'stopped', 'exited'}
                or data.get('provenance') != 'connector_managed' or data.get('api_access') != 'managed'
                or body(data.get('field_evidence')).get('native_input') != 'session_meta'
                or data.get('sdk_session_id') != proof['sdk_session_id']
                or data.get('native_input_state') != proof.get('input_state')
                or not runtime.same_original_input(origin, runtime.input_state({'inputState': data.get('native_input_state')}))
                or data.get('registry_origin') != {'controller_incarnation': proof['registry_incarnation'],
                    'created_at': origin['registry_created_at'], 'start_operation_id': proof['execution_operation_id']}):
            return False
        host = self.db.execute('SELECT * FROM hosts_observed WHERE host=?', (row['host'],)).fetchone()
        discovery = self.db.execute('SELECT * FROM discovery_latest WHERE host=? AND profile_id=?',
            (row['host'], pair['profile_id'])).fetchone()
        if not host or not host['reachable'] or not discovery:
            return False
        observed, scan = epoch(host['last_success_at']), body(discovery['body'])
        finished = epoch(scan.get('finished_at'))
        return (observed is not None and finished is not None and proof['recorded_at'] <= observed <= self.now < observed + self.max_age
            and proof['recorded_at'] <= finished <= self.now < finished + self.max_age
            and discovery['binding'] == scan.get('binding_version') == pair['binding_digest']
            and scan.get('status') == 'succeeded' and scan.get('complete_enumeration') is True
            and body(scan.get('authority')).get('verified') is True
            and data['workspace_id'] in body(scan.get('coverage')).get('workspace_ids', []))

    def work(self):
        rows = self.rows('SELECT * FROM work_items WHERE project_id=? ORDER BY work_item_id', (self.projects,), 'work')
        for row in rows:
            if not self.ref('work', row['work_item_id'], project_id=self.projects):
                continue
            self.record('work', row['work_item_id'], dict(row))
            completed = self.repair_work.get(row['work_item_id'])
            item = work_items._item(row)
            if (completed and item['version'] == completed[0]['work_version']
                    and work_items.fingerprint(item) == completed[0]['work_fingerprint']):
                self.exclusions.append({'kind': 'work', 'id': row['work_item_id'], 'reason': 'original_maintenance_attempt_completed',
                    'runtime_observation_id': completed[1]['observation_operation_id']})
            elif row['state'] == 'todo':
                self.exclusions.append({'kind': 'work', 'id': row['work_item_id'], 'reason': 'unselected_backlog'})
            else:
                # Approving a work-item content fingerprint is not a DoD assessment.
                self.reason('work_waiting' if row['state'] == 'waiting' else 'work_done_claim_unverified'
                    if row['state'] == 'done' else 'work_committed', 'development',
                    {'kind': 'work', 'id': row['work_item_id']})
        for link in self.links:
            self.record('work_link', str(link['link_id']), dict(link))

    def sprints(self):
        if not has_table(self.db, 'pm_sprint_state'):
            self.reason('sprint_source_unavailable')
            return
        rows = self.rows('SELECT * FROM pm_sprint_state WHERE workspace_id=? ORDER BY sprint_id', (self.wid,), 'sprints')
        for row in rows:
            if row['status'] not in {'planned', 'active'}:
                continue
            if not self.ref('sprint', row['sprint_id']):
                continue
            self.record('sprint', row['sprint_id'], dict(row))
            selected = self.db.execute('SELECT items FROM pm_sprint_selections WHERE revision_id=?', (row['selection_revision_id'],)).fetchone()
            if selected:
                self.record('sprint_selection', row['selection_revision_id'], selected[0])
            if row['status'] == 'active' or selected and body(selected[0]):
                self.reason('sprint_commitment', 'development', {'kind': 'sprint', 'id': row['sprint_id']})

    def operations(self):
        repo = self.scope['repository']
        rows = self.rows('''SELECT * FROM operations WHERE action NOT LIKE 'pm.%' AND
            (lower(json_extract(target,'$.repository'))=? OR json_extract(params,'$.project_id')=?)
            ORDER BY operation_id''', (repo.lower(), self.projects), 'operations')
        linked = {link['ref'] for link in self.links if link['kind'] == 'operation'}
        rows = {row['operation_id']: row for row in rows}
        # Deployment targets name a configured recipe, not necessarily a repo.
        # The established environment row supplies its original repository.
        deployments = self.rows('''SELECT d.*, e.repository FROM deployments d
            JOIN deployment_environments e USING(environment_key) WHERE lower(e.repository)=?
            ORDER BY d.deployment_id''', (repo.lower(),), 'deployments')
        linked.update(row['operation_id'] for row in deployments)
        for pair in self.scope['bat_workspaces']:
            candidates = self.rows('''SELECT o.* FROM operations o WHERE o.action NOT LIKE 'pm.%'
                AND json_extract(o.target,'$.host')=? AND (json_extract(o.target,'$.workspace_id')=?
                OR EXISTS(SELECT 1 FROM sessions_observed s WHERE s.host=json_extract(o.target,'$.host')
                    AND s.session_id=json_extract(o.target,'$.session_id') AND json_extract(s.body,'$.workspace_id')=?))
                ORDER BY o.operation_id''', (pair['host'], pair['workspace_id'], pair['workspace_id']), 'operations')
            rows.update((r['operation_id'], r) for r in candidates)
        for oid in sorted(linked)[:MAX_ROWS]:
            row = self.db.execute('SELECT * FROM operations WHERE operation_id=?', (oid,)).fetchone()
            if row and not row['action'].startswith('pm.'):
                rows[oid] = row
            elif row is None:
                self.reason('linked_operation_unavailable')
        if len(rows) > MAX_ROWS or len(linked) > MAX_ROWS:
            self.reason('source_row_limit', source={'kind': 'operations'})
        for oid, row in sorted(rows.items())[:MAX_ROWS]:
            if not self.ref('operation', oid):
                continue
            self.record('operation', oid, dict(row))
            steps = self.rows('SELECT * FROM operation_steps WHERE operation_id=? ORDER BY seq', (oid,), 'steps')
            for step in steps:
                self.record('step', f'{oid}/{step["seq"]}', dict(step))
            unknown = any(step['status'] in {'started', 'uncertain'} for step in steps)
            if unknown or row['status'] not in {'succeeded', 'failed', 'cancelled'}:
                self.reason('operation_unknown' if unknown or row['status'] == 'uncertain' else 'operation_committed',
                    'development', {'kind': 'operation', 'id': oid})
            elif row['action'] in {'repository.continue', 'session.start', 'checkpoint.continue'} and row['status'] == 'succeeded':
                result, target = body(row['result']), body(row['target'])
                host, sid = result.get('host') or target.get('host'), result.get('session_id')
                completed = self.repair_executions.get(oid)
                if (completed and row['action'] == 'repository.continue'
                        and (host, sid) == (completed['host'], completed['session_id'])):
                    self.exclusions.append({'kind': 'operation', 'id': oid, 'reason': 'original_maintenance_attempt_completed',
                        'runtime_observation_id': completed['observation_operation_id']})
                elif not host or not sid or (host, sid) not in self.verified_sessions:
                    self.reason('started_attempt_result_unverified', 'development', {'kind': 'operation', 'id': oid})
        for deployment in deployments:
            oid = deployment['operation_id']
            if ('operation', oid) not in self.refs:
                continue
            self.record('deployment', deployment['deployment_id'], dict(deployment))
            if not deployment['provider_terminal']:
                self.reason('deployment_waiting_or_unknown', 'development', {'kind': 'operation', 'id': oid})

    def bat(self):
        pairs = self.scope['bat_workspaces']
        if not pairs:
            self.reason('bat_scope_unconfigured')
        for pair in pairs:
            host, workspace, profile = pair['host'], pair['workspace_id'], pair['profile_id']
            label = {'kind': 'bat_workspace', 'host': host, 'workspace_id': workspace, 'profile_id': profile}
            start = len(self.reasons)
            fleet = self.ops.context.get('fleet')
            hc = fleet.config.hosts.get(host) if fleet else None
            if hc is None or hc.profile_id != profile or digest([hc.url, hc.fingerprint, hc.profile_id]) != pair['binding_digest']:
                self.reason('adapter_scope_changed', source=label)
            observed = self.db.execute('SELECT * FROM hosts_observed WHERE host=?', (host,)).fetchone()
            discovery = self.db.execute('SELECT * FROM discovery_latest WHERE host=? AND profile_id=?', (host, profile)).fetchone()
            if observed:
                self.record('host', host, dict(observed))
                self.fresh(observed['last_success_at'], label)
            if not observed or not observed['reachable']:
                self.reason('adapter_disconnected', source=label)
            if not discovery:
                self.reason('adapter_not_observed', source=label)
                data = {}
            else:
                self.record('discovery', host + '/' + profile, dict(discovery))
                data = body(discovery['body'])
                self.fresh(data.get('finished_at'), label)
                if (discovery['binding'] != pair['binding_digest'] or data.get('binding_version') != pair['binding_digest']
                        or data.get('status') != 'succeeded' or data.get('complete_enumeration') is not True
                        or body(data.get('authority')).get('verified') is not True
                        or workspace not in body(data.get('coverage')).get('workspace_ids', [])):
                    self.reason('adapter_coverage_incomplete', source=label)
            sessions = self.rows('''SELECT * FROM sessions_observed WHERE host=?
                AND json_extract(body,'$.workspace_id')=? ORDER BY session_id''', (host, workspace), 'sessions')
            unmapped = self.rows('''SELECT session_id,digest FROM sessions_observed WHERE host=?
                AND (json_extract(body,'$.workspace_id') IS NULL OR json_extract(body,'$.workspace_id')='')
                ORDER BY session_id''', (host,), 'unmapped_sessions')
            if unmapped:
                self.reason('session_workspace_unproven', source=label)
                self.record('unmapped_sessions', host, [dict(row) for row in unmapped])
            for session in sessions:
                self.session(session, label)
            self.coverage.append({**label, 'status': 'incomplete' if any(r['severity'] == 'unknown'
                for r in self.reasons[start:]) else 'complete',
                'observed_at': epoch(data.get('finished_at')), 'basis': 'saved_authenticated_inventory',
                'outside_scope': ['manual_sessions_without_tabs_or_facts', 'other_adapters', 'background_git_state']})

    def session(self, row, label):
        host, sid = row['host'], row['session_id']
        ref = {'kind': 'session', 'id': host + '/' + sid}
        if not self.ref('session', ref['id']):
            return
        data = body(row['body'])
        self.record('session', ref['id'], dict(row))
        if data.get('profile_id') != label['profile_id']:
            self.reason('session_profile_unproven', source=ref)
            return
        # Only the server-owned PM run binding establishes this exclusion.
        pm = self.db.execute('''SELECT r.workspace_id FROM pm_runs r JOIN pm_run_state s USING(run_id)
            WHERE json_extract(s.session_binding,'$.host')=? AND json_extract(s.session_binding,'$.session_id')=?''',
            (host, sid)).fetchone() if has_table(self.db, 'pm_runs') else None
        if pm and pm[0] == self.wid:
            self.exclusions.append({**ref, 'reason': 'pm_owned_session'})
            return
        verified = (host, sid) in self.verified_sessions
        # Tab absence alone never means completion. A verified task outcome is
        # also necessary, and current complete enumeration must cover this host.
        if verified and row['gone_at'] and row['missing_count'] >= 2:
            self.exclusions.append({**ref, 'reason': 'verified_ended_session_retained'})
            return
        fresh = self.fresh(row['last_seen_at'], label)
        if not fresh or data.get('fields_stale') or row['missing_count']:
            self.reason('session_state_unknown', source=ref)
        elif data.get('streaming') is True or data.get('runtime_status') in {'running', 'streaming', 'starting', 'active'}:
            self.reason('coding_active', 'development', ref)
        elif data.get('pending'):
            self.reason('session_waiting', 'development', ref)
        elif (host, sid) in self.repair_sessions:
            self.exclusions.append({**ref, 'reason': 'original_maintenance_attempt_completed',
                'runtime_observation_id': self.repair_sessions[(host, sid)]['observation_operation_id']})
        elif verified and data.get('loaded') is False and data.get('streaming') is False:
            self.exclusions.append({**ref, 'reason': 'verified_ended_session_retained'})
        else:
            self.reason('session_end_unproven', source=ref)

    def github(self):
        # No full repository PR/CI enumeration exists in the current observer.
        # W47 may add a fixed, authenticated, fully paginated observation source;
        # do not let a caller-supplied coverage flag substitute for that receipt.
        kind = self.scope.get('github_scope')
        from . import pm_github_observation
        if kind == pm_github_observation.provider.PROFILE:
            pm_github_observation.workload_evidence(self)
            return
        self.coverage.append({'kind': 'github', 'repository': self.scope['repository'],
            'requested_scope': kind, 'basis': 'linked_journal_receipts', 'status': 'incomplete',
            'network_checked': False, 'outside_scope': ['unlinked_remote_work', 'current_remote_pr_ci_state']})
        self.reason('github_repository_coverage_unavailable')
        for link in self.links:
            if link['kind'] == 'pull_request':
                self.reason('linked_pr_current_state_unproven', source={'kind': 'work', 'id': link['work_item_id']})

    def collect(self):
        self.tasks()
        self.completed_repairs()
        self.work()
        self.sprints()
        self.operations()
        self.bat()
        self.github()
        self.coverage.insert(0, {'kind': 'central_project', 'project_id': self.projects,
            'status': 'complete' if not any(r['code'] in {'source_row_limit', 'source_not_visible', 'source_reference_limit'}
                for r in self.reasons) else 'incomplete', 'observed_at': self.now, 'basis': 'same_journal_read_point'})
        severity = {reason['severity'] for reason in self.reasons}
        classification = 'development' if 'development' in severity else 'unknown' if severity else 'idle_candidate'
        return {'source_coverage': self.coverage, 'blocking_reasons': self.reasons, 'exclusions': self.exclusions,
            'classification': classification, 'expires_at': min(self.expiries),
            'source_digest': digest(sorted(self.tokens)), 'source_refs': list(self.refs.values()),
            'activity_digest': digest({'classification': classification, 'reasons': self.reasons,
                'exclusions': self.exclusions, 'scope': self.scope})}
