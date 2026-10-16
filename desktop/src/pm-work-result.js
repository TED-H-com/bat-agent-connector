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

export function workResultPanel({h, t, api, alive, workspaceId, caps, errorBox}) {
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

  function setContainerHidden(hidden) {
    panelElement.hidden = hidden;
    const section = panelElement.parentElement?.dataset.workSection === 'result'
      ? panelElement.parentElement
      : document.querySelector('[data-work-inspector] [data-work-section="result"]');
    if (section) {
      section.hidden = hidden;
      if (hidden) {
        section.remove();
      } else if (!section.isConnected) {
        const start = document.querySelector('[data-work-inspector] [data-work-section="start"]');
        if (start && start.nextSibling) {
          start.parentElement.insertBefore(section, start.nextSibling);
        } else if (start) {
          start.parentElement.append(section);
        }
      }
    }
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
    setContainerHidden(true);
  }

  function dispose() {
    activeAttempt = null;
    withdraw();
  }

  async function setAttempt(attempt) {
    if (!attempt || !attempt.session?.session_id) {
      activeAttempt = null;
      withdraw();
      return;
    }

    const previousAttemptId = activeAttempt?.attempt_id;
    activeAttempt = attempt;
    setContainerHidden(false);

    if (previousAttemptId !== attempt.attempt_id) {
      // Switched attempt: clear old content
      lastEnvelope = null;
      lastNormalized = null;
      lastStale = false;
      lastError = null;
      turnsOpen = false;
      detailsOpen = false;
      filesOpen = false;
      panelElement.replaceChildren();
    }

    await readResult();
  }

  async function refresh() {
    if (!activeAttempt || !activeAttempt.session?.session_id) return;
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
      // Re-render envelope with stale pill and error box above content
      renderEnvelope(lastEnvelope, attempt, errorContainer);
    } else {
      panelElement.replaceChildren(heading, errorContainer);
    }
  }

  function captureFocus() {
    const active = document.activeElement;
    if (!active || !panelElement.contains(active)) return null;

    if (active.tagName === 'A' && active.getAttribute('href')) {
      return {type: 'link', href: active.getAttribute('href')};
    }
    if (active.dataset.workResultFocus) {
      return {type: 'key', key: active.dataset.workResultFocus};
    }
    if (active.getAttribute('aria-label')) {
      return {type: 'label', label: active.getAttribute('aria-label')};
    }
    return {type: 'tag', tag: active.tagName};
  }

  function restoreFocus(token) {
    if (!token) return;
    let target = null;
    if (token.type === 'link') {
      target = panelElement.querySelector(`a[href="${CSS.escape(token.href)}"]`);
    } else if (token.type === 'key') {
      target = panelElement.querySelector(`[data-work-result-focus="${CSS.escape(token.key)}"]`);
    } else if (token.type === 'label') {
      target = panelElement.querySelector(`[aria-label="${CSS.escape(token.label)}"]`);
    } else if (token.type === 'tag') {
      target = panelElement.querySelector(token.tag);
    }
    if (target && typeof target.focus === 'function') {
      target.focus();
    }
  }

  function renderEnvelope(envelope, attempt, errorContainer = null) {
    // Preserve disclosure open states
    const existingTurns = panelElement.querySelector('[data-work-result-turns]');
    if (existingTurns) turnsOpen = existingTurns.open;
    const existingDetails = panelElement.querySelector('[data-work-result-details]');
    if (existingDetails) detailsOpen = existingDetails.open;
    const existingFiles = panelElement.querySelector('[data-work-result-all-files]');
    if (existingFiles) filesOpen = existingFiles.open;

    const focusToken = captureFocus();

    const sections = [];

    // Header
    const heading = h('h3', {}, t('wres_heading'));
    sections.push(heading);

    if (errorContainer) {
      sections.push(errorContainer);
    }

    // 1. State line
    const exec = envelope.execution || {};
    const stateKey = exec.state ? exec.state.replace(/-/g, '_') : 'unknown';
    const stateWord = t(`wres_state_${stateKey}`) || t('wres_state_unknown');

    const stateP = h('p', {class: 'pm-work-result-state'});
    stateP.append(h('strong', {}, stateWord));

    if (lastStale) {
      stateP.append(' ', statusPill('stale'));
    }

    if (exec.observed_at) {
      stateP.append(' · ', timeStamp(exec.observed_at));
    }
    sections.push(stateP);

    // Muted sentence for state_reason under unknown / failed / waiting / attention
    if (exec.state === 'unknown') {
      const reason = exec.state_reason;
      let reasonSentence = null;
      if (reason === 'handoff_outcome_uncertain') {
        reasonSentence = t('wres_reason_handoff_outcome_uncertain');
      } else if (reason === 'bat_unreachable') {
        reasonSentence = t('wres_reason_bat_unreachable');
      } else if (reason === 'observation_stale') {
        reasonSentence = t('wres_reason_observation_stale');
      } else if (reason === 'unloaded_history_session') {
        reasonSentence = t('wres_reason_unloaded_history_session');
      } else if (reason === 'turn_marker_not_visible' || reason === 'turn_boundary_not_collected') {
        reasonSentence = t('wres_reason_turn_boundary_not_collected');
      } else if (reason === 'session_not_managed_by_attempt') {
        reasonSentence = t('wres_reason_session_not_managed_by_attempt');
      }
      if (reasonSentence) {
        sections.push(h('p', {class: 'muted pm-work-result-reason'}, reasonSentence));
      }
    } else if (exec.state === 'failed') {
      const reason = exec.state_reason;
      if (reason === 'agent_error') {
        sections.push(h('p', {class: 'muted pm-work-result-reason'}, t('wres_agent_error')));
      } else if (reason) {
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
    const sess = attempt?.session;
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
      const pill = statusPill('neutral');
      pill.textContent = t('wres_pill_claimed_done');
      pill.dataset.workResultPill = 'claimed_done';
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
    const verifStatus = envelope.verification?.status || exec.state_facets?.verification || 'unknown';
    let verifText = t(`wres_verification_${verifStatus}`);
    if (!verifText || verifText === `wres_verification_${verifStatus}`) {
      verifText = t('wres_unknown');
    }
    dl.append(h('dt', {}, t('wres_check_verification')), h('dd', {'data-check': 'verification'}, verifText));

    // (b) Review
    const reviewDecision = exec.state_facets?.review_decision || envelope.verification?.quality_reviews?.current?.decision;
    let reviewText = t('wres_unknown');
    if (typeof reviewDecision === 'string') {
      if (reviewDecision === 'unassessed') reviewText = t('wres_review_unassessed');
      else if (reviewDecision === 'revocation') reviewText = t('wres_review_revocation');
    } else if (reviewDecision && typeof reviewDecision === 'object') {
      if (reviewDecision.kind === 'unassessed') {
        reviewText = t('wres_review_unassessed');
      } else if (reviewDecision.kind === 'revocation') {
        reviewText = t('wres_review_revocation');
      } else if (reviewDecision.kind === 'assessment' || reviewDecision.acceptance) {
        if (reviewDecision.acceptance === 'accepted') reviewText = t('wres_review_accepted');
        else if (reviewDecision.acceptance === 'rejected') reviewText = t('wres_review_rejected');
      }
    } else if (!reviewDecision) {
      reviewText = t('wres_review_unassessed');
    }
    dl.append(h('dt', {}, t('wres_check_review')), h('dd', {'data-check': 'review'}, reviewText));

    // (c) Requirement acceptance
    const acceptStatus = exec.state_facets?.acceptance?.status || 'unassessed';
    let acceptText = t(`wres_acceptance_${acceptStatus}`);
    if (!acceptText || acceptText === `wres_acceptance_${acceptStatus}`) {
      acceptText = t('wres_unknown');
    }
    dl.append(h('dt', {}, t('wres_check_acceptance')), h('dd', {'data-check': 'acceptance'}, acceptText));

    // (d) Pull request
    const mergeStatus = exec.state_facets?.merge || 'not_started';
    let mergeText = t(`wres_merge_${mergeStatus}`);
    if (!mergeText || mergeText === `wres_merge_${mergeStatus}`) {
      mergeText = t('wres_unknown');
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
    const deployState = exec.state_facets?.deployment?.state || 'not_deployed';
    const deployVerified = exec.state_facets?.deployment?.verified === true;
    let deployText = t(`wres_deploy_${deployState}`);
    if (deployState === 'succeeded' && deployVerified) {
      deployText = t('wres_deploy_succeeded_verified');
    } else if (!deployText || deployText === `wres_deploy_${deployState}`) {
      deployText = t('wres_unknown');
    }
    dl.append(h('dt', {}, t('wres_check_deployment')), h('dd', {'data-check': 'deployment'}, deployText));

    // (f) Delivery goal
    const goal = envelope.delivery?.delivery_goal || 'unknown';
    let goalText = t('wres_unknown');
    if (goal === 'pull_request') goalText = t('wres_goal_pr');
    else if (goal === 'reviewable_changes') goalText = t('wres_goal_reviewable_changes');
    dl.append(h('dt', {}, t('wres_check_delivery_goal')), h('dd', {'data-check': 'delivery-goal'}, goalText));

    sections.push(dl);

    // 4. Changes
    const changesBox = h('div', {class: 'pm-work-result-changes'});
    const ch = envelope.changes || {};
    const summary = ch.summary || {};

    const formatCount = val => (val === 'unknown' || val == null ? '?' : String(val));

    if (summary.complete === true) {
      const filesCount = formatCount(summary.file_count ?? summary.files?.length);
      const commitsCount = formatCount(summary.commit_count ?? summary.commits?.length);
      const addCount = formatCount(summary.additions);
      const delCount = formatCount(summary.deletions);
      const summaryText = t('wres_changes_summary', {
        files: filesCount,
        commits: commitsCount,
        additions: addCount,
        deletions: delCount,
      });
      changesBox.append(h('p', {class: 'pm-work-result-changes-summary'},
        summaryText, ' · ', t('wres_as_of_last_turn')));
    } else {
      const partlyText = h('p', {class: 'pm-work-result-changes-summary'},
        t('wres_changes_partly_read'), ' · ', t('wres_as_of_last_turn'));
      changesBox.append(partlyText);
    }

    // File list
    const files = summary.files || [];
    if (files.length > 0) {
      const renderFileRow = f => {
        let statusLabel = f.status || '';
        if (f.status === 'M') statusLabel = t('wres_file_modified');
        else if (f.status === 'A') statusLabel = t('wres_file_added');
        else if (f.status === 'D') statusLabel = t('wres_file_deleted');
        else if (typeof f.status === 'string' && f.status.startsWith('R')) statusLabel = t('wres_file_renamed');

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
        const extraList = h('ul', {class: 'pm-work-result-file-list'}, ...remainingFiles.map(renderFileRow));
        const allFilesDisclosure = disclosure({
          label: t('wres_show_all_files', {n: files.length}),
          body: extraList,
          open: filesOpen,
        });
        allFilesDisclosure.dataset.workResultAllFiles = '';
        changesBox.append(allFilesDisclosure);
      }
    }

    // Commits
    const commitsDiv = h('div', {class: 'pm-work-result-commits'});
    if (ch.base_commit) {
      commitsDiv.append(h('p', {},
        h('strong', {}, `${t('wres_base_commit')}: `),
        h('code', {class: 'mono'}, ch.base_commit.slice(0, 12)),
      ));
    }
    if (ch.fixed_result_commit?.commit_sha) {
      const fixed = ch.fixed_result_commit;
      let srcText = t('wres_unknown');
      if (fixed.source_kind === 'turn_end_observation') {
        srcText = t('wres_source_turn_end_observation');
      } else if (fixed.source_kind === 'session_verification' || fixed.source_kind === 'execution_verification') {
        srcText = t('wres_source_verified_commit');
      } else if (fixed.source_kind === 'integration') {
        srcText = t('wres_source_delivered_commit');
      }
      commitsDiv.append(h('p', {},
        h('strong', {}, `${t('wres_result_commit')}: `),
        h('code', {class: 'mono'}, fixed.commit_sha.slice(0, 12)),
        ' · ',
        h('span', {class: 'muted'}, srcText),
      ));
    }
    changesBox.append(commitsDiv);

    // Uncommitted changes
    const uncommitted = ch.uncommitted;
    if (uncommitted) {
      if (typeof uncommitted.count === 'number') {
        changesBox.append(h('p', {class: 'pm-work-result-uncommitted'},
          t('wres_uncommitted_changes', {n: uncommitted.count})));
      } else {
        changesBox.append(h('p', {class: 'pm-work-result-uncommitted muted'},
          t('wres_uncommitted_unknown')));
      }
    }
    sections.push(changesBox);

    // 5. Completeness
    const completeness = envelope.completeness;
    const completenessStatus = completeness?.status;
    if (completenessStatus && completenessStatus !== 'complete') {
      const compDiv = h('div', {class: 'pm-work-result-completeness'});
      const pillKind = completenessStatus === 'unavailable' ? 'bad' : 'warn';
      const compPill = statusPill(pillKind);
      compPill.textContent = completenessStatus;
      compDiv.append(compPill);

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
        // Clickable focusable link for test 2
        const link = h('a', {
          href: `#/turn/${encodeURIComponent(turn.turn_marker || turn.result_id || String(i))}`,
          class: 'mono pm-work-result-turn-link',
          'data-work-result-focus': `turn-${i}`,
        }, sha);
        row.append(link);

        if (i === 0) {
          const latestPill = statusPill('neutral');
          latestPill.textContent = t('wres_latest_turn');
          latestPill.classList.add('ui-xs');
          row.append(' ', latestPill);
        }
        turnsList.append(row);
      }
      turnsBody.append(turnsList);

      const turnsDisclosure = disclosure({
        label: t('wres_recorded_turns_count', {n: results.length}),
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

    // Unlisted reasons from completeness
    const unlistedReasons = (completeness?.reasons || []).filter(r => !KNOWN_REASONS.has(r));
    if (unlistedReasons.length > 0) {
      addDetailRow('Unlisted completeness reasons', unlistedReasons.join(', '));
    }

    // Usage
    const usage = envelope.usage || {};
    addDetailRow(t('wres_usage_context_tokens'), usage.context_tokens ?? 'unknown');
    addDetailRow(t('wres_usage_context_window'), usage.context_window ?? 'unknown');
    addDetailRow(t('wres_usage_num_turns'), usage.num_turns ?? 'unknown');

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
