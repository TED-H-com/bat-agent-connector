import {button, disclosure} from './ui/components.js';
import {setupGuide} from './global-screens.js';
import {internalFrom} from './work-paths.js';
export const eligibilityReasons = {
  PM_NATIVE_PLATFORM_UNSUPPORTED: 'agents_platform', PM_NATIVE_EXECUTABLE_UNAVAILABLE: 'agents_executable',
  PM_NATIVE_AUTH_UNAVAILABLE: 'agents_auth', PM_NATIVE_HOME_UNAVAILABLE: 'agents_home',
  PM_NATIVE_MANAGED_CONFIG_UNSUPPORTED: 'agents_policy', PM_NATIVE_INSTALLATION_UNAVAILABLE: 'agents_installation',
  action_unavailable: 'agents_action', PM_WORKTREE_REQUIRED: 'agents_worktree',
  REPOSITORY_BINDING_CHANGED: 'agents_binding', PM_BAT_BINDING_CHANGED: 'agents_binding',
  REPOSITORY_NOT_CONFIGURED: 'agents_bat_unknown', REPOSITORY_NOT_BOUND: 'agents_binding',
  REPOSITORY_HOST_UNAVAILABLE: 'agents_tier',
  TIER_DISABLED: 'agents_tier', HOST_READ_ONLY: 'agents_tier', START_WORKTREE_REQUIRED: 'agents_start_worktree',
};
export function settingsAgents({h, t, api, caps, guard, from}) {
  const heading = h('h2', {id: 'settings-agents-heading', tabindex: -1, 'data-agents-heading': ''}, t('agents_heading'));
  const status = h('span', {class: 'ui-pill tone-neutral', 'data-pm-eligibility': ''});
  const reason = h('span', {'data-pm-eligibility-reason': ''});
  const codes = h('div');
  const technical = disclosure({label: t('global_details'), body: codes});
  const safeFrom = internalFrom(from);
  const workspaceId = safeFrom?.match(/^#\/pm\/(ws_[a-f0-9]{32})\//)?.[1];
  let ticket = 0;
  const current = mine => {try {guard(); return mine === ticket && root.isConnected;} catch {return false;}};
  const notChecked = () => {status.textContent = t('agents_unchecked'); status.className = 'ui-pill tone-neutral'; reason.textContent = t('agents_not_checked'); codes.replaceChildren(); technical.hidden = true;};
  const paint = doc => {
    const native = doc?.native_analysis, bat = workspaceId ? doc?.bat_analysis : null;
    const descriptors = [native, bat].filter(value => value && typeof value.eligible_for_preflight === 'boolean');
    if (!descriptors.length) {notChecked(); return;}
    const permitted = ['observe', 'manage', 'start'].every(scope => caps()?.scopes?.includes(scope));
    const ready = descriptors.some(value => value.eligible_for_preflight === true && value.action_registered === true && value.authorized !== false) && permitted;
    status.textContent = t(ready ? 'agents_ready' : 'agents_unavailable');
    // Ready is an installation hint, not model execution evidence.
    status.className = `ui-pill tone-${ready ? 'info' : 'neutral'}`;
    const failures = descriptors.map(value => value.action_registered === false ? 'agents_action'
      : value.reason ? eligibilityReasons[value.reason] || (value === bat ? 'agents_bat_unknown' : 'agents_installation')
      : !permitted || value.authorized === false ? 'agents_scope' : 'agents_installation');
    reason.textContent = t(ready ? 'agents_preflight' : failures[0]);
    codes.replaceChildren(...descriptors.flatMap(value => value.reason ? [h('p', {}, t(value === bat ? 'agents_bat' : 'agents_native'), ': ', h('code', {}, value.reason))] : []));
    // The alternate adapter's reason remains inspectable even when local PM is ready.
    if (!ready && descriptors.length > 1) codes.prepend(h('p', {}, t(failures[1])));
    technical.hidden = !codes.childElementCount;
  };
  const load = async () => {
    const mine = ++ticket;
    notChecked(); check.disabled = true;
    const features = caps()?.features?.pm_workspaces;
    const endpoint = workspaceId && (features?.native_analysis || features?.bat_analysis || features?.installation_eligibility)
      ? `/pm/workspaces/${workspaceId}/capabilities` : features?.installation_eligibility ? '/pm/installation-capabilities' : null;
    try {
      if (!endpoint) return;
      const doc = await api('GET', endpoint);
      if (current(mine)) paint(doc);
    } catch {if (current(mine)) notChecked();}
    finally {if (current(mine)) check.disabled = false;}
  };
  const check = button({variant: 'secondary', size: 'sm', label: t('agents_check'), onClick: load, 'data-pm-eligibility-check': ''});
  const pm = h('section', {class: 'settings-agent-group', 'data-settings-pm-agent': ''},
    h('div', {class: 'settings-agent-heading'}, h('h3', {}, t('agents_pm')), status, check),
    h('p', {}, t('agents_pm_help')),
    h('p', {}, reason, ' ', h('a', {href: setupGuide() + '#pm-agent-setup'}, t('agents_pm_docs'))), technical);
  const config = JSON.stringify({mcpServers: {bat: {command: 'bat-agent-connector-mcp', args: ['--principal-only'], env: {BATC_API_TOKEN: '<SCOPED_TOKEN>'}}}}, null, 2);
  const copyStatus = h('span', {role: 'status'});
  const copy = button({variant: 'secondary', size: 'sm', label: t('agents_copy'), onClick: async () => {
    try {guard(); await navigator.clipboard.writeText(config); guard(); if (root.isConnected) copyStatus.textContent = t('agents_copied');}
    catch {if (root.isConnected) copyStatus.textContent = t('agents_copy_failed');}
  }});
  const bundled = Boolean(caps()?.managed_installation);
  const mcp = h('section', {class: 'settings-agent-group', 'data-settings-mcp': ''}, h('h3', {}, t('agents_mcp')),
    h('p', {}, t('agents_mcp_can')), h('p', {}, t('agents_mcp_limits')),
    h('p', {class: 'muted'}, t(bundled ? 'agents_bundle' : 'agents_operator')),
    ...(!bundled ? [h('pre', {'data-mcp-config': ''}, h('code', {}, config)), h('div', {class: 'actions'}, copy, copyStatus),
      h('p', {}, t('agents_token')), disclosure({label: t('agents_token_issue'), body: h('div', {}, h('p', {}, t('agents_issue')),
        h('pre', {}, h('code', {}, 'batc api-token issue --actor mcp-agent --scope observe --scope operate --scope start')),
        h('p', {}, t('agents_scopes')))})] : []),
    h('div', {class: 'actions'}, h('a', {href: setupGuide() + '#cli-and-agent-access'}, t('agents_mcp_docs')),
      h('a', {href: 'https://github.com/TED-H-com/bat-agent-connector/blob/main/docs/agent-skills.md'}, t('agents_skill_docs'))));
  const root = h('section', {class: 'panel settings-agents', 'aria-labelledby': heading.id}, heading,
    safeFrom ? h('a', {href: safeFrom, 'data-agents-back': ''}, t('agents_back')) : null, pm, mcp);
  notChecked();
  return {root, heading, load};
}
