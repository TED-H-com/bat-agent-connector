import {button, disclosure, statusPill} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
import {sessionHref} from './session-navigation.js';
import {createWorkPublish, publicationDeliveryLine, publicationPrLine} from './pm-work-publish.js';
import {createWorkQuality} from './pm-work-quality.js';
import {resultRequirements} from './pm-work-result-requirements.js';

const KNOWN_STATES = new Set([
  'accepted',
  'starting',
  'active',
  'waiting',
  'turn-ended',
  'process-ended',
  'failed',
  'unknown',
]);

const KNOWN_REASONS = new Set([
  'bat_unreachable',
  'observation_stale',
  'session_not_found',
  'transcript_truncated_by_size',
  'archived_messages_omitted',
  'parse_error',
  'session_not_started_yet',
  'start_outcome_uncertain',
  'handoff_outcome_uncertain',
  'unloaded_history_session',
  'turn_marker_not_visible',
  'turn_boundary_not_collected',
  'session_not_managed_by_attempt',
  'turn_slice_bounded',
  'result_document_bounded',
  'worktree_unreachable',
  'bat_empty_status_not_clean_proof',
  'diff_exceeded_pipe_limit',
  'commit_limit_exceeded',
  'changeset_time_budget_exceeded',
  'base_commit_not_ancestor',
  'source_digest_conflict',
]);

function normalizeForComparison(envelope) {
  if (!envelope || typeof envelope !== 'object') return null;
  const clone = JSON.parse(JSON.stringify(envelope));
  delete clone.cursor;
  delete clone.events_read;
  delete clone.wait;
  if (clone.collection) {
    delete clone.collection.checked_at;
  }
  return clone;
}

