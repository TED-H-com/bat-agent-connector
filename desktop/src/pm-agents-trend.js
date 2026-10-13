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

export function stackDailyBuckets(buckets, now = new Date()) {
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
    return {
      date,
      parts,
      partial: parts.some(part => part.total == null),
      mark: open ? (date === today ? 'in_progress' : 'settling') : '',
    };
  });
  return {tz, clis, days};
}
