// One member sign-in project. Dates are UTC offsets from a fixed instant so screenshots do not drift.
// User-facing titles and sentences follow the selected locale. Stable ids do not.

export const DEMO_NOW = Date.parse('2026-10-13T09:00:00.000Z');
const DAY = 86400000;

export type DemoLocale = 'en' | 'zh-TW';

export function hexId(prefix: string, n: number, width: number) {
  return prefix + n.toString(16).padStart(width, '0');
}
export function digest(n: number) {
  return n.toString(16).padStart(64, '0');
}
export function sha40(n: number) {
  return n.toString(16).padStart(40, '0');
}
export function day(now: number, offsetDays: number) {
  return new Date(now + offsetDays * DAY).toISOString().slice(0, 10);
}
export function unix(now: number, offsetDays = 0) {
  return Math.floor((now + offsetDays * DAY) / 1000);
}
export function iso(now: number, offsetDays = 0) {
  return new Date(now + offsetDays * DAY).toISOString();
}
export function normalizeLocale(locale?: string): DemoLocale {
  return (locale || '').toLowerCase().startsWith('zh') ? 'zh-TW' : 'en';
}

const requirementIds = {
  email: 'membership.email_sign_in',
  rate: 'membership.rate_limit',
  resetEmail: 'membership.reset_email',
  resetToken: 'membership.reset_token',
  audit: 'membership.audit_log',
  a11y: 'membership.a11y',
  l10n: 'membership.l10n',
  unlock: 'membership.admin_unlock',
} as const;

export type RequirementKey = keyof typeof requirementIds;
export type WorkKey = 'email' | 'rate' | 'resetEmail' | 'resetToken' | 'audit' | 'a11y' | 'l10n' | 'unlock'
  | 'smtpRetry' | 'lockoutMessage' | 'screenReader' | 'zhReset';
export type Column = 'backlog' | 'in_progress' | 'waiting' | 'review' | 'accepted';

const workSpec: {key: WorkKey, n: number, column: Column, requirement: RequirementKey, primary: boolean, version: number, ageDays: number | null, cycleDays: number | null}[] = [
  {key: 'email', n: 1, column: 'accepted', requirement: 'email', primary: true, version: 2, ageDays: null, cycleDays: 4},
  {key: 'rate', n: 2, column: 'review', requirement: 'rate', primary: true, version: 1, ageDays: 2, cycleDays: null},
  {key: 'resetEmail', n: 3, column: 'in_progress', requirement: 'resetEmail', primary: true, version: 1, ageDays: 1, cycleDays: null},
  {key: 'resetToken', n: 4, column: 'in_progress', requirement: 'resetToken', primary: true, version: 1, ageDays: 3, cycleDays: null},
  {key: 'audit', n: 5, column: 'waiting', requirement: 'audit', primary: true, version: 1, ageDays: 5, cycleDays: null},
  {key: 'a11y', n: 6, column: 'backlog', requirement: 'a11y', primary: true, version: 1, ageDays: null, cycleDays: null},
  {key: 'l10n', n: 7, column: 'accepted', requirement: 'l10n', primary: true, version: 1, ageDays: null, cycleDays: 6},
  {key: 'unlock', n: 8, column: 'accepted', requirement: 'unlock', primary: true, version: 1, ageDays: null, cycleDays: 2},
  {key: 'smtpRetry', n: 9, column: 'in_progress', requirement: 'resetEmail', primary: false, version: 1, ageDays: 2, cycleDays: null},
  {key: 'lockoutMessage', n: 10, column: 'review', requirement: 'rate', primary: false, version: 1, ageDays: 2, cycleDays: null},
  {key: 'screenReader', n: 11, column: 'backlog', requirement: 'a11y', primary: false, version: 1, ageDays: null, cycleDays: null},
  {key: 'zhReset', n: 12, column: 'accepted', requirement: 'l10n', primary: false, version: 1, ageDays: null, cycleDays: 1},
];

function text(locale: DemoLocale, en: string, zh: string) {
  return locale === 'zh-TW' ? zh : en;
}

