// One name for a maintenance finding. Wrong types are omitted whole: no coercion,
// no truncation, and no subset of an invalid list. Integers above the safe range
// are omitted because JavaScript cannot print them exactly.

const SOURCES = new Set(['issues', 'dependabot_pull_requests', 'dependabot_alerts']);
const SEVERITY = {
  low: {en: 'Low', 'zh-TW': '低', tone: 'neutral'},
  medium: {en: 'Medium', 'zh-TW': '中', tone: 'warn'},
  high: {en: 'High', 'zh-TW': '高', tone: 'bad'},
  critical: {en: 'Critical', 'zh-TW': '嚴重', tone: 'bad'},
};
const SENTINEL = /^(?:undefined|null|nan|\[object object\])$/i;

const COPY = {
  en: {
    issue: 'GitHub issue',
    pr: 'Dependabot PR',
    alert: 'Security alert',
    alertPackage: name => `Security alert: ${name}`,
  },
  'zh-TW': {
    issue: 'GitHub Issue',
    pr: 'Dependabot PR',
    alert: '安全性警示',
    alertPackage: name => `安全性警示：${name}`,
  },
};

function plain(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function acceptedText(value, maximum) {
  if (typeof value !== 'string' || value.length === 0 || Array.from(value).length > maximum) return '';
  if ([...value].some(char => char.charCodeAt(0) < 32)) return '';
  if (SENTINEL.test(value.trim())) return '';
  return value;
}

function acceptedNumber(value) {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : 0;
}

function acceptedPath(value) {
  const text = acceptedText(value, 512);
  if (!text || text.includes('\\')) return '';
  if (text.split('/').some(part => part === '' || part === '.' || part === '..')) return '';
  return text;
}

function acceptedList(value, accept) {
  if (!Array.isArray(value) || value.length > 100) return null;
  const items = [];
  const seen = new Set();
  for (const item of value) {
    const text = accept(item);
    if (!text || seen.has(text)) return null;
    seen.add(text);
    items.push(text);
  }
  return items;
}

export function acceptedFinding(value) {
  if (!plain(value) || !SOURCES.has(value.source)) return null;
  const finding = {source: value.source};
  const number = acceptedNumber(value.number);
  if (number) finding.number = number;
  if (value.source === 'issues') {
    const labels = acceptedList(value.labels, item => acceptedText(item, 100));
    if (labels) finding.labels = labels;
  } else if (value.source === 'dependabot_pull_requests') {
    const manifests = acceptedList(value.manifests, acceptedPath);
    if (manifests) finding.manifests = manifests;
  } else {
    const packageName = acceptedText(value.package, 512);
    const ecosystem = acceptedText(value.ecosystem, 100);
    const ghsa = acceptedText(value.ghsa_id, 100);
    if (packageName) finding.package = packageName;
    if (ecosystem) finding.ecosystem = ecosystem;
    if (ghsa) finding.ghsa_id = ghsa;
    if (Object.hasOwn(SEVERITY, value.severity)) finding.severity = value.severity;
  }
  return finding;
}

export function findingFromNative(record) {
  if (!plain(record) || !plain(record.native)) return null;
  const facts = plain(record.facts) ? record.facts : {};
  return acceptedFinding({
    source: record.native.kind,
    number: record.native.number,
    labels: facts.labels,
    manifests: facts.manifests,
    package: facts.package,
    ecosystem: facts.ecosystem,
    ghsa_id: facts.ghsa_id,
    severity: facts.severity,
  });
}

export function findingSeverity(severity, locale) {
  const row = SEVERITY[severity];
  if (!row) return null;
  return {severity, label: locale === 'zh-TW' ? row['zh-TW'] : row.en, tone: row.tone};
}

export function findingName(finding, locale, fallback = '') {
  const safeFallback = typeof fallback === 'string' ? fallback : '';
  const accepted = acceptedFinding(finding);
  if (!accepted) return safeFallback;
  const copy = COPY[locale] || COPY.en;
  const number = accepted.number;
  if (accepted.source === 'issues') return number ? `${copy.issue} #${number}` : copy.issue;
  if (accepted.source === 'dependabot_pull_requests') return number ? `${copy.pr} #${number}` : copy.pr;
  if (accepted.package) return copy.alertPackage(accepted.package);
  if (number) return `${copy.alert} #${number}`;
  return copy.alert;
}

export const findingNameStrings = {
  en: {
    fn_severity: 'Severity',
    fn_package: 'Package',
    fn_ecosystem: 'Ecosystem',
    fn_labels: 'Labels',
    fn_manifests: 'Manifests',
    fn_ghsa: 'GHSA',
  },
  'zh-TW': {
    fn_severity: '嚴重程度',
    fn_package: '套件',
    fn_ecosystem: '生態系',
    fn_labels: '標籤',
    fn_manifests: '清單路徑',
    fn_ghsa: 'GHSA',
  },
};
