// One formatter family. Date-only strings are calendar dates, never UTC instants.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAY = 86400000;
const zh = locale => String(locale || 'en').toLowerCase().startsWith('zh');
export function calendarDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date(year, month - 1, day);
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day ? date : null;
}
function parse(value) {
  if (value == null || value === '' || !(typeof value === 'string' || typeof value === 'number' || value instanceof Date)) return null;
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && !calendarDate(value)) return null;
  const date = calendarDate(value) || (value instanceof Date ? value : new Date(value));
  return Number.isFinite(date.getTime()) ? date : null;
}
function clock(date, chinese) {
  const hour = date.getHours(), minute = String(date.getMinutes()).padStart(2, '0');
  const period = hour < 12 ? (chinese ? '上午' : 'AM') : (chinese ? '下午' : 'PM');
  return chinese ? `${period}${hour % 12 || 12}:${minute}` : `${hour % 12 || 12}:${minute} ${period}`;
}
export function compactDate(value, {locale = 'en', now = Date.now()} = {}) {
  const date = parse(value);
  if (!date) return '';
  const year = date.getFullYear() !== new Date(now).getFullYear() ? date.getFullYear() : null;
  return zh(locale) ? `${year == null ? '' : `${year}年`}${date.getMonth() + 1}月${date.getDate()}日`
    : `${MONTHS[date.getMonth()]} ${date.getDate()}${year == null ? '' : `, ${year}`}`;
}
// Axis labels describe the scale, rather than an event at that date.
export function axisDate(value, {locale = 'en', unit = 'day', now = Date.now()} = {}) {
  const date = parse(value);
  if (!date) return '';
  if (unit === 'month') return zh(locale) ? `${date.getMonth() + 1}月` : MONTHS[date.getMonth()];
  return compactDate(date, {locale, now});
}
export function compactTime(value, {locale = 'en', now = Date.now()} = {}) {
  const date = parse(value);
  if (!date) return '';
  const chinese = zh(locale), delta = Number(now) - date.getTime(), distance = Math.abs(delta);
  if (distance < DAY) {
    if (distance < 60000) return chinese ? '剛剛' : 'just now';
    const minutes = Math.floor(distance / 60000), n = minutes < 60 ? minutes : Math.floor(minutes / 60);
    const unit = minutes < 60 ? (chinese ? '分鐘' : 'm') : (chinese ? '小時' : 'h');
    return chinese ? `${n} ${unit}${delta < 0 ? '後' : '前'}` : delta < 0 ? `in ${n}${unit}` : `${n}${unit} ago`;
  }
  return `${compactDate(date, {locale, now})}${chinese ? ' ' : ', '}${clock(date, chinese)}`;
}
export function fullTime(value, {locale = 'en'} = {}) {
  const date = parse(value);
  if (!date) return '';
  return new Intl.DateTimeFormat(zh(locale) ? 'zh-TW' : 'en-US', {
    year: 'numeric', month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit', timeZoneName: 'long',
  }).format(date);
}