export function demoProject(localeInput?: string, now = DEMO_NOW) {
  const locale = normalizeLocale(localeInput);
  const t = (en: string, zh: string) => text(locale, en, zh);
  const recoveryBaseline = day(now, 3);
  const recoveryForecast = day(now, 6);
  const designDate = day(now, -15);
  const launchDate = day(now, 20);
  const ids = {
    workspaceId: hexId('ws_', 1, 32),
    projectId: hexId('prj_', 1, 20),
    projectBindingId: hexId('pmpb_', 1, 32),
    planV2: hexId('pmrev_', 2, 32),
    planV3: hexId('pmrev_', 3, 32),
    scopeV2: hexId('pmscope_', 2, 32),
    scopeV3: hexId('pmscope_', 3, 32),
    snap1: hexId('pmsnap_', 1, 32),
    snap2: hexId('pmsnap_', 2, 32),
    threadIdea: hexId('pmt_', 1, 32),
    threadPlanning: hexId('pmt_', 2, 32),
    threadFollow: hexId('pmt_', 3, 32),
    draftId: hexId('pmd_', 1, 32),
    proposalId: hexId('pmcp_', 1, 32),
    runId: hexId('pmr_', 1, 32),
    poolId: hexId('pool_', 1, 32),
    roadmapBaseline: hexId('pmrb_', 1, 32),
    roadmapForecast: hexId('pmrf_', 1, 32),
    issueMapping: hexId('pmim_', 1, 32),
    issueRevision: hexId('pmir_', 1, 32),
    host: 'demo-host',
    serverId: 'demo-central',
    controllerId: hexId('ctr_', 1, 32),
    repository: 'example/membership',
    repositoryId: 7,
    providerOrigin: 'https://api.github.com',
    actor: 'reader',
    requirements: requirementIds,
    work: Object.fromEntries(workSpec.map(row => [row.key, hexId('wi_', row.n, 20)])) as Record<WorkKey, string>,
    bindings: Object.fromEntries(workSpec.map(row => [row.key, hexId('pmwb_', row.n, 32)])) as Record<WorkKey, string>,
    sessions: {implement: 'signin-implement', review: 'ratelimit-review', manual: 'manual-reset-notes'},
    sources: {codex: 'codex-implementer', claude: 'claude-reviewer', unavailable: 'security-notes'},
    mergeOp: hexId('op_', 87, 32),
    deployOp: hexId('op_', 91, 32),
  };
  const goal = t(
    'A member can sign in with email and recover access without contacting support.',
    '會員可以用電子郵件登入，並在不聯絡客服的情況下自行重設密碼。');
  const goalV2 = t('Members can sign in and request a password reset.', '會員可以登入，並申請重設密碼。');
  const scope = t(
    'Email sign-in, rate limiting, password reset, audit, accessibility, localisation, and admin unlock. Billing is out of scope.',
    '電子郵件登入、速率限制、重設密碼、稽核、無障礙、在地化與管理員解鎖。帳務不在這次範圍。');
  const stakeholders = t(
    'Product owner confirms scope changes. Security confirms the lockout threshold.',
    '產品負責人確認範圍變更。資安負責人確認鎖定門檻。');
  const quality = t(
    'Acceptance is separate from merge and deployment receipts.',
    '驗收與合併、部署紀錄是分開的。');
  const finance = t('Recovery support budget is not confirmed.', '復原支援預算尚未確認。');
  const resources = t(
    'Codex implements and Claude reviews. One usage source cannot be read.',
    'Codex 負責實作，Claude 負責審查。有一個用量來源目前讀不到。');
  const smtp = t('SMTP provider rate limits are still open.', 'SMTP 服務的寄信速率上限還沒有結論。');
  const blocker = t(
    'An outdated dependency still needs a maintenance finding before launch.',
    '上線前仍有一項過時相依套件的維護發現待處理。');
  const schedule = t(
    `Recovery review baseline is ${recoveryBaseline}; the forecast is three days later, on ${recoveryForecast}.`,
    `復原審查的基準日是 ${recoveryBaseline}，預測日晚三天，為 ${recoveryForecast}。`);
  const scheduleV2 = t(
    `Recovery review was planned for ${recoveryBaseline}.`,
    `復原審查原先排在 ${recoveryBaseline}。`);
  const decisions = {
    budget: t('Recovery support budget', '復原支援預算'),
    lockout: t('Lockout threshold', '鎖定門檻'),
  };
  const name = t('Member sign-in & password reset', '會員登入與忘記密碼');
  const planTitle = t('Member sign-in plan', '會員登入計畫');
  const planTitleV2 = t('Member sign-in plan (v2)', '會員登入計畫（v2）');
  const requirements = [
    {key: 'email' as const, id: requirementIds.email, text: t('Members can sign in with email and password.', '會員可以用電子郵件與密碼登入。'),
      acceptance: t('An incorrect password is rejected and does not start a session.', '密碼錯誤時拒絕登入，且不會建立工作階段。'), priority: 'must' as const},
    {key: 'rate' as const, id: requirementIds.rate, text: t('Sign-in attempts are rate limited.', '登入嘗試會受到速率限制。'),
      acceptance: t('Repeated failures from one account slow further attempts.', '同一帳號連續失敗後，後續嘗試會變慢。'), priority: 'must' as const},
    {key: 'resetEmail' as const, id: requirementIds.resetEmail, text: t('A member can request a password reset email.', '會員可以要求寄送重設密碼的電子郵件。'),
      acceptance: t('The email goes only to the address on the account.', '信件只會寄到帳號上登記的電子郵件地址。'), priority: 'must' as const},
    {key: 'resetToken' as const, id: requirementIds.resetToken, text: t('A reset link expires and works only once.', '重設連結會逾期，而且只能使用一次。'),
      acceptance: t('An expired or reused link cannot set a new password.', '逾期或重複使用的連結不能設定新密碼。'), priority: 'must' as const},
    {key: 'audit' as const, id: requirementIds.audit, text: t('Sign-in and reset events are written to the audit log.', '登入與重設密碼的事件會寫入稽核紀錄。'),
      acceptance: t('Each event records the account, result, and time.', '每筆事件都記錄帳號、結果與時間。'), priority: 'should' as const},
    {key: 'a11y' as const, id: requirementIds.a11y, text: t('Sign-in and reset pages meet the accessibility pass.', '登入與重設密碼頁面要通過無障礙檢查。'),
      acceptance: t('Errors are named for a screen reader and are not color-only.', '錯誤訊息可供螢幕閱讀器朗讀，而且不能只靠顏色表達。'), priority: 'should' as const},
    {key: 'l10n' as const, id: requirementIds.l10n, text: t('Sign-in and reset copy is localised for English and Traditional Chinese.', '登入與重設密碼的文案要提供英文與繁體中文。'),
      acceptance: t('zh-TW uses 重設密碼 and does not leave English labels in the form.', '繁體中文介面使用「重設密碼」，表單不殘留英文標籤。'), priority: 'should' as const},
    {key: 'unlock' as const, id: requirementIds.unlock, text: t('An administrator can unlock a locked account.', '管理員可以解鎖被鎖定的帳號。'),
      acceptance: t('Unlock is recorded in the audit log and does not reveal the password.', '解鎖會寫入稽核紀錄，而且不會顯示密碼。'), priority: 'should' as const},
  ];
  const workTitles: Record<WorkKey, string> = {
    email: t('Email sign-in', '電子郵件登入'),
    rate: t('Rate-limit policy', '速率限制政策'),
    resetEmail: t('Reset email', '重設密碼信'),
    resetToken: t('Reset link expiry', '重設連結有效期限'),
    audit: t('Audit log', '稽核紀錄'),
    a11y: t('Accessibility pass', '無障礙檢查'),
    l10n: t('Localised sign-in copy', '登入文案在地化'),
    unlock: t('Admin unlock', '管理員解鎖'),
    smtpRetry: t('SMTP retry for reset email', '重設信的 SMTP 重試'),
    lockoutMessage: t('Lockout message', '鎖定提示'),
    screenReader: t('Screen-reader names', '螢幕閱讀器名稱'),
    zhReset: t('Traditional Chinese reset copy', '重設密碼繁體中文文案'),
  };
  const work = workSpec.map(row => ({
    ...row,
    id: ids.work[row.key],
    bindingId: ids.bindings[row.key],
    title: workTitles[row.key],
    requirementId: requirementIds[row.requirement],
    ageSeconds: row.ageDays === null ? null : row.ageDays * 86400,
    cycleSeconds: row.cycleDays === null ? null : row.cycleDays * 86400,
  }));
  const copy = {
    name, goal, scope, stakeholders, quality, finance, resources, smtp, blocker, schedule, scheduleV2,
    decisions, planTitle, planTitleV2, goalV2,
    requirements: requirements.map(row => ({id: row.id, text: row.text, acceptance: row.acceptance})),
    work: workTitles,
    threads: {
      idea: t('Idea', '想法'),
      planning: t('Planning draft', '規劃草稿'),
      delivery: t('Delivery review', '交付審查'),
    },
    draftRationale: t(
      'Keep reset, audit, and accessibility in this release, and leave the budget and lockout threshold open.',
      '把重設密碼、稽核與無障礙留在這次範圍，預算與鎖定門檻先維持未決。'),
    proposalTitle: t('Review open sign-in decisions', '審閱尚未決定的登入事項'),
    proposalSummary: t(
      'Keep the eight accepted requirements. Recovery support budget and the lockout threshold still need a decision; they are not new requirements.',
      '保留八項已採用的需求。復原支援預算與鎖定門檻仍待決定，它們不是新需求。'),
    proposalQuestion: t(
      'What lockout threshold should support confirm before the audit log can proceed?',
      '客服要先確認哪個鎖定門檻，稽核紀錄才能繼續？'),
    proposalWhy: t('The audit log is blocked on that security decision.', '稽核紀錄正卡在這項資安決定。'),
    manualTitle: t('Reset notes from BAT', '在 BAT 記下的重設密碼筆記'),
    manualNote: t(
      'These notes were written in BAT while watching the reset flow. The connector does not send instructions to this session.',
      '這些筆記是在 BAT 裡看重設密碼流程時寫下的。Connector 不會對這個工作階段下指令。'),
    sessionImplement: t('Implement email sign-in', '實作電子郵件登入'),
    sessionReview: t('Review the rate-limit policy', '審查速率限制政策'),
    snapshotV2: t('Scope agreed', '範圍已合意'),
    snapshotV3: t('Delivery progress', '交付進度'),
    deliveryMerged: t('Pull request #87 is merged and is not deployed yet.', '合併請求 #87 已合併，但尚未部署。'),
    deliveryDeployed: t('Pull request #91 is deployed.', '合併請求 #91 已部署。'),
    planningRef: t('Planning discussion', '規劃討論'),
    ideaNote: t(
      'Members still contact support to regain access. We should let them sign in and reset a password themselves.',
      '會員現在還是要聯絡客服才能取回帳號。應該讓他們自己登入並重設密碼。'),
    planningNote: t(
      'Keep billing out of this release. Confirm the recovery support budget before we treat the forecast as firm.',
      '這次不包含帳務。在把預測日當成確定日期之前，要先確認復原支援預算。'),
    deliveryNote: t(
      'Recovery review is forecast three days late. SMTP rate limits are still open, and the audit log is waiting on the lockout decision.',
      '復原審查的預測日晚了三天。SMTP 寄信速率上限還沒有結論，稽核紀錄也在等鎖定門檻的決定。'),
    adoptionRationale: t('Adopt the sign-in and recovery scope for this release.', '採用這次登入與重設密碼的範圍。'),
    adoptionRationaleV2: t('Agree the first sign-in and reset scope.', '合意第一版的登入與重設範圍。'),
    issueTitle: t('Email sign-in delivery', '電子郵件登入的交付'),
    bindingRationale: t('Bound to the adopted sign-in plan.', '綁定到已採用的登入計畫。'),
  };
  return {locale, now, copy, ids, requirements, work, dates: {designDate, recoveryBaseline, recoveryForecast, launchDate}};
}

export type DemoProject = ReturnType<typeof demoProject>;
