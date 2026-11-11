"""Explicit Work-route installation; session creation remains unavailable.

Uses the existing Work handoff and operation contracts. No daemon installs this
module, no endpoint is opened, and no instruction or protocol frame is sent.
"""
from . import pm_work_start as work
from .operations import ActionDef, NeedsAttention

ACTION = 'ahp.work.create'
ROUTE = 'ahp_work_start'


def readiness(ops, document):
    host = document['binding']['host']
    runner = ops.context.get('git_runner')
    binding = runner.execution_binding(host) if runner and hasattr(runner, 'execution_binding') else None
    proven = (binding and binding.get('transport') == 'ssh'
              and binding.get('host_identity', {}).get('name') == host and runner.available(host))
    return {'available': False, 'reason': 'AHP_CREATE_UNAVAILABLE' if proven else 'GIT_RUNNER_UNAVAILABLE'}


def request(parent_id, intent, package_ref):
    pin = intent['profile']
    return {'action': ACTION, 'target': {key: intent[key] for key in ('workspace_id', 'work_item_id')},
        'params': {'parent_id': parent_id, 'profile': {key: pin[key] for key in ('profile_id', 'revision', 'digest')},
                   'package_ref': package_ref}, 'preconditions': {},
        'idempotency_key': f'work-start:{parent_id}:handoff'}


def _admit(ops, principal, target, params, pre):
    parent = work.handoff_parent(ops, target, params, pre)
    if parent is None or work._intent(parent).get('route') != ROUTE or params.get('parent_id') != parent['operation_id']:
        raise work._bad('AHP_HANDOFF_REQUIRED', 'AHP creation requires the original Work handoff slot')
    intent = work._intent(parent)
    return {'controller_authority': intent['controller'], 'pm_lineage': {'workspace_id': intent['workspace_id']},
            'ahp_work_start': {'parent_id': parent['operation_id'], 'profile': intent['profile']}}


def _authorize_existing(ops, principal, operation, verb):
    if verb == 'replay':
        _admit(ops, principal, operation['target'], operation['params'], operation['preconditions'])
    elif work.require_child(ops, operation) is None:
        raise work._bad('AHP_HANDOFF_REQUIRED', 'The original Work handoff relation is required')


async def _run(ctx):
    if work.require_child(ctx.service, ctx.operation_id) is None:
        raise work._bad('AHP_HANDOFF_REQUIRED', 'The original Work handoff relation is required')
    raise NeedsAttention('AHP_CREATE_UNAVAILABLE', 'Owned AHP session creation is not installed')


def install(ops):
    definition = ActionDef(ACTION, 'start', 'Create the original Work AHP session', _run, _admit,
                           ('workspace_id', 'work_item_id'), authorize_existing=_authorize_existing)
    if ACTION in ops.actions and ops.actions[ACTION] != definition:
        raise work._bad('PM_WORK_ROUTE_CONFLICT', 'The AHP handoff action has a different definition')
    work.register_handoff_route(ops, ROUTE, request, readiness)
    if ACTION not in ops.actions:
        ops.register(definition)
