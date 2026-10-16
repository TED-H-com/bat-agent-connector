import {button, disclosure, statusPill} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
import {sessionHref} from './session-navigation.js';

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

export function workResultPanel({h, t, api, alive, workspaceId, caps, errorBox, onVisibility = () => {}}) {
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

  const panelElement = h('div', {class: 'pm-work-result-panel', 'data-work-result': ''});
  panelElement.hidden = true;

  function updateVisibility(visible) {
    panelElement.hidden = !visible;
    onVisibility(visible);
  }

  function withdraw() {
    readSerial++;
    lastEnvelope = null;
    lastNormalized = null;
    lastStale = false;
    lastError = null;
    turnsOpen = false;
    detailsOpen = false;
    filesOpen = false;
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
        lastEnvelope = data;
        lastNormalized = normalized;
        lastStale = false;
        lastError = null;
        renderEnvelope(data, currentAttempt);
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
    panelElement.replaceChildren(
      h('h3', {}, t('wres_heading')),
      h('p', {class: 'pm-work-result-invalid'}, t('wres_invalid_envelope')),
    );
    updateVisibility(true);
  }

  function renderError(err, attempt) {
    const heading = h('h3', {}, t('wres_heading'));
    const retryBtn = button({
      label: t('wres_try_again'),
      class: 'ui-button secondary ui-sm pm-work-result-retry',
      onClick: () => { void readResult(); },
    });
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

  function saveFocus() {
    const active = document.activeElement;
    if (active && panelElement.contains(active)) {
      if (active.dataset?.workResultFocus) {
        return {type: 'attr', value: active.dataset.workResultFocus};
      }
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
    requestAnimationFrame(() => {
      if (token.type === 'attr') {
        const target = panelElement.querySelector(`[data-work-result-focus="${token.value}"]`);
        target?.focus();
      } else if (token.type === 'turns') {
        panelElement.querySelector('[data-work-result-turns] > summary')?.focus();
      } else if (token.type === 'details') {
        panelElement.querySelector('[data-work-result-details] > summary')?.focus();
      } else if (token.type === 'files') {
        panelElement.querySelector('.pm-work-result-files > summary')?.focus();
      }
    });
  }

  function renderEnvelope(envelope, attempt, errorBoxNode = null) {
    const focusToken = saveFocus();

    // Preserve disclosure open states from DOM if present
    const existingTurns = panelElement.querySelector('[data-work-result-turns]');
    if (existingTurns) turnsOpen = existingTurns.open;
    const existingDetails = panelElement.querySelector('[data-work-result-details]');
    if (existingDetails) detailsOpen = existingDetails.open;
    const existingFiles = panelElement.querySelector('.pm-work-result-files');
    if (existingFiles) filesOpen = existingFiles.open;

    const sections = [];
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
      const liveHref = sessionHref({host: sess.host, sessionId: sess.session_id, from: 'board'});
      linkItems.push(h('a', {href: liveHref, 'data-work-result-focus': 'live-output'}, t('wres_open_live_output')));
    }
    const opHash = envelope.links?.start_operation_hash || (envelope.identity?.start_operation_id ? `#/op/${envelope.identity.start_operation_id}` : null);
    if (opHash) {
      linkItems.push(h('a', {href: opHash, 'data-work-result-focus': 'operation'}, t('wres_view_operation')));
    }
    if (linkItems.length > 0) {
      sections.push(h('div', {class: 'pm-work-result-links'}, ...linkItems));
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
        const link = h('a', {href: `#/op/${encodeURIComponent(pr.publication_operation_id)}`},
          `PR #${pr.pull_number} · ${pr.repository}`);
        prList.append(h('li', {}, link));
      }
      prDd.append(prList);
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
      if (summary.reason) changeReasons.push(summary.reason);
      if (Array.isArray(summary.reasons)) {
        for (const r of summary.reasons) {
          if (!changeReasons.includes(r)) changeReasons.push(r);
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
      else if (srcKind === 'integration') srcText = t('wres_source_delivered_commit');

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

      const reasons = completeness.reasons || [];
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

  return {
    element: panelElement,
    setAttempt,
    refresh,
    withdraw,
    dispose,
  };
}
