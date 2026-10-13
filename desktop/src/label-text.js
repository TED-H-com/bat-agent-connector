// Composed labels omit a missing piece instead of printing undefined, null, NaN or [object Object].
const SENTINEL = /^(?:undefined|null|nan|\[object object\])$/i;

export function labelSegment(value) {
  if (typeof value === 'string') {
    const text = value.trim();
    return text && !SENTINEL.test(text) ? text : '';
  }
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return '';
}

export function firstLabel(...values) {
  for (const value of values) {
    const text = labelSegment(value);
    if (text) return text;
  }
  return '';
}

export function joinLabel(parts, separator = ' · ') {
  return parts.map(labelSegment).filter(Boolean).join(separator);
}
