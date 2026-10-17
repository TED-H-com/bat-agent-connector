// Publish one pull request from the fixed Result entry. The client copies the
// offered target, prefilled commit and base, and preconditions. It does not
// invent a provider, account, base, or GitHub URL.

const ACTION = 'github.pr.publish';
const SHA40 = /^[0-9a-f]{40}$/;
const OP_ID = /^op_[0-9a-f]{32}$/;
const TARGET_KEYS = ['workspace_id', 'repository', 'host', 'source_operation_id'];
const PARAM_NAMES = ['candidate_sha', 'base_ref', 'public_title', 'public_body', 'draft'];
const BLOCKED_KEYS = {
  not_controller: 'wpub_blocked_not_controller',
  not_writer_member: 'wpub_blocked_not_writer',
  missing_scope: 'wpub_blocked_scope',
  WAIT_ORIGINAL_OPERATION_RECONCILE: 'wpub_blocked_reconcile',
  pull_request_exists: 'wpub_blocked_pr_exists',
  lifecycle_stopped: 'wpub_blocked_lifecycle',
  github_not_configured: 'wpub_blocked_github',
  publication_source_incomplete: 'wpub_blocked_source',
};
const HOST_CODES = {
  PR_SOURCE_MOVED: 'wpub_source_moved',
  PR_BASE_MOVED: 'wpub_base_moved',
  PR_SOURCE_HISTORY_CHANGED: 'wpub_history_changed',
  PR_BASE_NOT_ANCESTOR: 'wpub_base_not_ancestor',
  PR_EMPTY_CHANGE: 'wpub_empty_change',
  PR_PIN_CHANGED: 'wpub_pin_changed',
  PR_RECEIPT_EXISTS: 'wpub_receipt_exists',
  PR_BRANCH_EXISTS: 'wpub_branch_exists',
  PR_BRANCH_CREATE_REFUSED: 'wpub_branch_refused',
  PR_CHANGE_LIMIT: 'wpub_change_limit',
  PR_CREATE_REFUSED: 'pm_terminal',
  PR_PATH_UNREADABLE: 'pm_terminal',
  PR_PUBLISHED_HEAD_CHANGED: 'pm_terminal',
  PR_RECEIPT_UNAVAILABLE: 'pm_terminal',
  PR_PROVIDER_TARGET_CHANGED: 'wpub_source_changed',
  PR_CREATED_TARGET_CHANGED: 'wpub_created_target_changed',
  PR_FOREIGN_CONTROLLER: 'wpub_blocked_not_controller',
  INTERNAL: 'error_generic',
  PR_AUTHORITY_UNAVAILABLE: 'wpub_authority',
};
const SETUP_CODES = new Set([
  'GITHUB_NOT_CONFIGURED',
  'REPO_NOT_CONFIGURED',
  'INTEGRATION_DISABLED',
  'UNKNOWN_HOST',
  'TIER_DISABLED',
  'NO_MANAGED_ROOT',
  'GIT_RUNNER_UNAVAILABLE',
]);
const QUIET_CODES = new Set(['TIMEOUT', 'NETWORK_ERROR', 'CENTRAL_OFFLINE', 'CONNECTION_CHANGED']);
let radioSerial = 0;

const text = value => typeof value === 'string' && value.length > 0;
const sha = value => typeof value === 'string' && SHA40.test(value);
const bytes = value => new TextEncoder().encode(value).length;

function exactTarget(target) {
  if (!target || typeof target !== 'object' || Array.isArray(target)) return null;
  const keys = Object.keys(target);
  if (keys.length !== TARGET_KEYS.length) return null;
  if (!TARGET_KEYS.every(key => Object.hasOwn(target, key) && text(target[key]))) return null;
  return {
    workspace_id: target.workspace_id,
    repository: target.repository,
    host: target.host,
    source_operation_id: target.source_operation_id,
  };
}

function exactPreconditions(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const keys = Object.keys(value).sort();
  if (keys.length !== 2 || keys[0] !== 'expected_base_sha' || keys[1] !== 'repository_id') return null;
  const id = value.repository_id;
  if (typeof id !== 'number' || !Number.isSafeInteger(id) || id <= 0) return null;
  if (!sha(value.expected_base_sha)) return null;
  return {repository_id: id, expected_base_sha: value.expected_base_sha};
}

