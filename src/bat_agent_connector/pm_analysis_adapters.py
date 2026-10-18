"""Explicit backend selection for registered structured PM sinks.

This module owns no executor, process or queue. Legacy requests remain native;
only trusted domain callbacks choose the fixed context and import destination.
"""
from __future__ import annotations

from . import pm_bat_operations, pm_bat_runtime, pm_native_journal, pm_native_operations, pm_runs, pm_runtime
from .operations import OperationError

DESTINATIONS = {
    'pm.conversation.propose': ('conversation_proposal', 'conversation_proposal'),
    'pm.follow_up.analyze': ('follow_up_assessment', 'follow_up_analysis'),
    'pm.continuation.prepare': ('continuation_brief', 'continuation_brief'),
}
ADAPTERS = (pm_native_journal.ADAPTER, pm_bat_runtime.ADAPTER)


def selection(params):
    value = params.get('adapter', pm_native_journal.ADAPTER)
    if not isinstance(value, str) or value not in ADAPTERS:
        raise OperationError('INVALID_PARAMS', 'Select an advertised structured PM analysis adapter', 422)
    return value


def ordinary_params(params):
    selection(params)
    return {key: value for key, value in params.items() if key not in {'adapter', 'intake_id', 'previous_brief_ref'}}


def admission(ops, principal, target, params, pre):
    adapter = selection(params)
    admit = pm_bat_operations._admit if adapter == pm_bat_runtime.ADAPTER else pm_native_operations._admit
    return {**admit(ops, principal, target, ordinary_params(params), {'expected_version': pre['expected_version']}),
        'analysis_adapter': adapter, 'pm_lineage': {'workspace_id': target['workspace_id']}}


def fixed_context(operation, adapter, context):
    """Original action, sink, backend and server-frozen context must agree."""
    spec = DESTINATIONS.get(operation['action'])
    binding = operation['external_refs'].get('admission_binding', {})
    if not spec or adapter != selection(operation['params']) or binding.get('analysis_adapter', ADAPTERS[0]) != adapter:
        return False
    original = binding.get(spec[1], {})
    if (context.get('output_destination') != spec[0] or original.get('context') != context
            or original.get('context_digest') != pm_runs._digest(context)):
        return False
    return adapter != pm_bat_runtime.ADAPTER or (
        isinstance(binding.get('pm_bat_binding'), dict)
        and pm_runs._digest(binding['pm_bat_binding']) == binding.get('pm_bat_binding_digest'))


def prepare(ctx, run, prompt):
    if run['adapter'] == pm_bat_runtime.ADAPTER:
        pm_runs.receipt(ctx.service.journal, run['run_id'], run['generation'], 'prepare', 'prepared',
            {'prompt': prompt, 'prompt_digest': pm_runtime.prompt_digest(prompt)})
    else:
        launch = {'kind': 'launch_intent', 'run_id': run['run_id'], 'context_digest': run['context_digest'],
            'prompt': prompt, 'prompt_digest': pm_runtime.prompt_digest(prompt),
            'model': ctx.params.get('model'), 'timeout_s': ctx.params.get('timeout_s', 300)}
        pm_native_journal.record(ctx.service.journal, run['run_id'], run['generation'], launch)
        pm_runs.receipt(ctx.service.journal, run['run_id'], run['generation'], 'start', 'prepared',
            {'adapter': run['adapter'], 'prompt_digest': launch['prompt_digest']})
    return {'run_id': run['run_id'], 'generation': run['generation']}


async def run_analysis(ctx, *, prepare, import_reply):
    adapter = selection(ctx.params)
    if ctx.admission_binding.get('analysis_adapter', ADAPTERS[0]) != adapter:
        raise OperationError('PM_CONTEXT_REFUSED', 'The original analysis backend changed', 409)
    execute = pm_bat_operations.run_analysis if adapter == pm_bat_runtime.ADAPTER else pm_native_operations.run_analysis
    return await execute(ctx, prepare=prepare, import_reply=import_reply)


def verified_reply(journal, run_id, generation):
    run = pm_runs.get(journal.db, run_id)
    if run['adapter'] == pm_bat_runtime.ADAPTER:
        return pm_bat_runtime.verified_reply(journal, run_id, generation)
    return pm_native_journal.verified_reply(journal, run_id, generation)


def capabilities(ops, principal, workspace, *, native=None, bat=None):
    from . import pm_native_capabilities
    native = native if native is not None else pm_native_capabilities.read(ops, principal, workspace)
    bat = bat if bat is not None else pm_bat_operations.capabilities(ops, principal, workspace)
    actions = {}
    for action in DESTINATIONS:
        registered = action in ops.actions
        actions[action] = {'adapters': [{**value, 'action_registered': registered and value['action_registered'],
            'authorized': registered and value['authorized']} for value in (native, bat)]}
    return {'version': 1, 'default_adapter': ADAPTERS[0], 'actions': actions}
