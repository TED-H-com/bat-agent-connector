// A read that finishes after its view was left must not report into the current event batch.
export const retiredView = error => ['VIEW_CHANGED', 'CONNECTION_CHANGED', 'PM_DISPOSED'].includes(error.code);

// HTTP and network failures are thrown before the caller reaches its guard. A
// same-connection route change moves the view generation, not the connection
// epoch, so those errors do not carry a retired-view code. Ask the owning guard
// when the catch runs. Any other guard failure stays with the original error.
export const viewLeft = alive => {
  try { alive(); return false; }
  catch (error) { return retiredView(error); }
};