function actionRows(list) {
  if (!Array.isArray(list)) return [];
  return list.filter(row => row && row.action === ACTION);
}

function paramsReady(entry) {
  return Array.isArray(entry?.required_params) && PARAM_NAMES.every(name => entry.required_params.includes(name));
}

// One rendering rule: Submit exists only for a single complete offered entry.
// A listed but incomplete entry keeps a draft and does not submit. No row at
// all draws nothing. A blocked row wins over a listed action.
export function classifyPublicationEntry(envelope) {
  const blocked = actionRows(envelope?.blocked_actions);
  const allowed = actionRows(envelope?.next_allowed_actions);
  if (blocked.length) {
    const reasons = blocked.map(row => typeof row.reason === 'string' ? row.reason : '');
    const unique = new Set(reasons);
    return {mode: 'blocked', gap: '', reason: unique.size === 1 ? reasons[0] : '', entry: null};
  }
  if (!allowed.length) return {mode: 'absent', gap: '', reason: '', entry: null};
  if (allowed.length > 1) return {mode: 'incomplete', gap: 'target', reason: '', entry: null};
  const row = allowed[0];
  if (row.kind != null && row.kind !== 'action') return {mode: 'incomplete', gap: 'target', reason: '', entry: null};
  const target = exactTarget(row.target);
  const prefilled = row.prefilled_params && typeof row.prefilled_params === 'object' && !Array.isArray(row.prefilled_params) ? row.prefilled_params : {};
  const candidate = sha(prefilled.candidate_sha) ? prefilled.candidate_sha : '';
  const baseRef = text(prefilled.base_ref) ? prefilled.base_ref : '';
  const preconditions = exactPreconditions(row.preconditions);
  const scopes = Array.isArray(row.required_scopes) ? row.required_scopes.filter(item => typeof item === 'string') : [];
  if (!target) return {mode: 'incomplete', gap: 'target', reason: '', entry: null};
  const entry = {target, candidate, baseRef, preconditions, scopes};
  if (!candidate) return {mode: 'incomplete', gap: 'commit', reason: '', entry};
  if (!baseRef || !preconditions || !paramsReady(row)) return {mode: 'incomplete', gap: 'preconditions', reason: '', entry};
  return {mode: 'ready', gap: '', reason: '', entry};
}

function identityOf(entry) {
  if (!entry?.target) return '';
  return JSON.stringify({target: entry.target, candidate_sha: entry.candidate || ''});
}

function readCaps(caps) {
  return typeof caps === 'function' ? caps() : caps;
}

function catalogAllows(caps, entry) {
  const current = readCaps(caps);
  const scopes = Array.isArray(current?.scopes) ? current.scopes : [];
  const required = entry?.scopes?.length ? entry.scopes : ['observe', 'manage', 'integrate'];
  if (!required.every(scope => scopes.includes(scope))) return false;
  if (!scopes.includes('integrate')) return false;
  return Boolean(current?.actions?.some(row => row.action === ACTION && row.allowed === true));
}

function receiptSha(envelope) {
  const rows = envelope?.delivery?.pull_requests;
  if (!Array.isArray(rows) || !rows.length) return '';
  const last = rows[rows.length - 1];
  return sha(last?.candidate_sha) ? last.candidate_sha : '';
}

function sourceSentence(t, envelope, candidate) {
  const fixed = envelope?.changes?.fixed_result_commit;
  if (fixed?.commit_sha === candidate) {
    if (fixed.source_kind === 'turn_end_observation') return t('wres_source_turn_end_observation');
    if (fixed.source_kind === 'execution_verification' || fixed.source_kind === 'session_verification') return t('wres_source_verified_commit');
    if (fixed.source_kind === 'integration') return t('wres_source_integration_commit');
  }
  const receipt = receiptSha(envelope);
  if (receipt && receipt === candidate && receipt !== fixed?.commit_sha) return t('wpub_commit_from_receipt');
  return t('wpub_commit_from_result');
}

function operationId(value) {
  return typeof value === 'string' && OP_ID.test(value) ? value : '';
}

