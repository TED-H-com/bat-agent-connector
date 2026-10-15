import './main.css';
import { h, place } from '../../src/ui/dom.js';
import { pageHeader, sectionCard, iconButton, inspector, tabs, searchField, menu, trapFocus, statusPill } from '../../src/ui/components.js';
import { icon } from '../../src/ui/icons.js';
import { projectViewIds, viewIcons } from '../../src/pm-workbench.js';
import { locale, setLocale, t } from './proto-i18n.js';
import { GOLDEN_CONSTANTS as C, FIXED_NOW, WORK_LIFECYCLE_ORDER, DIFF_HUNKS, createScenarioState } from './golden-data.js';
import { pill, action, notice, details, facts, message, composer, planCard, stepList, deliveryBar, unifiedDiffView } from './candidate-components.js';
const scenes = ['idea', 'planned', ...WORK_LIFECYCLE_ORDER, 'external_readonly', 'hermes', 'disconnect_recovery', 'maintenance', 'requirement_accepted', 'merged', 'deployed'];
const queryScene = new URLSearchParams(location.search).get('scene');
let state = createScenarioState(scenes.includes(queryScene) ? queryScene : 'idea', locale);
let theme = 'light',
  barExpanded = false,
  cleanups = [],
  currentInspector = null,
  streamTimer = null,
  previousPanelOpen = false,
  previousPanelView = null;
