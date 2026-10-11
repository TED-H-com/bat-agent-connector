"""Original execution verifier reads shared by HTTP, RPC and MCP."""
from __future__ import annotations

import re

from . import pm_execution_verification as verification
from .operations import OperationError

FIELDS = {
    'pm_execution_verification_configuration': ('workspace_id', 'execution_operation_id'),
    'pm_execution_verification_get': ('workspace_id', 'verification_operation_id'),
}


async def read(ops, principal, method, params):
    if not principal.allows('observe'):
        raise OperationError('FORBIDDEN', 'Execution verification reads need observe', 403)
    if method not in FIELDS or set(params) != set(FIELDS[method]):
        raise OperationError('INVALID_PARAMS', 'Select the exact original workspace and operation', 422)
    for field in FIELDS[method]:
        prefix = 'ws' if field == 'workspace_id' else 'op'
        if not isinstance(params[field], str) or not re.fullmatch(prefix + '_[0-9a-f]{32}', params[field]):
            raise OperationError('INVALID_PARAMS', 'Select the exact original workspace and operation', 422)
    if method == 'pm_execution_verification_configuration':
        return await verification.configuration(ops, principal, **params)
    return verification.get_verification(ops, principal, **params)
