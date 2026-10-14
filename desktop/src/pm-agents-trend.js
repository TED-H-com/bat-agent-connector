// Daily usage bars. Totals are never rebuilt from input, cache, output or reasoning.
const CLI_ORDER = ['codex', 'claude', 'grok', 'gemini', 'bat', 'pm'];
const CLI_LABEL = {codex: 'Codex', claude: 'Claude', grok: 'Grok', gemini: 'Gemini', bat: 'BAT', pm: 'PM'};

export function cliName(cli) {
  return Object.hasOwn(CLI_LABEL, cli) ? CLI_LABEL[cli] : (typeof cli === 'string' ? cli : '');
}

export function cliClass(cli) {
  return Object.hasOwn(CLI_LABEL, cli) ? cli : 'other';
}

export function browserTimeZone() {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (typeof zone === 'string' && /^[A-Za-z0-9_+/-]+$/.test(zone)) return zone;
  } catch { /* the view retries once with UTC */ }
  return 'UTC';
}

export function dayKey(instant, timeZone) {
  const zone = timeZone || 'UTC';
  try {
    return new Intl.DateTimeFormat('en-CA', {timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit'}).format(instant);
  } catch {
    return new Intl.DateTimeFormat('en-CA', {timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit'}).format(instant);
  }
}

export function isBucketPayload(buckets) {
  return Boolean(buckets && buckets.granularity === 'day' && typeof buckets.tz === 'string' && buckets.tz
    && Array.isArray(buckets.series));
}

// One step above the peak: 1, 2, 2.5 or 5 × 10ⁿ. A peak that already lands on a step moves up.
export function niceCeiling(value) {
  if (!(value > 0)) return 0;
  const exp = Math.floor(Math.log10(value));
  const pow = 10 ** exp;
  const frac = value / pow;
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (frac < step - 1e-9) return step * pow;
  }
  return 10 * pow;
}

function shiftDate(isoDate, delta) {
  const [y, m, d] = String(isoDate || '').split('-').map(Number);
  if (!y || !m || !d) return '';
  return new Date(Date.UTC(y, m - 1, d + delta)).toISOString().slice(0, 10);
}

function zoneParts(instant, timeZone) {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  const map = {};
  for (const part of fmt.formatToParts(instant)) map[part.type] = part.value;
  return {
    year: Number(map.year), month: Number(map.month), day: Number(map.day),
    hour: Number(map.hour) % 24, minute: Number(map.minute), second: Number(map.second),
  };
}

function alignMidnight(isoDate, timeZone) {
  const [y, m, d] = isoDate.split('-').map(Number);
  let utc = Date.UTC(y, m - 1, d);
  for (let i = 0; i < 4; i++) {
    const got = zoneParts(new Date(utc), timeZone);
    utc += Date.UTC(y, m - 1, d) - Date.UTC(got.year, got.month - 1, got.day, got.hour, got.minute, got.second);
  }
  return new Date(utc);
}

export function zonedMidnight(isoDate, timeZone) {
  const zone = timeZone || 'UTC';
  try {return alignMidnight(isoDate, zone);}
  catch {return alignMidnight(isoDate, 'UTC');}
}

// since is local midnight of (today − N + 1). until is now. "1d" is today only.
export function periodWindow(now, days, timeZone) {
  const zone = timeZone || 'UTC';
  const count = Math.max(1, Number(days) || 1);
  const today = dayKey(now, zone);
  const since = zonedMidnight(shiftDate(today, 1 - count), zone);
  return {since: since.toISOString(), until: now.toISOString()};
}

function cliRank(cli) {
  const index = CLI_ORDER.indexOf(cli);
  return index < 0 ? CLI_ORDER.length : index;
}

function numberOrNull(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

// A CLI total is the sum of that CLI's sources only when every addend is a number.
function combine(values) {
  if (!values.length || values.some(value => value == null)) return null;
  return values.reduce((sum, value) => sum + value, 0);
}

function clippedDay(date, tz, bounds) {
  const since = Date.parse(bounds?.since);
  if (!Number.isFinite(since) || typeof date !== 'string') return false;
  return zonedMidnight(date, tz).getTime() < since;
}

export function stackDailyBuckets(buckets, now = new Date(), bounds) {
  if (!isBucketPayload(buckets)) return null;
  const tz = buckets.tz;
  const today = dayKey(now, tz);
  const dates = [];
  const seen = new Set();
  const grouped = new Map();
  for (const series of buckets.series) {
    const cli = typeof series?.cli === 'string' && series.cli ? series.cli : '';
    if (!cli) continue;
    if (!grouped.has(cli)) grouped.set(cli, []);
    grouped.get(cli).push(series);
    for (const day of series.days || []) {
      if (typeof day?.date !== 'string' || seen.has(day.date)) continue;
      seen.add(day.date);
      dates.push(day.date);
    }
  }
  dates.sort();
  const clis = [...grouped.keys()].sort((a, b) => cliRank(a) - cliRank(b) || a.localeCompare(b));
  const days = dates.map(date => {
    let open = false;
    const parts = clis.map(cli => {
      const totals = grouped.get(cli).map(series => {
        const day = (series.days || []).find(item => item?.date === date);
        if (!day) return null;
        if (day.complete !== true) open = true;
        return numberOrNull(day.tokens?.total);
      });
      return {cli, total: combine(totals)};
    });
    let mark = '';
    if (open) {
      if (clippedDay(date, tz, bounds)) mark = 'partial_day';
      else if (date === today) mark = 'in_progress';
      else mark = 'settling';
    }
    return {date, parts, partial: parts.some(part => part.total == null), mark};
  });
  return {tz, clis, days};
}
