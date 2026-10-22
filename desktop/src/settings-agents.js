import {button, disclosure} from './ui/components.js';
import {setupGuide} from './global-screens.js';
import {internalFrom} from './work-paths.js';
import {eligibilityReasons, pmAvailability, renderPmAvailability} from './pm-availability.js';
export {eligibilityReasons};
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
  const failure = h('div', {'data-pm-eligibility-error': ''});
  const paintState = (state, descriptors = [], bat = null) => {
    status.textContent = t(state.statusKey);
    // Ready is an installation hint, not model execution evidence.
    status.className = `ui-pill tone-${state.ready ? 'info' : 'neutral'}`;
    reason.textContent = t(state.messageKey);
    renderPmAvailability(failure, {h, t, state, includeMessage: false});
    codes.replaceChildren(...descriptors.flatMap(value => value.reason
      ? [h('p', {}, t(value === bat ? 'agents_bat' : 'agents_native'), ': ', h('code', {}, value.reason))] : []));
    // The alternate adapter's reason remains inspectable even when local PM is ready.
    if (!state.ready && state.failures.length > 1) codes.prepend(h('p', {}, t(state.failures[1].messageKey)));
    technical.hidden = !codes.childElementCount;
  };
  const paint = doc => {
    const descriptors = [doc?.native_analysis, workspaceId ? doc?.bat_analysis : null]
      .filter(value => value && typeof value.eligible_for_preflight === 'boolean');
    paintState(pmAvailability({caps: caps(), document: doc, descriptors, checked: true}), descriptors, workspaceId ? doc?.bat_analysis : null);
  };
  let busy = false;
  const load = async () => {
    if (busy) return;
    busy = true;
    const mine = ++ticket;
    paintState(pmAvailability({caps: caps(), checking: true})); check.setAttribute('aria-disabled', 'true'); status.setAttribute('aria-busy', 'true');
    const features = caps()?.features?.pm_workspaces;
    const endpoint = workspaceId && (features?.native_analysis || features?.bat_analysis || features?.installation_eligibility)
      ? `/pm/workspaces/${workspaceId}/capabilities` : features?.installation_eligibility ? '/pm/installation-capabilities' : null;
    try {
      if (!endpoint) {paintState(pmAvailability({caps: caps(), checked: true, document: null})); return;}
      const doc = await api('GET', endpoint);
      if (current(mine)) paint(doc);
    } catch (error) {if (current(mine)) paintState(pmAvailability({caps: caps(), readError: error, checked: true}));}
    finally {
      if (mine === ticket) busy = false;
      if (current(mine)) {check.removeAttribute('aria-disabled'); status.removeAttribute('aria-busy');}
    }
  };
  const check = button({variant: 'secondary', size: 'sm', label: t('agents_check'), onClick: load, 'data-pm-eligibility-check': ''});
  const pm = h('section', {class: 'settings-agent-group', 'data-settings-pm-agent': ''},
    h('div', {class: 'settings-agent-heading'}, h('h3', {}, t('agents_pm')), status, check),
    h('p', {}, t('agents_pm_help')),
    h('p', {}, reason, ' ', h('a', {href: setupGuide() + '#pm-agent-setup'}, t('agents_pm_docs'))), failure, technical);
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
  paintState(pmAvailability({caps: caps(), checked: false}));
  return {root, heading, load};
}
