import {actionLabel} from './global-labels.js';

// Operation attempt titles in the central projection are registered action ids.
// Other titles, including multiline requirements and session names, remain source text.
export function scheduleTitle(row) {
  if (row.kind === 'attempt' && row.refs?.operation_id) {
    return actionLabel(row.title === row.refs.operation_id ? '' : row.title);
  }
  return String(row.title || '');
}
export const scheduleFirstLine = row => scheduleTitle(row).split(/\r?\n/)[0];
