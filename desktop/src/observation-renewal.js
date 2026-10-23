// Shared by Work and Settings. Spend a producer deadline before awaiting its
// read, so failed, unchanged and discarded responses cannot start retry loops.
export function claimObservationRenewal(renewed, key, deadline, now = Date.now()) {
  if (!Number.isFinite(deadline) || now < deadline || renewed.has(key)) return null;
  const attempt = {error: {code: 'OBSERVATION_NOT_RENEWED', unchangedDeadline: true}};
  renewed.set(key, attempt);
  return attempt;
}