if (queryScene === 'hermes') {
  state.hermesVisible = true;
  state.activeView = 'work_hermes';
}
const tid = id => ({
  'data-testid': id
});
const workViews = ['work_main', 'work_derived', 'work_separate'];
const go = view => {
  state.noteSavedBanner = false;
  state.activeView = view;
  if (workViews.includes(view)) state.workOpened = true;
  renderApp();
  window.scrollTo(0, 0);
};
const workTitle = () => t(state.activeView === 'work_derived' ? 'derivedTitle' : state.activeView === 'work_separate' ? 'separateTitle' : 'workTitle');
const workCopy = key => t((state.activeView === 'work_derived' ? 'derived_' : state.activeView === 'work_separate' ? 'separate_' : '') + key);
const workIdentity = () => state.activeView === 'work_derived' ? C.derivedIdentity : state.activeView === 'work_separate' ? C.separateIdentity : {
  workId: C.workId,
  attemptId: C.attemptId,
  sessionId: C.managedSessionId,
  worktreeId: C.managedWorktreeId,
  operationKey: C.operationKey,
  operationId: C.operationId
};
function startSeparate(view) {
  if (!state.additionalWorks.includes(view)) {
    state.additionalWorks.push(view);
    state.startRequestsSent++;
  }
  state.workState = 'running';
  state.secondSentenceQueued = false;
  state.queuedMessage = null;
  state.deliveredMessages = [];
  state.nextStepDraft = '';
  state.disconnectedDuringStart = false;
  state.reconciledAfterDisconnect = false;
  state.advancedExpanded = false;
  go(view);
}
const deliveryScene = () => state.delivery.deployed ? 'deployed' : state.delivery.merged ? 'merged' : state.delivery.accepted ? 'requirement_accepted' : state.workState;
const threadState = () => state.workStarted ? deliveryScene() : state.planAdoptedLater ? 'planned' : 'idea';
const scene = () => state.activeView === 'work_hermes' ? 'hermes' : state.activeView === 'work_manual' ? 'external_readonly' : ['maintenance', 'work_maint'].includes(state.activeView) ? 'maintenance' : workViews.includes(state.activeView) && state.disconnectedDuringStart ? 'disconnect_recovery' : workViews.includes(state.activeView) ? deliveryScene() : state.workStarted && state.planVisible && state.adoptMode === 'completed' ? deliveryScene() : state.planVisible ? 'planned' : 'idea';
function switchScene(value) {
  const postDelivery = ['requirement_accepted', 'merged', 'deployed'].includes(value);
  state = createScenarioState(postDelivery ? 'awaiting_verification' : value, locale);
  if (postDelivery) {
    Object.assign(state.delivery, {
      pr_open: true,
      accepted: true,
      merged: value !== 'requirement_accepted',
      deployed: value === 'deployed'
    });
    state.pmAssessment = 'passed';
    state.ownerDecisionRecorded = 'decisionAccepted';
    state.activeView = 'overview';
  }
  if (value === 'hermes') {
    state.hermesVisible = true;
    state.activeView = 'work_hermes';
  }
  renderApp();
}
function setLanguage(next) {
  const first = C.firstSentence[locale],
    second = C.secondSentence[locale];
  const translate = text => text === first ? C.firstSentence[next] : text === second ? C.secondSentence[next] : text;
  state.discussionNotes.forEach(note => { note.body = translate(note.body); });
  state.queuedMessage = translate(state.queuedMessage);
  state.deliveredMessages = state.deliveredMessages.map(translate);
  setLocale(next);
  if (state.composerDraft === first) state.composerDraft = C.firstSentence[locale];
  if (state.nextStepDraft === second) state.nextStepDraft = C.secondSentence[locale];
  renderApp();
}
function startWork(open = false) {
  state.planVisible = true;
  state.adoptMode = 'completed';
  state.adoptSteps = state.adoptSteps.map(step => ({
    ...step,
    status: 'done'
  }));
  if (!state.workStarted) {
    state.workStarted = true;
    state.startRequestsSent++;
  }
  state.workState = 'running';
  if (open) state.activeView = 'work_main';
  renderApp();
}
function partialStart() {
  state.planVisible = true;
  state.adoptMode = 'partial_failure';
  state.adoptSteps = state.adoptSteps.map((step, i) => ({
    ...step,
    status: i < 3 ? 'done' : i === 3 ? 'failed' : 'pending'
  }));
  go('discussions');
}
function setWorkState(next) {
  state.workStarted = true;
  if (['turn_ended', 'awaiting_verification'].includes(next) && state.queuedMessage) {
    state.deliveredMessages.push(state.queuedMessage);
    state.queuedMessage = null;
    state.secondSentenceQueued = false;
    state.nextStepDraft = '';
  }
  state.turnInterrupted = false;
  state.workState = next;
  state.disconnectedDuringStart = false;
  state.delivery.self_reported = ['turn_ended', 'awaiting_verification'].includes(next);
  state.delivery.tests_passed = next === 'awaiting_verification';
  go('work_main');
}
function disconnect() {
  if (!state.workStarted) {
    state.workStarted = true;
    state.startRequestsSent++;
  }
  state.workState = 'starting';
  state.disconnectedDuringStart = true;
  state.advancedExpanded = false;
  go('work_main');
}
const select = (label, id, options, value, onChange) => h('label', {
  class: 'proto-select'
}, h('span', {}, label), h('select', {
  ...tid(id),
  'aria-label': label,
  onchange: e => onChange(e.target.value)
}, ...options.map(([key, text]) => h('option', {
  value: key,
  selected: key === value
}, text))));
function renderBar() {
  return h('aside', {
    class: 'proto-bar',
    ...tid('prototype-bar'),
    'aria-label': t('prototype')
  }, h('div', {
    class: 'proto-bar-row'
  }, h('strong', {}, t('prototype')), select(t('stateSim'), 'scenario-picker', scenes.map(key => [key, t(key)]), scene(), switchScene), iconButton({
    icon: barExpanded ? 'chevron-up' : 'chevron-down',
    label: t('controls'),
    'aria-expanded': String(barExpanded),
    ...tid('proto-expand'),
    onClick: () => {
      barExpanded = !barExpanded;
      renderApp();
    }
  })), h('div', {
    class: 'proto-bar-extra',
    hidden: !barExpanded
  }, h('p', {}, t('synthetic')), h('div', {
    class: 'proto-actions'
  }, action('reset', 'proto-reset', () => switchScene(scene())), action('first', 'fill-first-sentence', () => {
    state.composerDraft = C.firstSentence[locale];
    renderApp();
  }), action('second', 'fill-second-sentence', () => {
    state.nextStepDraft = C.secondSentence[locale];
    state.composerDraft = C.secondSentence[locale];
    renderApp();
  }), action('pmUnavailableSim', 'toggle-pm-unavailable', () => {
    state.noteSavedBanner = false;
    state.pmAvailable = !state.pmAvailable;
    go('discussions');
  }), action('queueFailureSim', 'toggle-queue-failure', () => {
    state.queueAvailable = !state.queueAvailable;
    renderApp();
  }), action('partialSim', 'plan-simulate-partial', partialStart), action('disconnectSim', 'simulate-disconnect', disconnect), action('hermesSim', 'proto-trigger-hermes', () => {
    state.hermesVisible = true;
    go('work_hermes');
  }), action('mergedSim', 'btn-record-merge', () => {
    state.delivery.merged = true;
    renderApp();
  }, 'secondary', {
    disabled: !state.delivery.pr_open
  }), action('deployedSim', 'btn-record-deploy', () => {
    state.delivery.deployed = true;
    renderApp();
  }, 'secondary', {
    disabled: !state.delivery.merged
  }), action(theme === 'light' ? 'dark' : 'light', 'proto-theme-toggle', () => {
    theme = theme === 'light' ? 'dark' : 'light';
    document.documentElement.dataset.theme = theme;
    renderApp();
  }), h('button', {
    class: 'ui-button secondary sm',
    ...tid('proto-lang-en'),
    onclick: () => setLanguage('en')
  }, 'English'), h('button', {
    class: 'ui-button secondary sm',
    ...tid('proto-lang-zh'),
    onclick: () => setLanguage('zh-TW')
  }, '繁體中文')), h('div', {
    class: 'proto-actions'
  }, select(t('stateSim'), 'work-state-picker', WORK_LIFECYCLE_ORDER.map(key => [key, t(key)]), state.workState, setWorkState), select(t('assessmentSim'), 'assessment-picker', ['passed', 'failed', 'insufficient'].map(key => [key, t(key)]), state.pmAssessment, value => {
    state.pmAssessment = value;
    renderApp();
  })), h('div', {
    class: 'proto-counters'
  }, ...[['modelCalls', 'model-calls', 'modelCalls'], ['manualMutations', 'manual-mutations', 'manualMutations'], ['hermesMutations', 'hermes-mutations', 'hermesMutations'], ['startRequestsSent', 'start-requests', 'starts'], ['duplicateStarts', 'duplicate-starts', 'duplicates']].map(([field, id, label]) => h('span', {
    ...tid('counter-' + id),
    'data-count': state[field]
  }, t(label, {
    n: state[field]
  }))))));
}
function navButton(view, title, work = false) {
  return h('button', {
    type: 'button',
    class: work ? 'workspace-tree-link proto-work-link' : 'ui-button ghost',
    title,
    'aria-current': state.activeView === view ? 'page' : 'false',
    ...tid('nav-item-' + view),
    'data-pm-view': work ? null : view,
    onclick: () => go(view)
  }, work ? null : icon(viewIcons[view], {
    size: 20
  }), h('span', {
    class: work ? 'workspace-tree-label' : ''
  }, title), work ? h('span', {
    class: 'workspace-tree-state',
    'aria-hidden': 'true'
  }, h('span', {
    class: 'workspace-dot info'
  })) : null);
}
function sidebar() {
  const switcher = menu({
    button: h('button', {
      type: 'button',
      class: 'shell-workspace-switch',
      'aria-label': t('ui_switch_workspace')
    }, h('span', {
      class: 'shell-workspace-name'
    }, t('projectName')), icon('chevron-down')),
    items: [{
      label: t('projectName'),
      onSelect: () => go('overview')
    }]
  });
  cleanups.push(() => switcher.dispose());
  const workRows = [state.workStarted ? ['work_main', t('workTitle')] : null, ...state.additionalWorks.map(view => [view, t(view === 'work_derived' ? 'derivedTitle' : 'separateTitle')]), ['work_manual', t('manualTitle')], state.hermesVisible ? ['work_hermes', t('hermesTitle')] : null, state.maintenanceFixStarted ? ['work_maint', t('maintenanceTitle')] : null].filter(Boolean);
  const search = h('input', {
    type: 'search',
    placeholder: t('ui_search'),
    'aria-label': t('ui_search_work'),
    oninput: e => {
      for (const row of document.querySelectorAll('.proto-work-link')) row.hidden = !row.textContent.toLowerCase().includes(e.target.value.toLowerCase());
    }
  });
  return h('aside', {
    class: 'workspace-nav has-pm-context',
    id: 'workspace-sidebar',
    ...tid('shell-sidebar'),
    'aria-label': t('workspace_navigation')
  }, h('div', {
    class: 'shell-workspace-block'
  }, switcher, pill(t(state.maintenanceFixStarted ? 'ui_phase_maintain' : state.workStarted ? 'ui_phase_development' : state.planVisible ? 'ui_phase_planning' : 'ui_phase_idea'))), h('div', {
    class: 'shell-project-nav'
  }, h('h2', {
    class: 'shell-nav-label'
  }, t('ui_project')), h('nav', {
    class: 'pm-project-views',
    'aria-label': t('wb_navigation')
  }, ...projectViewIds.map(id => navButton(id, t('ui_' + id))))), h('div', {
    class: 'shell-work-header'
  }, h('h2', {
    class: 'shell-nav-label'
  }, t('ui_work')), action('ui_all_projects', 'all-projects', () => go('projects'), 'ghost')), searchField(search, {
    class: 'workspace-search'
  }), h('div', {
    class: 'workspace-tree-scroll'
  }, ...workRows.map(([id, title]) => navButton(id, title, true))), h('div', {
    class: 'workspace-nav-footer'
  }, ...[['pending', 'ui_pending', 'inbox'], ['tools', 'ui_tools', 'wrench'], ['settings', 'ui_settings', 'settings']].map(([id, key, glyph]) => h('a', {
    href: '#' + id,
    onclick: e => {
      e.preventDefault();
      go(id);
    }
  }, icon(glyph), h('span', {}, t(key))))));
}
function openDrawer() {
  const aside = document.getElementById('workspace-sidebar');
  document.body.classList.add('workspace-nav-open');
  state.navDrawerOpen = true;
  document.getElementById('main').inert = true;
  document.querySelector('.shell-scrim').hidden = false;
  aside.setAttribute('role', 'dialog');
  aside.setAttribute('aria-modal', 'true');
  document.getElementById('workspace-menu').setAttribute('aria-expanded', 'true');
  aside.querySelector('button').focus();
}
function closeDrawer() {
  state.navDrawerOpen = false;
  document.body.classList.remove('workspace-nav-open');
  document.getElementById('main').inert = false;
  document.querySelector('.shell-scrim').hidden = true;
  document.getElementById('workspace-sidebar').removeAttribute('role');
  document.getElementById('workspace-sidebar').removeAttribute('aria-modal');
  document.getElementById('workspace-menu').setAttribute('aria-expanded', 'false');
  document.getElementById('workspace-menu').focus();
}
const header = (title, actions = [], status = null) => pageHeader({
  title,
  pill: status,
  meta: `${t('projectName')} · ${C.serverId}`,
  actions
});
function panel(titleKey, content, id = 'context-inspector') {
  const node = inspector({
    title: t(titleKey),
    dock: true,
    returnFocus: () => document.querySelector('[data-testid="inspector-drawer-toggle"]')
  });
  node.classList.add('proto-inspector');
  node.dataset.testid = id;
  node.dataset.view = state.activeView;
  place(node.body, 'append', content);
  cleanups.push(() => node.dispose());
  currentInspector = node;
  if (previousPanelOpen && previousPanelView === state.activeView) queueMicrotask(() => {
    if (node.isConnected) node.open();
  });
  return node;
}
const inspectorToggle = key => iconButton({
  icon: 'panel-right',
  label: t(key),
  ...tid('inspector-drawer-toggle'),
  onClick: () => currentInspector?.open()
});
function discussionPanel() {
  const body = h('div', {}, tabs({
    label: t('discussionInfo'),
    value: 'context',
    options: ['context', 'inputs', 'planTab'].map(value => ({
      value,
      label: t(value)
    })),
    onChange: value => {
      for (const el of body.querySelectorAll('[data-info-tab]')) el.hidden = el.dataset.infoTab !== value;
    }
  }), h('div', {
    'data-info-tab': 'context'
  }, facts([['requirement', t('requirementName')], ['server', C.serverId]]), details(h('code', {}, C.discussionId))), h('div', {
    'data-info-tab': 'inputs',
    hidden: true
  }, h('p', {}, C.firstSentence[locale])), h('div', {
    'data-info-tab': 'planTab',
    hidden: true
  }, h('p', {}, t('planVersion')), details(h('code', {}, C.planRevision))));
  return panel('discussionInfo', body);
}
function openDiscussionComposer() {
  state.composeOpen = true;
  renderApp();
  document.querySelector('[data-testid="composer-textarea"]').focus();
}
function renderDiscussions() {
  const sendNote = note => {
    state.noteSavedBanner = false;
    if (!state.pmAvailable) return renderApp();
    note.sent = true;
    state.pmReplied = true;
    state.modelCalls++;
    state.composerDraft = '';
    renderApp();
  };
  const send = () => {
    state.noteSavedBanner = false;
    if (state.composerIntent === 'plan') {
      if (!state.composerDraft.trim() && !state.pmReplied) return;
      state.planVisible = true;
      state.modelCalls++;
    } else if (state.composerIntent === 'discuss') {
      if (!state.composerDraft.trim() || !state.pmAvailable) return renderApp();
      const saved = state.discussionNotes.find(note => !note.sent && note.body === state.composerDraft);
      if (saved) return sendNote(saved);
      state.discussionNotes.push({
        body: state.composerDraft,
        sent: true
      });
      state.pmReplied = true;
      state.modelCalls++;
    } else {
      if (!state.composerDraft.trim()) return;
      if (state.workStarted) {
        state.nextStepDraft = state.composerDraft;
        state.composerDraft = '';
        state.activeView = 'work_main';
        queueSecond();
      } else {
        state.composerDraft = '';
        startWork(true);
      }
      return;
    }
    state.composerDraft = '';
    renderApp();
  };
  return h('div', {
    class: 'proto-view',
    ...tid('view-discussions')
  }, header(t('ui_discussions'), [inspectorToggle('discussionInfo')]), h('div', {
    class: 'proto-discussions'
  }, h('div', {
    class: 'proto-thread-list'
  }, searchField(h('input', {
    placeholder: t('ui_search'),
    'aria-label': t('ui_search')
  })), h('button', {
    class: 'proto-thread selected',
    onclick: openDiscussionComposer
  }, h('strong', {}, t('threadTitle')), h('small', {
    ...tid('discussion-list-state')
  }, t(threadState())))), h('div', {
    class: 'proto-thread-detail'
  }, h('header', {
    class: 'proto-thread-heading'
  }, h('strong', {}, t('threadTitle')), pill(t(threadState()), 'neutral', tid('discussion-thread-state')), state.workStarted ? action('openWork', 'discussion-open-work', () => go('work_main'), 'ghost') : null, state.adoptMode !== 'idle' ? action('messageLabel', 'discussion-compose', openDiscussionComposer, 'ghost') : null), h('div', {
    class: 'proto-transcript session-workspace'
  }, h('div', {
    class: 'conversation-scroll'
  }, message('mei.lin', t('threadFirst')), message('PM', t('threadReply'), true), ...state.discussionNotes.map(note => message('mei.lin', h('div', {}, h('p', {
    ...tid('discussion-message-text')
  }, note.body), note.note ? h('small', {
    ...tid('note-kind')
  }, t(note.sent ? 'noteSent' : 'noteUnsent')) : null, !note.sent ? action('sendPm', 'send-saved-note', () => sendNote(note), 'ghost', {
    disabled: !state.pmAvailable
  }) : null), false, {
    ...tid(note.note ? 'saved-note' : 'discussion-message')
  })), state.pmReplied ? message('PM', t('pmReply'), true) : null, state.planVisible && state.adoptMode === 'idle' ? planCard(state, () => {
    state.planAdoptedLater = true;
    renderApp();
  }, () => startWork()) : null, state.adoptMode !== 'idle' ? state.workOpened && state.adoptMode === 'completed' ? h('div', {
    class: 'proto-started-line',
    ...tid('work-started-summary')
  }, h('span', {}, t('workReady')), action('openWork', 'adopt-open-work-page', () => go('work_main'), 'ghost'), details(stepList({
    ...state,
    adoptMode: 'history'
  }, () => startWork(), () => go('work_main')))) : stepList(state, () => startWork(), () => go('work_main')) : null, state.ownerDecisionRecorded ? message('mei.lin', h('div', {
    ...tid('discussion-decision-banner')
  }, h('p', {}, t(state.ownerDecisionRecorded === 'decisionAccepted' ? 'discussionAccepted' : 'discussionFollowup')), action('viewOverview', 'discussion-decision-overview', () => go('overview'), 'ghost')), false, {at: FIXED_NOW}) : null)), !state.pmAvailable ? h('div', {
    class: 'proto-diagnostic',
    ...tid('pm-unavailable-diagnostic')
  }, h('strong', {}, t('pmMissing')), h('p', {}, t('pmReason')), h('p', {}, t('pmHere')), h('p', {}, t('draftKept')), action('setupPm', 'fix-pm-available', () => go('pm_settings'))) : null, state.adoptMode === 'idle' || state.composeOpen ? composer({
    state,
    onIntent: value => {
      state.noteSavedBanner = false;
      state.composerIntent = value;
      renderApp();
    },
    onInput: value => {
      state.composerDraft = value;
      state.noteSavedBanner = false;
      document.querySelector('[data-testid=note-saved-banner]')?.remove();
    },
    onSend: send,
    onNote: () => {
      if (!state.composerDraft.trim()) return;
      state.discussionNotes.push({
        body: state.composerDraft,
        note: true,
        sent: false
      });
      state.composerDraft = '';
      state.noteSavedBanner = true;
      renderApp();
    }
  }) : null), discussionPanel()));
}
function advanced() {
  const identity = workIdentity();
  const node = details(h('div', {}, facts([['ui_work', identity.workId], ['attempt', identity.attemptId], ['ui_session', identity.sessionId], ['preparing_worktree', identity.worktreeId], ['operationKey', identity.operationKey], ['planVersion', C.planRevision]]), h('pre', tid('advanced-raw-events'), `operation: ${identity.operationId}\noperation_key: ${identity.operationKey}${state.secondSentenceQueued ? "\nqueue_receipt: msg_q_02\nsteered: false" : ""}`)), 'advanced');
  node.dataset.testid = 'work-area-advanced';
  node.open = state.advancedExpanded;
  node.addEventListener('toggle', () => {
    if (node.isConnected) state.advancedExpanded = node.open;
  });
  return node;
}
function queueSecond() {
  if (!state.nextStepDraft.trim()) return;
  state.queueFailed = !state.queueAvailable;
  if (state.queueFailed) return renderApp();
  state.secondSentenceSteered = false;
  if (['running', 'waiting_input'].includes(state.workState)) {
    if (state.secondSentenceQueued) return;
    state.queuedMessage = state.nextStepDraft;
    state.secondSentenceQueued = true;
  } else {
    state.deliveredMessages.push(state.nextStepDraft);
    state.workState = 'running';
  }
  state.nextStepDraft = '';
  renderApp();
}
function resultsPanel() {
  return panel('results', h('div', tid('work-area-results'), details(h('ul', {}, ...DIFF_HUNKS.map(item => h('li', {}, h('code', {}, item.file)))), 'files'), details(unifiedDiffView({
    hunks: DIFF_HUNKS
  }), 'diff'), details(h('div', {}, h('code', {}, 'pytest -q tests/auth/test_sign_in_double_submit.py'), h('p', {}, t('testsSummary'))), 'tests'), deliveryBar(state.delivery, () => {
    state.delivery.pr_open = true;
    state.pmAssessment = 'passed';
    renderApp();
  }), h('p', {}, t(state.delivery.accepted ? 'verificationAccepted' : 'verificationOpen')), action('reviewPm', 'review-pm-summary', () => go('overview'), state.delivery.pr_open ? 'primary' : 'secondary')), 'results-inspector');
}
function renderWork() {
  const unknown = state.disconnectedDuringStart,
    running = state.workState === 'running' && !unknown,
    waiting = state.workState === 'waiting_input' && !unknown,
    ended = state.workState === 'turn_ended' && !unknown,
    results = state.workState === 'awaiting_verification' && !unknown;
  const nextKey = state.delivery.deployed ? 'nextDeployed' : state.delivery.merged ? 'nextMerged' : state.delivery.accepted ? 'nextAccepted' : unknown || ['accepted', 'preparing_worktree', 'starting'].includes(state.workState) ? 'nextStarting' : waiting ? 'nextWaiting' : results ? state.delivery.pr_open ? 'nextReviewPm' : 'nextVerify' : ended ? 'nextEnded' : 'nextRunning';
  const queue = h('section', {
    class: 'proto-work-composer',
    ...tid('work-queue-box'),
    'data-second-steered': 'false'
  }, state.secondSentenceQueued ? h('div', {
    ...tid('queued-second-sentence-receipt'),
    'data-queue-policy': 'after_current',
    'data-steered': 'false'
  }, notice('queueSaved'), h('p', {}, state.queuedMessage), details(h('code', {}, 'msg_q_02'))) : null, h('textarea', {
    rows: 2,
    'aria-label': t('sendNext'),
    placeholder: t('nextPlaceholder'),
    ...tid('next-step-textarea'),
    oninput: e => {
      state.nextStepDraft = e.target.value;
    }
  }, state.nextStepDraft), state.queueFailed ? notice('queueFailed', 'queue-failed') : null, h('div', {
    class: 'proto-queue-row'
  }, h('small', {
    ...tid('next-step-hint')
  }, t(running ? 'queueHint' : waiting ? 'waitingHint' : 'nextReadyHint')), h('div', {
    class: 'proto-actions'
  }, running ? action('interrupt', 'interrupt-work', () => {
    state.turnInterrupted = true;
    renderApp();
  }, 'danger') : null, action('sendNext', 'btn-queue-second-sentence', queueSecond, 'primary', {
    disabled: state.secondSentenceQueued
  }))), state.turnInterrupted ? notice('interrupted', 'interruption-feedback') : null);
  return h('div', {
    class: 'proto-view proto-session-view',
    ...tid('view-work-main'),
    'data-question-answered': String(state.questionAnswered)
  }, header(workTitle(), results ? [inspectorToggle('results')] : [], pill(t(unknown ? 'disconnected' : deliveryScene()), unknown ? 'warn' : 'info')), h('section', {
    class: 'proto-work-header',
    ...tid('work-area-header')
  }, facts([['goal', workCopy('goalValue')], ['controller', t('controllerPerson')], ['host', C.hostId], ['runtime', `${t('profile')} · ${t('model')}`], ['nextStep', t(nextKey)]])), h('div', {
    class: `proto-session-grid ${results ? 'has-results' : ''}`
  }, h('div', {
    class: 'proto-work-main',
    ...tid('work-area-main')
  }, h('div', {
    class: 'session-workspace proto-work-transcript'
  }, h('div', {
    class: 'conversation-scroll'
  }, !unknown && state.deliveredMessages.length ? message('mei.lin', C.firstSentence[locale]) : null, !unknown ? state.deliveredMessages.map(body => message('mei.lin', body, false, tid('delivered-next-step'))) : null, unknown ? h('div', {
    class: 'proto-diagnostic',
    ...tid('disconnect-diagnostic')
  }, h('strong', {}, t('disconnected')), h('p', {}, t('disconnectKnown')), h('p', {}, t('disconnectCheck')), h('p', {}, t('draftKept')), action('reconnect', 'reconnect-reconcile', () => {
    state.disconnectedDuringStart = false;
    state.reconciledAfterDisconnect = true;
    state.workState = 'running';
    renderApp();
  }, 'primary')) : null, state.reconciledAfterDisconnect ? notice('reconciled', 'reconciled-notice', 'ok') : null, !unknown && ['accepted', 'preparing_worktree', 'starting'].includes(state.workState) ? notice({
    accepted: 'requestAccepted',
    preparing_worktree: 'preparing',
    starting: 'startingText'
  }[state.workState], 'work-pre-output') : null, running ? h('div', tid('live-stream-output'), !state.deliveredMessages.length ? message('mei.lin', state.activeView === 'work_main' ? C.firstSentence[locale] : workCopy('request')) : null, message('Claude', workCopy('liveReply'), true), h('h3', {}, t('tools')), h('ul', {
    class: 'proto-tools'
  }, h('li', {
    'data-tool-state': 'done'
  }, icon('check'), workCopy('toolRead'), statusPill('done')), h('li', {
    'data-tool-state': 'running'
  }, icon('wrench'), workCopy('toolEdit'), statusPill('running'))), h('h3', {}, t('liveOutput')), h('pre', tid('stream-console'), workCopy('stream1'))) : null, waiting ? h('div', {
    class: 'proto-question',
    ...tid('work-question-card'),
    'data-question-answered': 'false'
  }, h('h2', {}, t('questionTitle')), h('p', {}, t('question')), action('keepPassword', 'answer-question-keep', () => {
    state.questionAnswered = true;
    setWorkState('awaiting_verification');
  }, 'primary')) : null, ended || results ? h('div', tid('turn-summary'), message('Claude', t('turnSummary'), true)) : null, state.activeView === 'work_derived' ? notice('derivedStarted', 'manual-derived-notice', 'ok') : state.activeView === 'work_separate' ? notice('separateStarted', 'hermes-separate-notice', 'ok') : null)), running || waiting || ended || results ? queue : null, advanced()), results ? resultsPanel() : null));
}
function pmSummary() {
  const suffix = state.pmAssessment === 'passed' ? 'Passed' : state.pmAssessment === 'failed' ? 'Failed' : 'Insufficient';
  return sectionCard({
    title: t('pmSummary'),
    actions: [pill(t(state.pmAssessment), state.pmAssessment === 'passed' ? 'ok' : 'warn', tid('pm-assessment-pill'))],
    body: h('div', tid('pm-assessment-card'), h('h3', {}, t('judgment')), h('p', tid('pm-current-judgment'), t('judgment' + suffix)), h('h3', {}, t('recommendation')), h('p', tid('pm-recommendation'), t(state.delivery.deployed ? 'recommendationDeployed' : state.delivery.merged ? 'recommendationMerged' : state.delivery.accepted ? 'recommendationAccepted' : 'recommendation' + suffix)), h('h3', {}, t('sources')), h('ul', {}, state.delivery.self_reported ? h('li', {}, t('sourceWork')) : null, state.delivery.tests_passed ? h('li', {}, t('sourceTests')) : null, h('li', {}, t(state.delivery.merged ? 'sourceMerged' : state.delivery.pr_open ? 'sourcePr' : 'sourceMissing')), state.delivery.deployed ? h('li', {}, t('sourceDeployed')) : null), state.ownerDecisionRecorded ? h('div', {}, notice(state.ownerDecisionRecorded, 'pm-owner-decision-banner', 'ok'), action('returnThread', 'overview-open-discussion', () => go('discussions'))) : h('div', {
      class: 'proto-actions',
      ...tid('pm-owner-decisions')
    }, action('accept', 'decision-accept-overview', () => {
      state.delivery.accepted = true;
      state.ownerDecisionRecorded = 'decisionAccepted';
      renderApp();
    }, 'primary', {
      disabled: state.pmAssessment !== 'passed' || !state.delivery.pr_open
    }), action('followup', 'decision-keep-review', () => {
      state.ownerDecisionRecorded = 'decisionFollowup';
      renderApp();
    })))
  });
}
function renderOverview() {
  return h('div', {
    class: 'proto-view',
    ...tid('view-overview')
  }, header(t('ui_overview'), [action('ui_discuss', 'overview-discuss', () => go('discussions'))], pill(t('ui_phase_development'))), h('div', {
    class: 'proto-status-strip'
  }, ...[['goal', 'projectGoal'], ['progress', state.delivery.accepted ? 'progressAccepted' : 'progressValue'], ['blockers', 'blockerValue'], ['decisions', 'decisionValue']].map(([label, value]) => h('div', label === 'decisions' ? tid('overview-open-decisions-strip') : {}, h('small', {}, t(label)), h('strong', {}, t(value))))), h('div', {
    class: 'proto-overview-columns'
  }, h('div', {}, pmSummary(), sectionCard({
    title: t('attention'),
    body: h('p', {}, t('finding'))
  })), h('div', {}, sectionCard({
    title: t('milestones'),
    body: h('p', {}, t('milestone'))
  }), sectionCard({
    title: t('deliveries'),
    body: deliveryBar(state.delivery, null, false)
  }))), h('h2', {}, t('charter')), h('div', {
    class: 'proto-charter'
  }, ...[['purpose', 'projectGoal'], ['scope', 'scopeCharter'], ['milestones', 'milestone'], ['resources', 'budget'], ['risks', 'risk'], ['decisions', 'decisionValue']].map(([title, body]) => sectionCard({
    title: t(title),
    body: h('p', title === 'decisions' ? tid('overview-open-decisions-charter') : {}, t(body))
  }))));
}
function renderManual() {
  const derive = () => {
    const dialog = h('dialog', {
      class: 'proto-dialog',
      'aria-label': t('deriveTitle'),
      'aria-modal': 'true',
      ...tid('derive-confirmation')
    }, h('h2', {}, t('deriveTitle')), h('p', {}, t('deriveBasis')), h('p', {}, t('deriveExcluded')), h('div', {
      class: 'proto-actions'
    }, action('cancel', 'derive-cancel', () => {
      dialog.close();
      document.querySelector('[data-testid="manual-derive-new"]').focus();
    }), action('start', 'derive-start', () => {
      dialog.close();
      startSeparate('work_derived');
    }, 'primary')));
    document.querySelector('[data-testid="product-shell"]').append(dialog);
    dialog.showModal();
    const backdrop = h('div', {
      class: 'proto-dialog-backdrop',
      ...tid('derive-backdrop'),
      'aria-hidden': 'true'
    });
    document.body.append(backdrop);
    dialog.addEventListener('close', () => backdrop.remove());
    dialog.addEventListener('close', () => dialog.remove());
  };
  return h('div', {
    class: 'proto-view proto-session-view',
    ...tid('view-external-manual'),
    'data-session-readonly': 'true'
  }, header(t('manualTitle'), [inspectorToggle('results')], pill(t('ui_status_read_only'), 'review')), h('div', {
    class: 'proto-session-grid has-results'
  }, h('div', {
    class: 'proto-work-main'
  }, h('div', {
    class: 'session-workspace proto-work-transcript'
  }, h('div', {
    class: 'conversation-scroll'
  }, message(t('message_person'), t('manualMessage')), message('BAT', t('manualReply')))), h('p', {
    class: 'proto-readonly-reason'
  }, t('sessionReason'))), panel('results', h('div', {}, h('h3', {}, t('checkpoints')), h('p', {}, '4b91e0c'), h('div', {
    class: 'proto-actions proto-manual-actions',
    ...tid('manual-session-actions')
  }, action('viewOriginal', 'manual-view-original', () => {
    state.manualActionFeedback = 'view_original';
    renderApp();
  }), action('linkWork', 'manual-link-existing', () => {
    state.manualActionFeedback = 'linked';
    renderApp();
  }), action('derive', 'manual-derive-new', derive, 'primary')), state.manualActionFeedback ? notice(state.manualActionFeedback === 'linked' ? 'linked' : 'originalShown', state.manualActionFeedback === 'linked' ? 'manual-linked-notice' : 'manual-view-original-notice', 'ok') : null, details(h('div', {}, h('code', {}, C.manualSessionId), h('p', {}, C.manualUncommittedFiles)))))));
}
function renderHermes() {
  return h('div', {
    class: 'proto-view',
    ...tid('view-hermes-work'),
    'data-external-controller': 'Hermes'
  }, header(t('hermesTitle'), [], pill(t('running'), 'info')), sectionCard({
    title: t('ui_work'),
    body: h('div', {}, facts([['requester', 'Hermes'], ['controller', 'Hermes'], ['runtime', t('hermesRuntime')], ['attempt', t('running')]]), h('p', {}, t('hermesPolicy')), h('div', {
      class: 'proto-actions',
      ...tid('hermes-actions')
    }, action('inbox', 'hermes-send-inbox', () => {
      state.hermesActionFeedback = 'inbox';
      renderApp();
    }, 'primary'), action('separate', 'hermes-start-separate', () => {
      state.hermesActionFeedback = 'separate';
      startSeparate('work_separate');
    })), state.hermesActionFeedback === 'inbox' ? notice('inboxSent', 'hermes-inbox-feedback', 'ok') : null, details(h('pre', {}, `${C.hermesWorkId}\n${C.hermesAttemptId}\n${C.hermesControllerId}\n${C.hermesPrincipalId}\ninbox_msg_01`)))
  }));
}
function renderMaintenance() {
  return h('div', {
    class: 'proto-view',
    ...tid('view-maintenance')
  }, header(t('ui_maintenance_review'), [], pill(t('released'), 'ok')), sectionCard({
    title: t('finding'),
    body: h('div', {}, h('p', {}, t('findingBody')), facts([['scope', t('boundedFix')], ['profileLabel', t('maintProfile')], ['host', C.hostId]]), !state.maintenanceFixStarted ? action('maintStart', 'maint-start-fix', () => {
      state.maintenanceFixStarted = true;
      renderApp();
    }, 'primary') : h('div', {}, notice('maintResult', 'maint-result', 'ok'), action('maintReturn', 'maint-return-review', () => {
      state.maintenanceReviewReturned = true;
      renderApp();
    }, 'primary')), state.maintenanceReviewReturned ? notice('maintReturned', 'maint-review-returned-banner', 'ok') : null, details(h('pre', {}, `${C.maintWorkId}\n${C.maintReviewId}`)))
  }));
}
function renderCanvas() {
  const views = {
    discussions: renderDiscussions,
    work_main: renderWork,
    work_derived: renderWork,
    work_separate: renderWork,
    overview: renderOverview,
    work_manual: renderManual,
    work_hermes: renderHermes,
    maintenance: renderMaintenance,
    work_maint: renderMaintenance
  };
  if (views[state.activeView]) return views[state.activeView]();
  if (state.activeView === 'pm_settings') return h('div', {
    class: 'proto-view'
  }, header(t('ui_settings')), sectionCard({
    title: t('pmSettings'),
    body: h('div', {}, h('p', {}, t('pmReason')), h('p', {}, t('draftKept')), action('pmSettings', 'restore-pm', () => {
      state.pmAvailable = true;
      go('discussions');
    }, 'primary'))
  }));
  const key = {
    pending: 'ui_pending',
    tools: 'ui_tools',
    settings: 'ui_settings',
    projects: 'ui_all_projects'
  }[state.activeView] || 'ui_' + state.activeView;
  return h('div', {
    class: 'proto-view',
    ...tid('view-placeholder-' + state.activeView)
  }, header(t(key)), h('p', {}, t('placeholder')), action('returnThread', 'placeholder-return-discussions', () => go('discussions')));
}
function pendingButton() {
  const node = iconButton({
    icon: 'inbox',
    label: t('ui_pending'),
    onClick: () => go('pending')
  });
  node.classList.add('shell-pending');
  return node;
}
export function renderApp() {
  document.title = `BAT Dashboard · ${t("prototype")}`;
  previousPanelOpen = currentInspector?.getAttribute('aria-modal') === 'true';
  previousPanelView = currentInspector ? currentInspector.dataset.view : null;
  clearTimeout(streamTimer);
  cleanups.forEach(dispose => dispose());
  cleanups = [];
  currentInspector = null;
  document.body.classList.remove('workspace-nav-open');
  document.body.classList.add('has-workspace');
  state.navDrawerOpen = false;
  const viewLabel = projectViewIds.includes(state.activeView) ? t('ui_' + state.activeView) : t('ui_work');
  const shell = h('div', tid('product-shell'), h('header', {
    class: 'top'
  }, iconButton({
    icon: 'menu',
    label: t('ui_menu'),
    id: 'workspace-menu',
    'aria-expanded': 'false',
    ...tid('nav-drawer-toggle'),
    onClick: openDrawer
  }), h('div', {
    class: 'brand'
  }, h('span', {
    class: 'brand-mark'
  }), 'BAT Dashboard'), h('nav', {
    id: 'nav'
  }, h('button', {
    class: 'shell-crumb-workspace',
    onclick: () => go('overview')
  }, h('span', {
    class: 'shell-workspace-name'
  }, t('projectName'))), h('span', {
    class: 'shell-breadcrumb-divider'
  }, '/'), h('span', {
    class: 'shell-crumb-view'
  }, viewLabel), state.activeView.startsWith('work_') ? h('span', {
    class: 'shell-crumb-view'
  }, '/ ' + t('ui_session')) : null, h('button', {
    class: 'shell-mobile-name',
    onclick: openDrawer
  }, t('projectName'))), h('div', {
    class: 'shell-top-actions'
  }, pendingButton(), h('span', {
    class: `live ${state.disconnectedDuringStart ? 'down' : 'ok'}`,
    title: C.serverId
  }, t(state.disconnectedDuringStart ? 'ui_offline' : 'ui_connected')))), h('div', {
    id: 'workspace'
  }, sidebar(), h('div', {
    class: 'shell-scrim',
    hidden: true,
    onclick: closeDrawer
  }), h('main', {
    id: 'main',
    tabindex: -1
  }, renderCanvas())));
  place(document.getElementById('app'), 'replaceChildren', renderBar(), shell);
  if (state.activeView === 'discussions' && state.planVisible) {
    const transcript = document.querySelector('.proto-transcript');
    const steps = transcript.querySelector('[data-testid=compound-step-list]')?.closest('.ui-section-card');
    transcript.scrollTop = steps ? steps.getBoundingClientRect().top - transcript.getBoundingClientRect().top + transcript.scrollTop : transcript.scrollHeight;
  }
  if (state.activeView === 'discussions' && state.ownerDecisionRecorded && matchMedia('(max-width: 799px)').matches) {
    queueMicrotask(() => document.querySelector('[data-testid=discussion-decision-banner]')?.scrollIntoView({block: 'center'}));
  }
  if (state.activeView === 'discussions' && !state.planVisible && state.discussionNotes.length) {
    const transcript = document.querySelector('.proto-transcript');
    transcript.scrollTop = transcript.scrollHeight;
  }
  // Fixed-clock, bounded output simulation. No network or runtime calls.
  if (workViews.includes(state.activeView) && state.workState === 'running' && !state.disconnectedDuringStart) streamTimer = setTimeout(() => {
    const el = document.querySelector('[data-testid="stream-console"]');
    if (el) el.textContent += '\n' + workCopy('stream2');
  }, 150);
}
document.addEventListener('keydown', event => {
  if (!state.navDrawerOpen) return;
  if (event.key === 'Escape' && !event.isComposing) {
    event.preventDefault();
    closeDrawer();
  } else trapFocus(document.getElementById('workspace-sidebar'), event);
});
if (['requirement_accepted', 'merged', 'deployed'].includes(queryScene)) switchScene(queryScene);
else renderApp();