export function workResultPanel({h, t, api, alive, workspaceId, caps, errorBox, onVisibility = () => {}, form = null, namespace = ''}) {
  let activeAttempt = null;
  let readSerial = 0;
  let lastEnvelope = null;
  let lastNormalized = null;
  let lastStale = false;
  let lastError = null;

  // Track disclosure open states across re-renders
  let turnsOpen = false;
  let detailsOpen = false;
  let filesOpen = false;

  // Part B state: current changes and diffs
  let currentChanges = null;
  let currentChangesError = null;
  let changesBusy = false;
  let isOutOfDate = false;
  let changesSerial = 0;
  let diffGeneration = 0;
  const diffKeySerials = new Map();
  const diffCache = new Map();
  const diffErrors = new Map();
  const diffLoading = new Set();
  const openDiffs = new Set();

  const getCaps = () => (typeof caps === 'function' ? caps() : caps);
  const canChanges = () => Boolean(getCaps()?.features?.pm_workspaces?.work_attempt_results?.routes?.changes);
  const canDiff = () => Boolean(getCaps()?.features?.pm_workspaces?.work_attempt_results?.routes?.diff);

  const panelElement = h('div', {class: 'pm-work-result-panel', 'data-work-result': ''});
  panelElement.hidden = true;
  const publish = createWorkPublish({h, t, form, namespace, caps, errorBox});
  const quality = createWorkQuality({h,t,api,alive,workspaceId,caps,form,errorBox,namespace,refreshResult:readResult});

  function updateVisibility(visible) {
    panelElement.hidden = !visible;
    onVisibility(visible);
  }

  function withdraw() {
    readSerial++;
    changesSerial++;
    diffGeneration++;
    diffKeySerials.clear();
    lastEnvelope = null;
    lastNormalized = null;
    lastStale = false;
    lastError = null;
    currentChanges = null;
    currentChangesError = null;
    changesBusy = false;
    isOutOfDate = false;
    diffCache.clear();
    diffErrors.clear();
    diffLoading.clear();
    openDiffs.clear();
    turnsOpen = false;
    detailsOpen = false;
    filesOpen = false;
    publish.dispose();
    quality.dispose();
    panelElement.replaceChildren();
    updateVisibility(false);
  }

  function dispose() {
    activeAttempt = null;
    withdraw();
  }

  async function setAttempt(attempt) {
    if (!attempt || !attempt.attempt_id) {
      activeAttempt = null;
      withdraw();
      return;
    }

    const hasSession = Boolean(attempt.session?.session_id || attempt.native_session?.session_id || attempt.session_id);
    if (!hasSession) {
      activeAttempt = attempt;
      withdraw();
      return;
    }

    if (activeAttempt?.attempt_id !== attempt.attempt_id) {
      withdraw();
    }

    activeAttempt = attempt;
    await readResult();
  }

  async function refresh() {
    if (!activeAttempt) return;
    const hasSession = Boolean(activeAttempt.session?.session_id || activeAttempt.native_session?.session_id || activeAttempt.session_id);
    if (!hasSession) return;
    await readResult();
  }

  async function readResult() {
    if (!activeAttempt) return;
    const ticket = ++readSerial;
    const attemptId = activeAttempt.attempt_id;
    const currentAttempt = activeAttempt;

    try {
      const data = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/work-attempts/${encodeURIComponent(attemptId)}/result`);
      try { alive?.(); } catch { return; }
      if (ticket !== readSerial || activeAttempt?.attempt_id !== attemptId) {
        return; // Drop late reply
      }

      // Validate envelope contract and matching IDs
      const isValid =
        data &&
        typeof data === 'object' &&
        data.contract === 'pm-work-attempt-result/v1' &&
        data.workspace_id === workspaceId &&
        data.attempt_id === attemptId &&
        data.execution &&
        KNOWN_STATES.has(data.execution.state);

      if (!isValid) {
        lastEnvelope = null;
        lastNormalized = null;
        lastStale = false;
        lastError = null;
        renderInvalid();
        return;
      }

      const normalized = normalizeForComparison(data);
      const isUnchanged = lastNormalized && JSON.stringify(lastNormalized) === JSON.stringify(normalized);

      if (!isUnchanged || lastStale) {
        if (!isUnchanged && currentChanges) {
          isOutOfDate = true;
        }
        lastEnvelope = data;
        lastNormalized = normalized;
        lastStale = false;
        lastError = null;
        renderEnvelope(data, currentAttempt);
      } else {
        publish.recheck();
        quality.recheck();
      }
    } catch (err) {
      try { alive?.(); } catch { return; }
      if (ticket !== readSerial || activeAttempt?.attempt_id !== attemptId) {
        return;
      }

      const status = err?.status;
      if (status === 401 || status === 403 || status === 404) {
        // Withdraw content and hide section
        withdraw();
        return;
      }

      // Other errors: keep last good content under stale pill, show errorBox with Try again
      lastStale = true;
      lastError = err;
      renderError(err, currentAttempt);
    }
  }

  function renderInvalid() {
    publish.dispose();
    quality.dispose();
    panelElement.replaceChildren(
      h('h3', {}, t('wres_heading')),
      h('p', {class: 'pm-work-result-invalid'}, t('wres_invalid_envelope')),
    );
    updateVisibility(true);
  }

  function renderError(err, attempt) {
    quality.dispose();
    const heading = h('h3', {}, t('wres_heading'));
    const retryBtn = button({
      label: t('wres_try_again'),
      class: 'ui-button secondary ui-sm pm-work-result-retry',
      onClick: () => { void readResult(); },
    });
    retryBtn.setAttribute('data-work-result-retry', 'result');
    const errBoxNode = errorBox ? errorBox(err, {within: panelElement}) : h('div', {class: 'error'}, err?.message || String(err));
    const errorContainer = h('div', {class: 'pm-work-result-error-box'}, errBoxNode, retryBtn);

    if (lastEnvelope) {
      renderEnvelope(lastEnvelope, attempt, errorContainer);
    } else {
      panelElement.replaceChildren(heading, errorContainer);
      updateVisibility(true);
    }
  }

  function formatCount(val) {
    if (val === 'unknown' || val == null) return t('wres_unknown');
    return String(val);
  }

  // Match attribute values in script. Interpolating a file path into a selector
  // throws when the path contains a quote or a backslash, and that throw stops
  // the render and the focus restore.
  function elementByAttr(root, name, value) {
    if (!root || value == null) return null;
    for (const node of root.querySelectorAll(`[${name}]`)) {
      if (node.getAttribute(name) === value) return node;
    }
    return null;
  }

  function saveFocus() {
    const active = document.activeElement;
    if (active && panelElement.contains(active)) {
      if (active.closest('[data-work-quality]')) {
        return {type: 'quality-field', node: active, start: active.selectionStart, end: active.selectionEnd};
      }
      if (active.dataset?.workResultFocus) {
        return {type: 'attr', value: active.dataset.workResultFocus};
      }
      if (active.dataset?.workResultDiffSummary) {
        return {type: 'diff-summary', key: active.dataset.workResultDiffSummary};
      }
      if (active.dataset?.workResultDiffRegion) {
        return {type: 'diff-region', key: active.dataset.workResultDiffRegion};
      }
      if (active.getAttribute('data-work-result-retry') === 'result') {
        return {type: 'retry-result'};
      }
      if (active.getAttribute('data-work-result-retry') === 'changes') {
        return {type: 'retry-changes'};
      }
      if (active.getAttribute('data-work-result-retry-diff')) {
        return {type: 'retry-diff', key: active.getAttribute('data-work-result-retry-diff')};
      }
      if (active.classList.contains('pm-work-result-read-changes')) {
        return {type: 'read-changes'};
      }
      if (active.classList.contains('pm-work-result-read-again')) {
        return {type: 'read-again'};
      }
      if (active.matches?.('[data-work-publish-title], [data-work-publish-body]')) {
        return {
          type: 'publish-field',
          hook: active.hasAttribute('data-work-publish-title') ? 'title' : 'body',
          start: active.selectionStart,
          end: active.selectionEnd,
        };
      }
      if (active.matches?.('[data-work-publish-draft]')) {
        return {type: 'publish-draft', value: active.getAttribute('data-work-publish-draft')};
      }
      if (active.matches?.('[data-work-publish-submit]')) return {type: 'publish-submit'};
      if (active.tagName === 'SUMMARY' && active.parentElement?.tagName === 'DETAILS') {
        const details = active.parentElement;
        if (details.dataset.workResultTurns !== undefined) return {type: 'turns'};
        if (details.dataset.workResultDetails !== undefined) return {type: 'details'};
        if (details.classList.contains('pm-work-result-files')) return {type: 'files'};
      }
    }
    return null;
  }

  function restoreFocus(token) {
    if (!token) return;
    if (token.type === 'quality-field') {
      if (panelElement.contains(token.node) && !token.node.disabled) {
        token.node.focus({preventScroll: true});
        if (typeof token.start === 'number') token.node.setSelectionRange?.(token.start, token.end);
      }
    } else if (token.type === 'attr') {
      elementByAttr(panelElement, 'data-work-result-focus', token.value)?.focus();
    } else if (token.type === 'diff-summary') {
      elementByAttr(panelElement, 'data-work-result-diff-summary', token.key)?.focus();
    } else if (token.type === 'diff-region') {
      elementByAttr(panelElement, 'data-work-result-diff-region', token.key)?.focus();
    } else if (token.type === 'read-changes') {
      const target = panelElement.querySelector('.pm-work-result-read-changes') || panelElement.querySelector('[data-work-result-retry="changes"]');
      target?.focus();
    } else if (token.type === 'read-again') {
      const target = panelElement.querySelector('.pm-work-result-read-again') || panelElement.querySelector('.pm-work-result-read-changes') || panelElement.querySelector('[data-work-result-retry="changes"]');
      target?.focus();
    } else if (token.type === 'retry-changes') {
      const target = panelElement.querySelector('[data-work-result-retry="changes"]') || panelElement.querySelector('.pm-work-result-read-changes');
      target?.focus();
    } else if (token.type === 'retry-diff') {
      const target = elementByAttr(panelElement, 'data-work-result-retry-diff', token.key) || elementByAttr(panelElement, 'data-work-result-diff-summary', token.key);
      target?.focus();
    } else if (token.type === 'retry-result') {
      elementByAttr(panelElement, 'data-work-result-retry', 'result')?.focus();
    } else if (token.type === 'turns') {
      panelElement.querySelector('[data-work-result-turns] > summary')?.focus();
    } else if (token.type === 'details') {
      panelElement.querySelector('[data-work-result-details] > summary')?.focus();
    } else if (token.type === 'files') {
      panelElement.querySelector('.pm-work-result-files > summary')?.focus();
    } else if (token.type === 'publish-field') {
      const node = panelElement.querySelector(token.hook === 'title' ? '[data-work-publish-title]' : '[data-work-publish-body]');
      if (node) {
        node.focus();
        try { node.setSelectionRange(token.start ?? 0, token.end ?? token.start ?? 0); } catch { /* the control cannot take a caret */ }
      } else publishFocusFallback();
    } else if (token.type === 'publish-draft') {
      const node = elementByAttr(panelElement, 'data-work-publish-draft', token.value);
      if (node) node.focus();
      else publishFocusFallback();
    } else if (token.type === 'publish-submit') {
      const node = panelElement.querySelector('[data-work-publish-submit]');
      if (node) node.focus();
      else publishFocusFallback();
    }
  }

  function publishFocusFallback() {
    const link = panelElement.querySelector('[data-work-publish-pr]');
    const note = panelElement.querySelector('[data-work-publish-blocked-reason], [data-work-publish-gap], [data-work-publish-held]');
    const target = link || note;
    if (!target || target.hidden) return;
    if (!link) target.tabIndex = -1;
    target.focus();
  }

  function renderEnvelope(envelope, attempt, errorBoxNode = null) {
    const focusToken = saveFocus();
    const requirementOpen = new Set(Array.from(panelElement.querySelectorAll('[data-result-requirement-details][open]'),
      node => node.getAttribute('data-result-requirement-details')));

    // Preserve disclosure open states from DOM if present
    const existingTurns = panelElement.querySelector('[data-work-result-turns]');
    if (existingTurns) turnsOpen = existingTurns.open;
    const existingDetails = panelElement.querySelector('[data-work-result-details]');
    if (existingDetails) detailsOpen = existingDetails.open;
    const existingFiles = panelElement.querySelector('.pm-work-result-files');
    if (existingFiles) filesOpen = existingFiles.open;
    for (const dt of panelElement.querySelectorAll('.pm-work-result-diff-details[open]')) {
      const sum = dt.querySelector('[data-work-result-diff-summary]');
      if (sum?.dataset.workResultDiffSummary) {
        openDiffs.add(sum.dataset.workResultDiffSummary);
      }
    }
    for (const dt of panelElement.querySelectorAll('.pm-work-result-diff-details:not([open])')) {
      const sum = dt.querySelector('[data-work-result-diff-summary]');
      if (sum?.dataset.workResultDiffSummary) {
        openDiffs.delete(sum.dataset.workResultDiffSummary);
      }
    }

    const sections = [];
    const panelReasons = new Set();
    const unlistedFacets = [];
    const unlistedFileStatuses = [];

    // Header
    const heading = h('h3', {}, t('wres_heading'));
    sections.push(heading);

    // Error box if rendering under error state
    if (errorBoxNode) {
      const stalePill = h('span', {class: 'ui-pill tone-warn', 'data-status': 'stale'}, t('ui_status_stale'));
      sections.push(h('div', {class: 'pm-work-result-stale-banner'}, stalePill, errorBoxNode));
    }

    const exec = envelope.execution || {};

    // 1. State line
    const stateKey = `wres_state_${exec.state?.replace(/-/g, '_')}`;
    const stateLabel = t(stateKey) !== stateKey ? t(stateKey) : t('wres_unknown');
    const stateP = h('p', {class: 'pm-work-result-state'});
    stateP.append(h('strong', {}, stateLabel));
    if (exec.observed_at) {
      stateP.append(' · ', timeStamp(exec.observed_at));
    }
    sections.push(stateP);

    // State reason or attention
    if (exec.state_reason) {
      const reason = exec.state_reason;
      panelReasons.add(reason);
      if (reason === 'agent_error') {
        sections.push(h('p', {class: 'muted pm-work-result-reason'}, t('wres_agent_error')));
      } else if (KNOWN_REASONS.has(reason)) {
        const textKey = `wres_reason_${reason}`;
        if (t(textKey) !== textKey) {
          sections.push(h('p', {class: 'muted pm-work-result-reason'}, t(textKey)));
        }
      } else if (exec.state === 'failed') {
        const wstKey = `wst_error_${reason}`;
        const errKey = `error_code_${reason}`;
        let failText = '';
        if (t(wstKey) !== wstKey) {
          failText = t(wstKey);
        } else if (t(errKey) !== errKey) {
          failText = t(errKey);
        }
        if (failText) {
          sections.push(h('p', {class: 'muted pm-work-result-reason'}, failText));
        }
      }
    } else if (exec.state === 'waiting' && exec.pending != null) {
      sections.push(h('p', {class: 'muted pm-work-result-reason'}, t('wres_waiting_pending')));
    }

    if (exec.attention) {
      let attnText = t('wres_status_needs_attention');
      if (exec.attention.message) {
        attnText += ` ${exec.attention.message}`;
      }
      sections.push(h('p', {class: 'muted pm-work-result-attention'}, attnText));
    }

    // Links: "Open live output" and "View operation"
    const linkItems = [];
    const sess = attempt?.session || attempt?.native_session;
    if (sess?.session_id && sess?.host) {
      const liveHref = sessionHref({host: sess.host, sessionId: sess.session_id, from: 'board', workspaceId,
        workItemId: envelope.identity?.work_item_id, attemptId: attempt.attempt_id});
      linkItems.push(h('a', {href: liveHref, 'data-work-result-focus': 'live-output'}, t('wres_open_live_output')));
    }
    const opHash = envelope.links?.start_operation_hash || (envelope.identity?.start_operation_id ? `#/op/${envelope.identity.start_operation_id}` : null);
    if (opHash) {
      linkItems.push(h('a', {href: opHash, 'data-work-result-focus': 'operation'}, t('wres_view_operation')));
    }
    if (linkItems.length > 0) {
      const linksContainer = h('div', {class: 'pm-work-result-links'});
      for (let i = 0; i < linkItems.length; i++) {
        if (i > 0) linksContainer.append(' · ');
        linksContainer.append(linkItems[i]);
      }
      sections.push(linksContainer);
    }

    // 2. The agent's own summary
    const summaryHeader = h('div', {class: 'pm-work-result-summary-header'});
    summaryHeader.append(h('h4', {}, t('wres_agent_summary_label')));

    const selfReported = exec.state_facets?.self_reported;
    if (selfReported === 'claimed_done') {
      const pill = h('span', {class: 'ui-pill tone-neutral', 'data-work-result-pill': 'claimed_done'}, t('wres_pill_claimed_done'));
      summaryHeader.append(' ', pill);
    }
    sections.push(summaryHeader);

    const summaryText = envelope.conversation?.latest_assistant_summary?.text;
    if (summaryText && typeof summaryText === 'string') {
      const summaryBox = h('div', {
        role: 'region',
        tabindex: '0',
        'aria-label': t('wres_agent_summary_label'),
        class: 'pm-work-result-summary-box',
        'data-work-result-focus': 'summary-box',
      });
      summaryBox.textContent = summaryText; // Plain text only! Strict CSP
      sections.push(summaryBox);
    } else {
      sections.push(h('p', {class: 'muted pm-work-result-empty-summary'}, t('wres_no_recorded_turn')));
    }

    // 3. Checks and delivery definition list
    const dl = h('dl', {class: 'pm-work-result-checks'});

    // (a) Verification
    const verifStatus = exec.state_facets?.verification || envelope.verification?.status;
    let verifText = t('wres_unknown');
    if (verifStatus && ['unverified', 'running', 'verified', 'failed', 'stale', 'unknown'].includes(verifStatus)) {
      verifText = t(`wres_verification_${verifStatus}`);
    } else if (verifStatus) {
      unlistedFacets.push(`verification: ${verifStatus}`);
    }
    dl.append(h('dt', {}, t('wres_check_verification')), h('dd', {'data-check': 'verification'}, verifText));

    // (b) Review
    const reviewDecision = exec.state_facets?.review_decision || envelope.verification?.quality_reviews?.current?.decision;
    let reviewText = t('wres_unknown');
    if (typeof reviewDecision === 'string') {
      if (reviewDecision === 'unassessed') reviewText = t('wres_review_unassessed');
      else if (reviewDecision === 'revocation') reviewText = t('wres_review_revocation');
      else unlistedFacets.push(`review_decision: ${reviewDecision}`);
    } else if (reviewDecision && typeof reviewDecision === 'object') {
      if (reviewDecision.kind === 'unassessed') {
        reviewText = t('wres_review_unassessed');
      } else if (reviewDecision.kind === 'revocation') {
        reviewText = t('wres_review_revocation');
      } else if (reviewDecision.kind === 'assessment' || reviewDecision.acceptance) {
        if (reviewDecision.acceptance === 'accepted') reviewText = t('wres_review_accepted');
        else if (reviewDecision.acceptance === 'rejected') reviewText = t('wres_review_rejected');
        else if (reviewDecision.acceptance === 'unassessed') reviewText = t('wres_review_unassessed');
        else if (reviewDecision.acceptance) unlistedFacets.push(`review_acceptance: ${reviewDecision.acceptance}`);
      } else if (reviewDecision.kind) {
        unlistedFacets.push(`review_kind: ${reviewDecision.kind}`);
      }
    }
    dl.append(h('dt', {}, t('wres_check_review')), h('dd', {'data-check': 'review'}, reviewText));

    // (c) Requirement acceptance
    const acceptObj = exec.state_facets?.acceptance;
    const acceptStatus = acceptObj?.status;
    let acceptText = t('wres_unknown');
    if (acceptStatus === 'passed') acceptText = t('wres_acceptance_passed');
    else if (acceptStatus === 'blocked') acceptText = t('wres_acceptance_blocked');
    else if (acceptStatus === 'unassessed') acceptText = t('wres_acceptance_unassessed');
    else if (acceptStatus) unlistedFacets.push(`acceptance_status: ${acceptStatus}`);
    dl.append(h('dt', {}, t('wres_check_acceptance')), h('dd', {'data-check': 'acceptance'}, acceptText));

    // (d) Pull request
    const mergeStatus = exec.state_facets?.merge;
    let mergeText = t('wres_unknown');
    if (['not_started', 'not_merged', 'pr_open', 'pr_closed_unmerged', 'merge_pending', 'merged'].includes(mergeStatus)) {
      mergeText = t(`wres_merge_${mergeStatus}`);
    } else if (mergeStatus) {
      unlistedFacets.push(`merge: ${mergeStatus}`);
    }
    const prDd = h('dd', {'data-check': 'pr'});
    prDd.append(h('span', {class: 'pm-work-result-pr-status'}, mergeText));

    const prs = envelope.delivery?.pull_requests || [];
    if (prs.length > 0) {
      const prList = h('ul', {class: 'pm-work-result-pr-list'});
      for (const pr of prs) {
        const line = publicationPrLine(h, t, pr);
        if (line) prList.append(line);
      }
      if (prList.childElementCount) prDd.append(prList);
      for (const pr of prs) {
        const delivery = publicationDeliveryLine(h, t, pr, envelope, attempt);
        if (delivery) prDd.append(delivery);
      }
    }
    if (envelope.completeness?.coverage?.delivery === 'partial') {
      prDd.append(h('p', {'data-work-publish-partial': ''}, t('wpub_pr_partial')));
    }
    dl.append(h('dt', {}, t('wres_check_pr')), prDd);

    // (e) Deployment
    const deployObj = exec.state_facets?.deployment;
    const deployState = deployObj?.state;
    const deployVerified = deployObj?.verified === true;
    let deployText = t('wres_unknown');
    if (['not_deployed', 'pending', 'running', 'failed', 'needs_attention', 'cancelled'].includes(deployState)) {
      deployText = t(`wres_deploy_${deployState}`);
    } else if (deployState === 'succeeded') {
      deployText = deployVerified ? t('wres_deploy_succeeded_verified') : t('wres_deploy_succeeded');
    } else if (deployState) {
      unlistedFacets.push(`deployment_state: ${deployState}`);
    }
    dl.append(h('dt', {}, t('wres_check_deployment')), h('dd', {'data-check': 'deployment'}, deployText));

    // (f) Delivery goal
    const goal = envelope.delivery?.delivery_goal;
    let goalText = t('wres_unknown');
    if (goal === 'pull_request') goalText = t('wres_goal_pr');
    else if (goal === 'reviewable_changes') goalText = t('wres_goal_reviewable_changes');
    else if (goal && goal !== 'unknown') unlistedFacets.push(`delivery_goal: ${goal}`);
    dl.append(h('dt', {}, t('wres_check_delivery_goal')), h('dd', {'data-check': 'delivery-goal'}, goalText));

    sections.push(dl);
    const requirements = resultRequirements({h, t, section: envelope.requirements, open: requirementOpen});
    if (requirements) sections.push(requirements);

    // 4. Changes
    const changesBox = h('div', {class: 'pm-work-result-changes'});
    const ch = envelope.changes || {};
    const summary = ch.summary || {};

    const isComplete = summary.complete === true && (!summary.unavailable || summary.unavailable.length === 0);
    const filesCountNum = Number.isSafeInteger(summary.file_count)
      ? summary.file_count
      : (Array.isArray(summary.files) ? summary.files.length : null);
    const commitsCountNum = Number.isSafeInteger(summary.commit_count)
      ? summary.commit_count
      : (Array.isArray(summary.commits) ? summary.commits.length : null);

    if (isComplete) {
      let summaryKey = 'wres_changes_summary';
      if (filesCountNum === 1 && commitsCountNum === 1) summaryKey = 'wres_changes_summary_one';
      else if (filesCountNum === 1) summaryKey = 'wres_changes_summary_one_file';
      else if (commitsCountNum === 1) summaryKey = 'wres_changes_summary_one_commit';

      const summaryText = t(summaryKey, {
        files: formatCount(filesCountNum),
        commits: formatCount(commitsCountNum),
        additions: formatCount(summary.additions),
        deletions: formatCount(summary.deletions),
      });
      changesBox.append(h('p', {class: 'pm-work-result-changes-summary'},
        summaryText, ' · ', t('wres_as_of_last_turn')));
    } else {
      const partlyText = h('p', {class: 'pm-work-result-changes-summary'},
        t('wres_changes_partly_read'), ' · ', t('wres_as_of_last_turn'));
      changesBox.append(partlyText);

      const changeReasons = [];
      if (summary.reason && !panelReasons.has(summary.reason)) {
        changeReasons.push(summary.reason);
        panelReasons.add(summary.reason);
      }
      if (Array.isArray(summary.reasons)) {
        for (const r of summary.reasons) {
          if (!changeReasons.includes(r) && !panelReasons.has(r)) {
            changeReasons.push(r);
            panelReasons.add(r);
          }
        }
      }
      if (changeReasons.length > 0) {
        const reasonsList = h('ul', {class: 'pm-work-result-changes-reasons'});
        for (const code of changeReasons) {
          const key = `wres_reason_${code}`;
          let text = t(key);
          if (!text || text === key) {
            text = t('wres_reason_generic_limit');
          }
          reasonsList.append(h('li', {}, text));
        }
        changesBox.append(reasonsList);
      }
    }

    // Current changes (Part B)
    if (canChanges()) {
      changesBox.append(renderCurrentChangesBlock());
    }

    // File list
    const files = summary.files || [];
    if (files.length > 0) {
      const renderFileRow = f => {
        let statusLabel = t('wres_unknown');
        if (f.status === 'M' || f.status === 'modified') statusLabel = t('wres_file_modified');
        else if (f.status === 'A' || f.status === 'added') statusLabel = t('wres_file_added');
        else if (f.status === 'D' || f.status === 'deleted') statusLabel = t('wres_file_deleted');
        else if (typeof f.status === 'string' && (f.status.startsWith('R') || f.status === 'renamed')) statusLabel = t('wres_file_renamed');
        else if (f.status) unlistedFileStatuses.push(`${f.path}: ${f.status}`);

        const adds = f.additions === 'unknown' || f.additions == null ? '?' : `+${f.additions}`;
        const dels = f.deletions === 'unknown' || f.deletions == null ? '?' : `−${f.deletions}`;

        return h('li', {class: 'pm-work-result-file-row'},
          h('span', {class: 'pm-work-result-file-path mono'}, f.path),
          ' ',
          h('span', {class: 'pm-work-result-file-status muted'}, statusLabel),
          ' ',
          h('span', {class: 'pm-work-result-file-diff mono'}, `${adds} ${dels}`),
        );
      };

      const topFiles = files.slice(0, 20);
      const fileList = h('ul', {class: 'pm-work-result-file-list'}, ...topFiles.map(renderFileRow));
      changesBox.append(fileList);

      if (files.length > 20) {
        const remainingFiles = files.slice(20);
        const remainingList = h('ul', {class: 'pm-work-result-file-list'}, ...remainingFiles.map(renderFileRow));
        const filesLabel = remainingFiles.length === 1 ? t('wres_show_all_files_one') : t('wres_show_all_files', {n: files.length});
        const filesDisclosure = disclosure({
          label: filesLabel,
          body: remainingList,
          open: filesOpen,
        });
        filesDisclosure.classList.add('pm-work-result-files');
        changesBox.append(filesDisclosure);
      }
    }

    // Base commit
    if (ch.base_commit) {
      changesBox.append(h('p', {class: 'pm-work-result-base-commit'},
        h('strong', {}, `${t('wres_base_commit')}: `),
        h('span', {class: 'mono'}, ch.base_commit.slice(0, 12)),
      ));
    }

    // Result commit
    const fixedCommit = ch.fixed_result_commit;
    if (fixedCommit && fixedCommit.commit_sha) {
      const srcKind = fixedCommit.source_kind;
      let srcText = '';
      if (srcKind === 'turn_end_observation') srcText = t('wres_source_turn_end_observation');
      else if (srcKind === 'execution_verification' || srcKind === 'session_verification') srcText = t('wres_source_verified_commit');
      else if (srcKind === 'integration') srcText = t('wres_source_integration_commit');

      const commitP = h('p', {class: 'pm-work-result-commit'},
        h('strong', {}, `${t('wres_result_commit')}: `),
        h('span', {class: 'mono'}, fixedCommit.commit_sha.slice(0, 12)),
      );
      if (srcText) {
        commitP.append(' · ', srcText);
      }
      changesBox.append(commitP);
    }

    // Uncommitted changes
    const uncommitted = ch.uncommitted || {};
    if (uncommitted.count === 'unknown') {
      changesBox.append(h('p', {class: 'muted pm-work-result-uncommitted'}, t('wres_uncommitted_unknown')));
    } else if (Number.isSafeInteger(uncommitted.count) && uncommitted.count > 0) {
      changesBox.append(h('p', {class: 'pm-work-result-uncommitted'},
        t('wres_uncommitted_changes', {n: uncommitted.count}),
      ));
    }

    sections.push(changesBox);

    // 5. Completeness
    const completeness = envelope.completeness;
    const completenessStatus = completeness?.status;
    if (completenessStatus && completenessStatus !== 'complete') {
      const compDiv = h('div', {class: 'pm-work-result-completeness'});
      if (completenessStatus === 'partial') {
        compDiv.append(statusPill('partial'));
      } else {
        compDiv.append(h('span', {class: 'ui-pill tone-bad', 'data-status': 'unavailable'}, t('wres_status_unavailable')));
      }

      const reasons = (completeness.reasons || []).filter(code => {
        if (panelReasons.has(code)) return false;
        panelReasons.add(code);
        return true;
      });
      if (reasons.length > 0) {
        const reasonList = h('ul', {class: 'pm-work-result-completeness-reasons'});
        for (const code of reasons) {
          const key = `wres_reason_${code}`;
          let text = t(key);
          if (!text || text === key) {
            text = t('wres_reason_generic_limit');
          }
          reasonList.append(h('li', {}, text));
        }
        compDiv.append(reasonList);
      }
      sections.push(compDiv);
    }

    const publishNode = publish.render(envelope);
    if (publishNode) sections.push(publishNode);
    if (!lastStale) {
      const qualityNode = quality.render(envelope, attempt);
      if (qualityNode) sections.push(qualityNode);
    }

    // 6. Recorded turns disclosure
    const results = envelope.results || [];
    if (results.length > 0) {
      const turnsBody = h('div', {class: 'pm-work-result-turns-body'});
      if (envelope.results_has_more) {
        turnsBody.append(h('p', {class: 'muted pm-work-result-more-turns'}, t('wres_showing_latest_20')));
      }

      const turnsList = h('ul', {class: 'pm-work-result-turns-list'});
      for (let i = 0; i < results.length; i++) {
        const turn = results[i];
        const row = h('li', {class: 'pm-work-result-turn-row'});
        if (turn.observed_at) {
          row.append(timeStamp(turn.observed_at), ' ');
        }
        const sha = turn.head_commit ? turn.head_commit.slice(0, 12) : '—';
        row.append(h('span', {class: 'mono'}, sha));

        if (i === 0) {
          row.append(' ', h('span', {class: 'ui-pill tone-neutral ui-xs', 'data-work-result-pill': 'latest_turn'}, t('wres_latest_turn')));
        }
        turnsList.append(row);
      }
      turnsBody.append(turnsList);

      const turnsLabel = results.length === 1 ? t('wres_recorded_turns_count_one') : t('wres_recorded_turns_count', {n: results.length});
      const turnsDisclosure = disclosure({
        label: turnsLabel,
        body: turnsBody,
        open: turnsOpen,
      });
      turnsDisclosure.dataset.workResultTurns = '';
      sections.push(turnsDisclosure);
    }

    // 7. Details disclosure (collapsed by default)
    const detailsBody = h('div', {class: 'pm-work-result-details-body'});
    const addDetailRow = (label, value) => {
      if (value === undefined) return;
      detailsBody.append(h('p', {},
        h('strong', {}, `${label}: `),
        h('span', {class: 'mono'}, String(value == null ? '—' : value)),
      ));
    };

    addDetailRow(t('wres_attempt_id'), envelope.attempt_id);
    addDetailRow(t('wres_result_id'), envelope.sources?.result_id);
    addDetailRow(t('wres_start_operation_id'), envelope.identity?.start_operation_id);
    addDetailRow(t('wres_execution_operation_id'), envelope.identity?.execution_operation_id);
    addDetailRow(t('wres_source_key'), envelope.sources?.source_key);
    addDetailRow(t('wres_digest'), envelope.sources?.digest ? envelope.sources.digest.slice(0, 16) : null);
    addDetailRow(t('wres_collection_outcome'), envelope.collection?.outcome);
    addDetailRow(t('wres_raw_state'), exec.state);
    addDetailRow(t('wres_raw_stage'), exec.stage);
    if (exec.state_reason) {
      addDetailRow(t('wres_raw_reasons'), exec.state_reason);
    }

    if (unlistedFacets.length > 0) {
      addDetailRow(t('wres_unlisted_facets'), unlistedFacets.join(', '));
    }
    if (unlistedFileStatuses.length > 0) {
      addDetailRow(t('wres_unlisted_file_statuses'), unlistedFileStatuses.join(', '));
    }

    // Unlisted reasons from completeness
    const unlistedReasons = (completeness?.reasons || []).filter(r => !KNOWN_REASONS.has(r));
    if (unlistedReasons.length > 0) {
      addDetailRow(t('wres_unlisted_completeness_reasons'), unlistedReasons.join(', '));
    }

    // Usage
    const usage = envelope.usage || {};
    const formatUsageVal = val => (val == null || val === 'unknown') ? t('ui_status_unknown') : String(val);
    addDetailRow(t('wres_usage_context_tokens'), formatUsageVal(usage.context_tokens));
    addDetailRow(t('wres_usage_context_window'), formatUsageVal(usage.context_window));
    addDetailRow(t('wres_usage_num_turns'), formatUsageVal(usage.num_turns));

    // Exit code: not exposed by BAT
    detailsBody.append(h('p', {class: 'muted'}, t('wres_exit_code_not_exposed')));

    const detailsDisclosure = disclosure({
      label: t('wres_details'),
      body: detailsBody,
      open: detailsOpen,
    });
    detailsDisclosure.dataset.workResultDetails = '';
    sections.push(detailsDisclosure);

    panelElement.replaceChildren(...sections);
    updateVisibility(true);

    restoreFocus(focusToken);
  }

  function sortCommitsOldestFirst(commitsTouched, allCommits) {
    if (!Array.isArray(commitsTouched)) return [];
    if (!Array.isArray(allCommits) || allCommits.length === 0) return [...commitsTouched];
    const order = new Map();
    allCommits.forEach((sha, idx) => order.set(sha, idx));
    const originalIndex = new Map();
    commitsTouched.forEach((sha, idx) => originalIndex.set(sha, idx));
    return [...commitsTouched].sort((a, b) => {
      const hasA = order.has(a);
      const hasB = order.has(b);
      if (hasA && hasB) {
        return order.get(a) - order.get(b);
      }
      if (hasA) return -1;
      if (hasB) return 1;
      return originalIndex.get(a) - originalIndex.get(b);
    });
  }

  function renderDiffContent(container, commit, path, key, summaryNode) {
    container.replaceChildren();

    if (diffLoading.has(key)) {
      container.append(h('p', {class: 'muted pm-work-result-diff-loading'}, t('wres_diff_loading')));
      return;
    }

    const err = diffErrors.get(key);
    if (err) {
      if (err.status === 404 || err.code === 'PM_DIFF_NOT_FOUND') {
        container.append(h('p', {class: 'muted pm-work-result-diff-notice'}, t('error_code_PM_DIFF_NOT_FOUND')));
      } else {
        const errBoxNode = errorBox ? errorBox(err, {within: container}) : h('div', {class: 'error'}, err?.message || String(err));
        const retryBtn = button({
          label: t('wres_try_again'),
          class: 'ui-button secondary ui-sm pm-work-result-retry',
          onClick: () => {
            diffErrors.delete(key);
            void loadDiff(commit, path, key, container, summaryNode);
          },
        });
        retryBtn.setAttribute('data-work-result-retry-diff', key);
        container.append(h('div', {class: 'pm-work-result-error-box'}, errBoxNode, retryBtn));
      }
      return;
    }

    const data = diffCache.get(key);
    if (!data) return;

    // Stats: +A −D
    const stats = data.stats || {};
    const adds = stats.additions === 'unknown' || stats.additions == null ? '?' : `+${stats.additions}`;
    const dels = stats.deletions === 'unknown' || stats.deletions == null ? '?' : `−${stats.deletions}`;
    container.append(h('p', {class: 'pm-work-result-diff-stats mono'}, `${adds} ${dels}`));

    // Notices
    if (data.truncated === true) {
      const noticeP = h('p', {class: 'muted pm-work-result-diff-notice'});
      const rawText = t('wres_diff_truncated');
      if (rawText.includes('256 KiB')) {
        const [before, after] = rawText.split('256 KiB');
        noticeP.append(before, h('span', {class: 'mono'}, '256 KiB'), after);
      } else {
        noticeP.textContent = rawText;
      }
      container.append(noticeP);
    }

    if (data.available === false) {
      let reasonText = '';
      if (data.reason === 'diff_exceeded_pipe_limit') {
        reasonText = t('wres_reason_diff_exceeded_pipe_limit');
      } else if (data.reason && t(`wres_reason_${data.reason}`) !== `wres_reason_${data.reason}`) {
        reasonText = t(`wres_reason_${data.reason}`);
      } else if (data.reason && t(`error_code_${data.reason}`) !== `error_code_${data.reason}`) {
        reasonText = t(`error_code_${data.reason}`);
      } else {
        reasonText = t('wres_reason_generic_limit');
      }
      container.append(h('p', {class: 'muted pm-work-result-diff-notice'}, reasonText));
    } else if (typeof data.diff === 'string') {
      const pre = h('pre', {
        class: 'pm-work-result-diff-pre mono',
        tabindex: '0',
        role: 'region',
        'aria-label': t('wres_diff_region_label', {path, sha: commit.slice(0, 12)}),
        'data-work-result-diff-region': key,
      });
      pre.textContent = data.diff;
      container.append(pre);
    }
  }

  async function loadDiff(commit, path, key, container, summaryNode) {
    if (diffLoading.has(key)) return;
    diffLoading.add(key);
    diffErrors.delete(key);
    const generation = diffGeneration;
    const keyTicket = (diffKeySerials.get(key) || 0) + 1;
    diffKeySerials.set(key, keyTicket);
    const attemptId = activeAttempt?.attempt_id;
    const activeAtStart = document.activeElement;
    const startedFromRetry = Boolean(activeAtStart && activeAtStart.getAttribute('data-work-result-retry-diff') === key);
    const parkOnSummary = Boolean(activeAtStart && (container.contains(activeAtStart) || activeAtStart === summaryNode));
    if (parkOnSummary) {
      summaryNode.focus();
    }
    renderDiffContent(container, commit, path, key, summaryNode);

    try {
      const data = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/work-attempts/${encodeURIComponent(attemptId)}/diff?commit=${encodeURIComponent(commit)}&path=${encodeURIComponent(path)}`);
      try { alive?.(); } catch { return; }
      if (generation !== diffGeneration || diffKeySerials.get(key) !== keyTicket || activeAttempt?.attempt_id !== attemptId) return;
      diffCache.set(key, data);
    } catch (err) {
      try { alive?.(); } catch { return; }
      if (generation !== diffGeneration || diffKeySerials.get(key) !== keyTicket || activeAttempt?.attempt_id !== attemptId) return;
      diffErrors.set(key, err);
    } finally {
      if (generation === diffGeneration && diffKeySerials.get(key) === keyTicket) {
        diffLoading.delete(key);
        if (activeAttempt?.attempt_id === attemptId && panelElement.contains(container)) {
          renderDiffContent(container, commit, path, key, summaryNode);
          // Decide from where focus is now. Move it only when it is still on
          // this diff's summary, where this read put it. A retry that fails
          // again returns to the new Try again. Every other reply leaves focus
          // where the person has it, including a read started from the summary.
          const activeNow = document.activeElement;
          const stillOnSummary = Boolean(activeNow && (
            (activeNow === summaryNode && summaryNode.isConnected)
            || (panelElement.contains(activeNow) && activeNow.getAttribute('data-work-result-diff-summary') === key)
          ));
          if (stillOnSummary && startedFromRetry && diffErrors.has(key)) {
            elementByAttr(container, 'data-work-result-retry-diff', key)?.focus();
          }
        }
      }
    }
  }

  async function readCurrentChanges(isReadAgain = false) {
    if (changesBusy) return;
    changesBusy = true;
    const ticket = ++changesSerial;
    const attemptId = activeAttempt?.attempt_id;

    if (isReadAgain) {
      diffGeneration++;
      diffKeySerials.clear();
      diffCache.clear();
      diffErrors.clear();
      diffLoading.clear();
      openDiffs.clear();
      for (const dt of panelElement.querySelectorAll('.pm-work-result-diff-details[open]')) {
        dt.open = false;
      }
    }

    currentChangesError = null;
    if (lastEnvelope && activeAttempt) {
      renderEnvelope(lastEnvelope, activeAttempt);
    }

    try {
      const data = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/work-attempts/${encodeURIComponent(attemptId)}/changes`);
      try { alive?.(); } catch { return; }
      if (ticket !== changesSerial || activeAttempt?.attempt_id !== attemptId) return;

      currentChanges = data;
      isOutOfDate = false;
      currentChangesError = null;
    } catch (err) {
      try { alive?.(); } catch { return; }
      if (ticket !== changesSerial || activeAttempt?.attempt_id !== attemptId) return;
      currentChangesError = err;
    } finally {
      if (ticket === changesSerial) {
        changesBusy = false;
      }
      if (ticket === changesSerial && activeAttempt?.attempt_id === attemptId && lastEnvelope) {
        renderEnvelope(lastEnvelope, activeAttempt);
      }
    }
  }

  function renderCurrentChangesBlock() {
    const block = h('div', {
      class: 'pm-work-result-current-changes',
      'data-work-result-current-changes': '',
    });
    if (changesBusy) {
      block.setAttribute('aria-busy', 'true');
    }

    if (currentChangesError) {
      const errBoxNode = errorBox ? errorBox(currentChangesError, {within: block}) : h('div', {class: 'error'}, currentChangesError?.message || String(currentChangesError));
      const retryBtn = button({
        label: t('wres_try_again'),
        class: 'ui-button secondary ui-sm pm-work-result-retry',
        onClick: () => { void readCurrentChanges(false); },
      });
      retryBtn.setAttribute('data-work-result-retry', 'changes');
      if (changesBusy) {
        retryBtn.setAttribute('aria-disabled', 'true');
      }
      block.append(h('div', {class: 'pm-work-result-error-box'}, errBoxNode, retryBtn));
      return block;
    }

    if (isOutOfDate) {
      const outPill = h('span', {class: 'ui-pill tone-warn', 'data-status': 'out-of-date'}, t('wres_out_of_date'));
      const readAgainBtn = button({
        label: t('wres_read_again'),
        class: 'ui-button secondary ui-sm pm-work-result-read-again',
        onClick: () => { void readCurrentChanges(true); },
      });
      if (changesBusy) {
        readAgainBtn.setAttribute('aria-disabled', 'true');
      }
      block.append(h('div', {class: 'pm-work-result-out-of-date-banner'}, outPill, ' ', readAgainBtn));
    } else {
      const readBtn = button({
        label: t('wres_read_current_changes'),
        class: 'ui-button secondary ui-sm pm-work-result-read-changes',
        onClick: () => { void readCurrentChanges(false); },
      });
      if (changesBusy) {
        readBtn.setAttribute('aria-disabled', 'true');
      }
      block.append(readBtn);
    }

    if (!currentChanges) {
      return block;
    }

    // Head commit
    const headSha = currentChanges.live_head_commit;
    const headP = h('p', {class: 'pm-work-result-head'});
    headP.append(h('strong', {}, `${t('wres_head')}: `));
    if (headSha && headSha !== 'unknown') {
      headP.append(h('span', {class: 'mono'}, headSha.slice(0, 12)));
    } else {
      headP.append(t('wres_unknown'));
    }
    block.append(headP);

    // Freshness
    const freshness = currentChanges.head_freshness;
    let freshnessKey = 'wres_freshness_unavailable';
    if (freshness === 'live') freshnessKey = 'wres_freshness_live';
    else if (freshness === 'cached_receipt') freshnessKey = 'wres_freshness_cached_receipt';
    block.append(h('p', {class: 'muted pm-work-result-freshness'}, t(freshnessKey)));

    // Summary line
    const curSummary = currentChanges.summary || {};
    const isCurComplete = curSummary.complete === true && (!curSummary.unavailable || curSummary.unavailable.length === 0);
    const curFilesCountNum = Number.isSafeInteger(curSummary.file_count)
      ? curSummary.file_count
      : (Array.isArray(curSummary.files) ? curSummary.files.length : null);
    const curCommitsCountNum = Number.isSafeInteger(curSummary.commit_count)
      ? curSummary.commit_count
      : (Array.isArray(curSummary.commits) ? curSummary.commits.length : null);

    let curSummaryKey = 'wres_changes_summary';
    if (curFilesCountNum === 1 && curCommitsCountNum === 1) curSummaryKey = 'wres_changes_summary_one';
    else if (curFilesCountNum === 1) curSummaryKey = 'wres_changes_summary_one_file';
    else if (curCommitsCountNum === 1) curSummaryKey = 'wres_changes_summary_one_commit';

    const curSummaryText = t(curSummaryKey, {
      files: formatCount(curFilesCountNum),
      commits: formatCount(curCommitsCountNum),
      additions: formatCount(curSummary.additions),
      deletions: formatCount(curSummary.deletions),
    });

    if (isCurComplete) {
      block.append(h('p', {class: 'pm-work-result-changes-summary'}, curSummaryText));
    } else {
      block.append(h('p', {class: 'pm-work-result-changes-summary'}, t('wres_changes_partly_read')));
    }

    // Reason deduplication within Current changes block
    const blockReasons = new Set();
    if (!isCurComplete) {
      const curChangeReasons = [];
      if (curSummary.reason) {
        curChangeReasons.push(curSummary.reason);
        blockReasons.add(curSummary.reason);
      }
      if (Array.isArray(curSummary.reasons)) {
        for (const r of curSummary.reasons) {
          if (!curChangeReasons.includes(r) && !blockReasons.has(r)) {
            curChangeReasons.push(r);
            blockReasons.add(r);
          }
        }
      }
      if (curChangeReasons.length > 0) {
        const reasonsList = h('ul', {class: 'pm-work-result-changes-reasons'});
        for (const code of curChangeReasons) {
          const key = `wres_reason_${code}`;
          let text = t(key);
          if (!text || text === key) text = t('wres_reason_generic_limit');
          reasonsList.append(h('li', {}, text));
        }
        block.append(reasonsList);
      }
    }

    const curCompleteness = currentChanges.completeness;
    const curCompStatus = curCompleteness?.status;
    if (curCompStatus && curCompStatus !== 'complete') {
      const compDiv = h('div', {class: 'pm-work-result-completeness'});
      if (curCompStatus === 'partial') {
        compDiv.append(statusPill('partial'));
      } else {
        compDiv.append(h('span', {class: 'ui-pill tone-bad', 'data-status': 'unavailable'}, t('wres_status_unavailable')));
      }
      const compReasons = (curCompleteness.reasons || []).filter(code => {
        if (blockReasons.has(code)) return false;
        blockReasons.add(code);
        return true;
      });
      if (compReasons.length > 0) {
        const reasonList = h('ul', {class: 'pm-work-result-completeness-reasons'});
        for (const code of compReasons) {
          const key = `wres_reason_${code}`;
          let text = t(key);
          if (!text || text === key) text = t('wres_reason_generic_limit');
          reasonList.append(h('li', {}, text));
        }
        compDiv.append(reasonList);
      }
      block.append(compDiv);
    }

    // File rows
    const curFiles = curSummary.files || [];
    if (curFiles.length > 0) {
      const curFileList = h('ul', {class: 'pm-work-result-file-list'});
      for (const f of curFiles) {
        let statusLabel = t('wres_unknown');
        if (f.status === 'M' || f.status === 'modified') statusLabel = t('wres_file_modified');
        else if (f.status === 'A' || f.status === 'added') statusLabel = t('wres_file_added');
        else if (f.status === 'D' || f.status === 'deleted') statusLabel = t('wres_file_deleted');
        else if (typeof f.status === 'string' && (f.status.startsWith('R') || f.status === 'renamed')) statusLabel = t('wres_file_renamed');

        const adds = f.additions === 'unknown' || f.additions == null ? '?' : `+${f.additions}`;
        const dels = f.deletions === 'unknown' || f.deletions == null ? '?' : `−${f.deletions}`;

        const row = h('li', {class: 'pm-work-result-file-row'},
          h('span', {class: 'pm-work-result-file-path mono'}, f.path),
          ' ',
          h('span', {class: 'pm-work-result-file-status muted'}, statusLabel),
          ' ',
          h('span', {class: 'pm-work-result-file-diff mono'}, `${adds} ${dels}`),
        );

        if (canDiff()) {
          const commits = sortCommitsOldestFirst(f.commits_touched, curSummary.commits);
          for (const commit of commits) {
            const key = `${commit}:${f.path}`;
            const isOpen = openDiffs.has(key);
            const sha12 = commit.slice(0, 12);

            const summaryNode = h('summary', {
              'data-work-result-diff-summary': key,
              tabindex: '0',
            }, t('wres_show_diff_label'), ' · ', h('span', {class: 'mono'}, sha12));

            const bodyContainer = h('div', {class: 'pm-work-result-diff-body'});
            renderDiffContent(bodyContainer, commit, f.path, key, summaryNode);

            const diffDetails = disclosure({
              element: h('details', {class: 'pm-work-result-diff-details', open: isOpen}, summaryNode, bodyContainer),
            });

            diffDetails.addEventListener('toggle', () => {
              if (diffDetails.open) {
                openDiffs.add(key);
                if (!diffCache.has(key) && !diffLoading.has(key) && !diffErrors.has(key)) {
                  void loadDiff(commit, f.path, key, bodyContainer, summaryNode);
                }
              } else {
                openDiffs.delete(key);
              }
            });

            row.append(diffDetails);
          }
        }
        curFileList.append(row);
      }
      block.append(curFileList);
    }

    // Uncommitted
    const curUncommitted = currentChanges.uncommitted || {};
    const uncommittedBox = h('div', {class: 'pm-work-result-uncommitted-box'});
    if (curUncommitted.count === 'unknown') {
      uncommittedBox.append(h('p', {class: 'muted pm-work-result-uncommitted'}, t('wres_uncommitted_unknown')));
    } else if (Number.isSafeInteger(curUncommitted.count) && curUncommitted.count > 0) {
      uncommittedBox.append(h('p', {class: 'pm-work-result-uncommitted'},
        t('wres_uncommitted_changes', {n: curUncommitted.count}),
      ));
      if (Array.isArray(curUncommitted.files) && curUncommitted.files.length > 0) {
        const uncommittedList = h('ul', {class: 'pm-work-result-file-list pm-work-result-uncommitted-list'});
        for (const uf of curUncommitted.files) {
          const p = typeof uf === 'string' ? uf : uf?.path;
          const s = typeof uf === 'object' && uf?.status ? uf.status : null;
          const li = h('li', {class: 'pm-work-result-file-row'},
            h('span', {class: 'pm-work-result-file-path mono'}, p),
          );
          if (s) {
            let statusLabel = s;
            if (s === 'M' || s === 'modified') statusLabel = t('wres_file_modified');
            else if (s === 'A' || s === 'added') statusLabel = t('wres_file_added');
            else if (s === 'D' || s === 'deleted') statusLabel = t('wres_file_deleted');
            li.append(' ', h('span', {class: 'pm-work-result-file-status muted'}, statusLabel));
          }
          uncommittedList.append(li);
        }
        uncommittedBox.append(uncommittedList);
      }
    }
    uncommittedBox.append(h('p', {class: 'muted pm-work-result-uncommitted-no-diff'}, t('wres_uncommitted_no_diff')));
    block.append(uncommittedBox);

    return block;
  }

  return {
    element: panelElement,
    sync: () => publish.sync(),
    setAttempt,
    refresh,
    withdraw,
    dispose,
  };
}
