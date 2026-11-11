const operationId = value => typeof value === 'string' && /^op_[0-9a-f]{32}$/.test(value);

// The cursor belongs to the scanned central page, which may contain no visible
// rows. Do not derive it from the last visible operation or discard its ID.
export function operationPage(result, previous = null) {
  if (!Array.isArray(result?.operations) || result.operations.some(op => !operationId(op?.operation_id))) return null;
  const time = result.next_before ?? null, id = result.next_before_id ?? null;
  if (time === null) return id === null ? {operations: result.operations, next: null} : null;
  const paired = Object.hasOwn(result, 'next_before_id');
  if (typeof time !== 'number' || !Number.isFinite(time) || time <= 0 || paired && !operationId(id)) return null;
  if (previous && !(time < previous.time || time === previous.time &&
      previous.id !== null && id !== null && id < previous.id)) return null;
  return {operations: result.operations, next: {time, id}};
}
