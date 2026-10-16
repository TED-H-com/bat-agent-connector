// A read that finishes after its view was left must not report into the current event batch.
export const retiredView = error => ['VIEW_CHANGED', 'CONNECTION_CHANGED', 'PM_DISPOSED'].includes(error.code);