export function publicationPrLine(h, t, pr) {
  if (!pr || typeof pr !== 'object') return null;
  const opId = operationId(pr.publication_operation_id);
  const number = Number.isSafeInteger(pr.pull_number) ? pr.pull_number : null;
  const repo = text(pr.repository) ? pr.repository : '';
  const label = number != null && repo ? `PR #${number} · ${repo}` : '';
  const item = h('li', {});
  if (opId) {
    item.append(h('a', {href: `#/op/${encodeURIComponent(opId)}`, 'data-work-publish-pr': opId}, label || t('pm_operation')));
    if (pr.target_verified !== true) item.append(' ', h('span', {'data-work-publish-unverified': ''}, t('wpub_pr_unverified')));
  } else if (label) {
    item.append(h('span', {}, label));
  } else {
    return null;
  }
  if (pr.requirement_acceptance === 'not_evaluated_by_publication') {
    item.append(' ', h('span', {'data-work-publish-not-accepted': ''}, t('wpub_pr_not_accepted')));
  }
  return item;
}

export function publicationDeliveryLine(h, t, pr, envelope, attempt) {
  if (!operationId(pr?.publication_operation_id)) return null;
  const host = text(attempt?.session?.host) ? attempt.session.host : (text(attempt?.native_session?.host) ? attempt.native_session.host : '');
  const source = text(pr.child_operation_id) ? pr.child_operation_id : (text(envelope?.identity?.execution_operation_id) ? envelope.identity.execution_operation_id : '');
  const repo = text(pr.repository) ? pr.repository : '';
  if (!host || !source || !repo) return null;
  const href = `#/delivery/${encodeURIComponent(host)}/execution/${encodeURIComponent(source)}/${encodeURIComponent(repo)}`;
  return h('p', {'data-work-publish-delivery': ''}, h('a', {href}, t('nav_delivery')), ' ', t('wpub_delivery_link'));
}

function gapSentence(t, gap) {
  if (gap === 'commit') return t('wpub_commit_missing');
  if (gap === 'preconditions') return t('wpub_gap_preconditions');
  return t('wpub_gap_target');
}

