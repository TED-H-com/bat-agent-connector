const time = value => typeof value === 'number' && Number.isFinite(value) ? value
  : typeof value === 'string' && value ? Date.parse(value) : NaN;

// Display evidence only. This clock never participates in an action gate.
// Inventory-derived facts with missing producer validity use null, never the
// live-reader fallback or a successful HTTP receipt as a fresh BAT observation.
export function displayFact(fact = {}, {now = Date.now(), bootstrap, version, defaultValidityMs = 60_000} = {}) {
  const observed = time(fact.observed_at), deadline = time(fact.valid_until);
  const ageSeconds = Number.isFinite(observed) ? Math.max(0, Math.floor((now - observed) / 1000)) : null;
  const invalidated = fact.static && (fact.bootstrap !== bootstrap || fact.version !== version);
  const freshness = invalidated ? 'invalidated' : fact.static ? 'current'
    : fact.stale ? 'stale' : !Number.isFinite(observed) ? 'unknown'
    : Number.isFinite(deadline) ? now >= deadline ? 'stale' : 'current'
    : defaultValidityMs === null ? 'unknown'
    : now - observed >= defaultValidityMs ? 'stale' : 'current';
  return {...fact, freshness, ageSeconds};
}

export function healthAge(seconds, t) {
  return seconds === null ? t('health_age_unknown') : seconds < 60 ? t('health_age_recent')
    : t('health_age_minutes', {count: Math.floor(seconds / 60)});
}
export function healthLabel(fact, t, {age = true} = {}) {
  const label = t(fact.key === 'health_unknown' ? 'obs_unknown' : fact.key || fact.value);
  if (fact.error) return t('health_read_failed', {value: label, age: healthAge(fact.ageSeconds, t), reason: fact.error.code || fact.error.status});
  if (fact.stale_reason === 'host_unreachable') return t('health_unreachable_value', {value: label, age: healthAge(fact.ageSeconds, t)});
  if (fact.freshness === 'stale' && age) return t('health_stale_value', {value: label,
    age: healthAge(fact.ageSeconds, t), checking: t('health_checking')});
  if (['stale', 'unknown', 'invalidated'].includes(fact.freshness) && fact.key !== 'ui_connecting')
    return label.includes(t('health_checking')) ? label : t('health_unknown_value', {value: label, checking: t('health_checking')});
  return label;
}
