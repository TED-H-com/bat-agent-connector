// Format an elapsed age only. Callers own the clock, freshness and the choice
// between a relative age and a calendar date. Units are shared across all views.
export const relativeAgeStrings = {
  en: {age_unknown: 'Age unknown', age_recent: 'just now', age_under_minute: '<1 min',
    age_minute: '{n} min', age_hour: '{n} hr', age_day: '{n} d', age_past: '{age} ago', age_future: 'in {age}'},
  'zh-TW': {age_unknown: '時間未知', age_recent: '剛剛', age_under_minute: '不到 1 分鐘',
    age_minute: '{n} 分鐘', age_hour: '{n} 小時', age_day: '{n} 天', age_past: '{age}前', age_future: '{age}後'},
};
// Standalone time labels use the same single-pass interpolation contract as t.
export function relativeAgeTranslator(locale = 'en') {
  const strings = relativeAgeStrings[String(locale).toLowerCase().startsWith('zh') ? 'zh-TW' : 'en'];
  return (key, vars = {}) => (strings[key] ?? key).replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ''));
}
export function relativeAge(seconds, t, {direction = 'past'} = {}) {
  if (!Number.isFinite(seconds)) return t('age_unknown');
  const age = Math.max(0, seconds);
  if (age < 60) return t(direction === 'duration' ? 'age_under_minute' : 'age_recent');
  const [unit, size] = age >= 86400 ? ['day', 86400] : age >= 3600 ? ['hour', 3600] : ['minute', 60];
  const value = t('age_' + unit, {n: Math.floor(age / size)});
  return direction === 'duration' ? value : t('age_' + direction, {age: value});
}
