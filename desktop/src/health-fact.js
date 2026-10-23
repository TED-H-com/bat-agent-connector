import {relativeAge} from './relative-age.js';
export {relativeAge as healthAge} from './relative-age.js';
const time = value => typeof value === 'number' && Number.isFinite(value) ? value
  : typeof value === 'string' && value ? Date.parse(value) : NaN;

// A validated read can confirm a newer failed host attempt without advancing
// its success-based deadline. Actual read failures always retain their code.
export function healthReadError(reason, error) {
  return reason === 'host_unreachable' && error?.unchangedDeadline === true ? null : error;
}

// Display evidence only. This clock never participates in an action gate.
export function displayFact(fact = {}, {now = Date.now(), bootstrap, version} = {}) {
  const observed = time(fact.observed_at), deadline = time(fact.valid_until);
  const ageSeconds = Number.isFinite(observed) ? Math.max(0, Math.floor((now - observed) / 1000)) : null;
  const invalidated = fact.static && (fact.bootstrap !== bootstrap || fact.version !== version);
  const freshness = invalidated ? 'invalidated' : fact.static ? 'current'
    : fact.stale ? 'stale' : !Number.isFinite(observed) ? 'unknown'
    : Number.isFinite(deadline) ? now >= deadline ? 'stale' : 'current'
    : now - observed >= 60_000 ? 'stale' : 'current';
  return {...fact, freshness, ageSeconds};
}

export function healthWaiting(fact) {
  return fact && ['stale', 'unknown', 'invalidated'].includes(fact.freshness)
    && fact.key !== 'ui_connecting' && !fact.error && fact.stale_reason !== 'host_unreachable';
}
export function healthLabel(fact, t, {age = true, checking = true} = {}) {
  const label = t(fact.key === 'health_unknown' ? 'obs_unknown' : fact.key || fact.value);
  if (fact.error) return t('health_read_failed', {value: label, age: relativeAge(fact.ageSeconds, t), reason: fact.error.code || fact.error.status});
  if (fact.stale_reason === 'host_unreachable') return t('health_unreachable_value', {value: label, age: relativeAge(fact.ageSeconds, t)});
  if (!checking) return fact.freshness === 'stale' && age
    ? t('health_join', {left: label, right: relativeAge(fact.ageSeconds, t)}) : label;
  if (fact.freshness === 'stale' && age) return t('health_stale_value', {value: label,
    age: relativeAge(fact.ageSeconds, t), checking: t('health_checking')});
  if (['stale', 'unknown', 'invalidated'].includes(fact.freshness) && fact.key !== 'ui_connecting')
    return label.includes(t('health_checking')) ? label : t('health_unknown_value', {value: label, checking: t('health_checking')});
  return label;
}