export function createWorkPublish({h, t, form, namespace, caps, errorBox}) {
  let editor = null;
  let root = null;
  let mode = 'absent';
  let bound = null;
  let pinned = null;
  let identity = '';
  let heldNode = null;
  let gapNode = null;
  let radios = null;
  let facts = null;
  let shownEnvelope = null;

  const displayEntry = () => (editor?.locked() ? (pinned || bound) : bound);

  function dispose() {
    dropEditor();
    root = null;
  }

  function dropEditor() {
    editor?.dispose();
    editor = null;
    identity = '';
    bound = null;
    pinned = null;
    mode = 'absent';
    radios = null;
    facts = null;
    heldNode = null;
    gapNode = null;
  }

  function recheck() {
    if (editor?.locked()) editor.recheck();
  }

  function sentenceFor(error) {
    const code = error?.code || '';
    const status = editor?.operation()?.status || '';
    if (status === 'uncertain' || code === 'UNCERTAIN') return t('wpub_uncertain');
    if (code === 'PR_CREATED_TARGET_CHANGED') return t('wpub_created_target_changed');
    if (status === 'needs_attention' || code === 'UNCERTAIN_UNRESOLVED') return t('wpub_needs_attention');
    if (HOST_CODES[code]) return t(HOST_CODES[code]);
    if (SETUP_CODES.has(code)) return t(readCaps(caps)?.managed_installation ? 'wpub_blocked_github_managed' : 'wpub_blocked_github_plain');
    if (!code || QUIET_CODES.has(code)) return '';
    if (status === 'failed' || status === 'cancelled' || error?.admissionRefused === true || error?.status) return t('error_generic');
    return '';
  }

  function paintFacts() {
    if (!facts) return;
    const entry = displayEntry();
    const candidate = entry?.candidate || '';
    facts.commit.hidden = !candidate;
    if (candidate) {
      facts.commitSha.textContent = candidate.slice(0, 12);
      facts.commitSha.title = candidate;
      facts.commitSha.setAttribute('aria-label', `${t('wpub_commit')} ${candidate}`);
      facts.commitSource.textContent = sourceSentence(t, shownEnvelope, candidate);
      facts.detailCommit.textContent = candidate;
    }
    const showBase = Boolean(entry?.baseRef && entry?.preconditions);
    facts.base.hidden = !showBase;
    facts.baseSha.hidden = !showBase;
    facts.origin.hidden = !showBase;
    if (showBase) {
      facts.baseValue.textContent = entry.baseRef;
      const full = entry.preconditions.expected_base_sha;
      facts.baseShaValue.textContent = full.slice(0, 12);
      facts.baseShaValue.title = full;
      facts.baseShaValue.setAttribute('aria-label', `${t('wpub_base_sha')} ${full}`);
      facts.detailBase.textContent = full;
    }
  }

  function applyChrome() {
    if (!editor) return;
    const submit = editor.box.querySelector('button[type="submit"]');
    const reset = editor.box.querySelector('.actions > button[type="button"]');
    if (submit) submit.setAttribute('data-work-publish-submit', '');
    if (reset) reset.setAttribute('data-work-publish-new-request', '');
    const locked = Boolean(editor.locked());
    if (submit && !locked && mode !== 'ready') submit.hidden = true;
    const code = editor.operation()?.error?.code;
    if (reset && code === 'PR_RECEIPT_EXISTS') reset.hidden = true;
    if (radios) {
      radios.yes.disabled = locked;
      radios.no.disabled = locked;
      const value = radios.mirror.value;
      radios.yes.checked = value === 'true';
      radios.no.checked = value === 'false';
    }
    if (gapNode) gapNode.hidden = mode === 'ready' || locked;
    const statusBox = editor.box.querySelector(':scope > [role="status"]');
    const op = editor.operation();
    if (statusBox) {
      let note = statusBox.querySelector('[data-work-publish-admitted]');
      if (op?.status === 'accepted') {
        if (!note) statusBox.append(h('p', {'data-work-publish-admitted': ''}, t('wpub_admitted_note')));
      } else if (note) note.remove();
    }
    if (locked) pinned = pinned || bound;
    else pinned = null;
    paintFacts();
  }

  function noteHeld(decision) {
    if (!heldNode) return;
    const next = identityOf(decision.entry);
    const current = displayEntry();
    const baseMoved = Boolean(decision.entry && current && (
      decision.entry.baseRef !== current.baseRef
      || decision.entry.preconditions?.expected_base_sha !== current.preconditions?.expected_base_sha
      || decision.entry.target?.repository !== current.target?.repository
    ));
    heldNode.hidden = Boolean(next && next === identity && !baseMoved);
  }

  function chooseDraft(value) {
    if (!radios || editor?.locked()) return;
    radios.mirror.value = value;
    radios.mirror.dispatchEvent(new Event('input', {bubbles: true}));
    radios.mirror.dispatchEvent(new Event('change', {bubbles: true}));
  }

  function mountEditor(decision, envelope) {
    const entry = decision.entry;
    bound = entry;
    mode = decision.mode;
    shownEnvelope = envelope;
    identity = identityOf(entry);
    const candidateKey = entry.candidate || 'no-commit';
    const groupId = `wpub-draft-${++radioSerial}`;
    const titleInput = h('input', {type: 'text', 'data-work-publish-title': '', 'aria-label': t('wpub_title')});
    const bodyInput = h('textarea', {'data-work-publish-body': '', 'aria-label': t('wpub_body')});
    const mirror = h('input', {type: 'hidden', 'data-work-publish-draft-value': ''});
    titleInput.dataset.pmDefault = '';
    bodyInput.dataset.pmDefault = '';
    mirror.dataset.pmDefault = '';
    const yes = h('input', {type: 'radio', name: `work-publish-draft-${radioSerial}`, value: 'true', 'data-work-publish-draft': 'true'});
    const no = h('input', {type: 'radio', name: `work-publish-draft-${radioSerial}`, value: 'false', 'data-work-publish-draft': 'false'});
    yes.addEventListener('change', () => { if (yes.checked) chooseDraft('true'); });
    no.addEventListener('change', () => { if (no.checked) chooseDraft('false'); });
    radios = {yes, no, mirror, titleInput, bodyInput};
    const commitSha = h('span', {class: 'mono', 'data-work-publish-commit': ''}, '');
    const commitSource = h('span', {'data-work-publish-commit-source': ''}, '');
    const baseValue = h('span', {'data-work-publish-base': ''}, '');
    const baseShaValue = h('span', {class: 'mono', 'data-work-publish-base-sha': ''}, '');
    const detailCommit = h('span', {class: 'mono'}, '');
    const detailBase = h('span', {class: 'mono'}, '');
    facts = {
      commit: h('p', {class: 'pm-work-publish-fact'}, h('strong', {}, `${t('wpub_commit')}: `), commitSha, ' · ', commitSource),
      commitSha, commitSource,
      base: h('p', {class: 'pm-work-publish-fact'}, h('strong', {}, `${t('wpub_base')}: `), baseValue),
      baseValue,
      baseSha: h('p', {class: 'pm-work-publish-fact'}, h('strong', {}, `${t('wpub_base_sha')}: `), baseShaValue),
      baseShaValue,
      origin: h('p', {class: 'muted pm-work-publish-fact'}, t('wpub_base_from_start')),
      detailCommit, detailBase,
    };
    heldNode = h('p', {'data-work-publish-held': '', hidden: true}, t('wpub_result_held'));
    gapNode = h('p', {'data-work-publish-gap': decision.gap || 'none'}, gapSentence(t, decision.gap));
    const extra = h('div', {class: 'pm-work-publish-fields'},
      h('p', {class: 'pm-work-publish-public'}, t('wpub_public')),
      facts.commit, facts.base, facts.baseSha, facts.origin,
      h('details', {'data-work-publish-sha-details': ''}, h('summary', {}, t('wres_details')),
        h('p', {}, t('wpub_commit'), ' ', detailCommit),
        h('p', {}, t('wpub_base_sha'), ' ', detailBase)),
      h('label', {}, t('wpub_title'), titleInput),
      h('label', {}, t('wpub_body'), bodyInput),
      h('div', {role: 'radiogroup', 'aria-labelledby': groupId, class: 'pm-work-publish-draft'},
        h('p', {id: groupId}, t('wpub_draft_group')),
        h('p', {class: 'muted'}, t('wpub_draft_help')),
        h('label', {class: 'pm-work-publish-choice'}, yes, h('span', {}, t('wpub_draft_yes'), ' ', h('span', {class: 'muted'}, t('wpub_draft_yes_help')))),
        h('label', {class: 'pm-work-publish-choice'}, no, h('span', {}, t('wpub_draft_no'), ' ', h('span', {class: 'muted'}, t('wpub_draft_no_help'))))),
      gapNode);
    editor = form({
      namespace: `${namespace}.work-publish.${candidateKey}`,
      action: ACTION,
      target: {
        workspace_id: entry.target.workspace_id,
        repository: entry.target.repository,
        host: entry.target.host,
        source_operation_id: entry.target.source_operation_id,
      },
      requiredScope: 'integrate',
      savedText: 'wpub_success',
      pendingText: 'wpub_pending',
      label: 'action_github_pr_publish',
      fields: {public_title: titleInput, public_body: bodyInput, draft: mirror},
      params: values => {
        const current = bound;
        const title = values.public_title ?? '';
        const body = values.public_body ?? '';
        if (!current || mode !== 'ready' || !current.candidate || !current.baseRef || !current.preconditions) {
          throw Error(t('wpub_gap_target'));
        }
        if (title.trim() === '') {
          titleInput.focus();
          throw Error(t('wpub_title_required'));
        }
        if (title.includes('\n') || title.includes('\r') || bytes(title) > 256) {
          titleInput.focus();
          throw Error(t('wpub_title_bounds'));
        }
        if (bytes(body) > 32000) {
          bodyInput.focus();
          throw Error(t('wpub_body_bounds'));
        }
        if (values.draft !== 'true' && values.draft !== 'false') {
          yes.focus();
          throw Error(t('wpub_draft_required'));
        }
        pinned = {
          target: current.target,
          candidate: current.candidate,
          baseRef: current.baseRef,
          preconditions: current.preconditions,
          scopes: current.scopes,
        };
        return {
          candidate_sha: current.candidate,
          base_ref: current.baseRef,
          public_title: title,
          public_body: body,
          draft: values.draft === 'true',
        };
      },
      preconditions: () => {
        const current = editor?.locked() ? (pinned || bound) : bound;
        if (!current?.preconditions) return {};
        return {
          repository_id: current.preconditions.repository_id,
          expected_base_sha: current.preconditions.expected_base_sha,
        };
      },
      permitted: () => mode === 'incomplete' || (mode === 'ready' && catalogAllows(caps, bound)),
      recoverPermitted: () => true,
      onSaved: () => {
        if (!radios) return;
        radios.yes.checked = false;
        radios.no.checked = false;
        radios.mirror.value = '';
      },
      onError: error => {
        const sentence = sentenceFor(error);
        if (!sentence) return false;
        const statusBox = editor?.box.querySelector(':scope > [role="status"]');
        if (!statusBox) return false;
        statusBox.hidden = false;
        const presented = errorBox ? errorBox(error, {summary: sentence, within: statusBox}) : h('p', {}, sentence);
        statusBox.replaceChildren(presented);
        if (error?.code === 'PR_AUTHORITY_UNAVAILABLE' || (SETUP_CODES.has(error?.code) && readCaps(caps)?.managed_installation)) {
          statusBox.append(h('p', {}, h('a', {href: '#/settings?section=connection'}, t('paths_setup'))));
        }
        return true;
      },
      extra,
    });
    const originalUpdate = editor.update;
    editor.update = () => {
      originalUpdate();
      applyChrome();
    };
    editor.box.addEventListener('submit', event => {
      if (!editor.locked() && mode !== 'ready') {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }, true);
    editor.box.addEventListener('click', event => {
      const reset = editor.box.querySelector('[data-work-publish-new-request]');
      if (!reset || (event.target !== reset && !reset.contains(event.target))) return;
      if (editor.operation()?.error?.code === 'PR_RECEIPT_EXISTS') {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }, true);
    editor.box.classList.add('pm-work-publish-form');
    editor.update();
    root = h('section', {class: 'pm-work-publish', 'data-work-publish': ''},
      h('h4', {}, t('action_github_pr_publish')), heldNode, editor.box);
    paintFacts();
    return root;
  }

  function staticSection(decision, envelope) {
    dropEditor();
    mode = decision.mode;
    shownEnvelope = envelope;
    if (decision.mode === 'absent') {
      root = null;
      return null;
    }
    if (decision.mode === 'blocked') {
      const known = BLOCKED_KEYS[decision.reason];
      const copy = known ? t(known) : t('wres_unknown');
      const next = decision.reason === 'github_not_configured'
        ? (readCaps(caps)?.managed_installation
          ? h('p', {}, h('a', {href: '#/settings?section=connection'}, t('paths_setup')), ' ', t('wpub_blocked_github_managed'))
          : h('p', {}, t('wpub_blocked_github_plain')))
        : null;
      root = h('section', {class: 'pm-work-publish', 'data-work-publish': '', 'data-work-publish-blocked': decision.reason || 'unknown'},
        h('h4', {}, t('action_github_pr_publish')),
        h('p', {'data-work-publish-blocked-reason': ''}, copy),
        next,
        decision.reason && !known ? h('details', {}, h('summary', {}, t('wres_details')), h('p', {}, decision.reason)) : null);
      return root;
    }
    const entry = decision.entry;
    if (!form || !entry?.target) {
      root = h('section', {class: 'pm-work-publish', 'data-work-publish': ''},
        h('h4', {}, t('action_github_pr_publish')),
        h('p', {'data-work-publish-gap': decision.gap || 'target'}, decision.mode === 'ready' ? t('wpub_no_submit') : gapSentence(t, decision.gap || 'target')));
      return root;
    }
    return mountEditor(decision, envelope);
  }

  function render(envelope) {
    const decision = classifyPublicationEntry(envelope);
    if (editor?.locked() && root) {
      noteHeld(decision);
      return root;
    }
    shownEnvelope = envelope;
    if (decision.mode === 'absent') return staticSection(decision, envelope);
    if (decision.mode === 'blocked') return staticSection(decision, envelope);
    if (!form || !decision.entry?.target) return staticSection(decision, envelope);
    const nextIdentity = identityOf(decision.entry);
    if (editor && root && identity === nextIdentity) {
      if (!editor.locked()) {
        bound = decision.entry;
        mode = decision.mode;
        if (gapNode) {
          gapNode.setAttribute('data-work-publish-gap', decision.gap || 'none');
          gapNode.textContent = gapSentence(t, decision.gap);
        }
        applyChrome();
      }
      return root;
    }
    dropEditor();
    return mountEditor(decision, envelope);
  }

  return {render, dispose, recheck, element: () => root};
}
