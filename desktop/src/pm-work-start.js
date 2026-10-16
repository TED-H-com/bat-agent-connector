import {button, disclosure, statusPill} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
import {sessionHref} from './session-navigation.js';
import {operationStatusLabel} from './global-labels.js';

const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const PRECONDITION_KEYS = [
  'expected_work_item_fingerprint',
  'expected_work_version',
  'expected_binding_id',
  'expected_scope_revision',
  'expected_previous_package_id',
  'expected_project_version',
  'repository_id',
  'binding_digest',
  'expected_source_sha',
  'expected_latest_attempt_id',
];

export function workStartPanel({h, t, api, alive, namespace, workspaceId, caps, permitted, form, errorBox, plan: getPlan, onStartableChange = () => {}}) {
  let activeCard = null;
  let activeWorkId = null;
  let prepared = null;
  let serial = 0;
  let reading = false;
  let attempts = [];
  let latestAttempt = null;
  let visibilityFiltered = false;
  let partialAttempts = false;
  let chosenCandidate = null;
  let defaultInstruction = '';

  const editors = new Map();
  const attemptsView = h('div', {class: 'pm-work-start-attempts', 'data-work-start-attempts': ''});
  const editorView = h('div', {class: 'pm-work-start-editor', 'data-work-start-editor': ''});
  const statusView = h('div', {role: 'status', class: 'pm-work-start-status'});

  const panelElement = h('div', {class: 'pm-work-start-panel', 'data-work-start': ''},
    h('h3', {}, t('wst_heading')),
    attemptsView,
    editorView,
    statusView,
  );

  const valid = ticket => {
    try {
      alive();
      return serial === ticket;
    } catch {
      return false;
    }
  };

  const capability = () => caps()?.features?.pm_workspaces?.work_start;

  const available = () => {
    const cap = capability();
    return Boolean(cap?.version === 1 && cap?.available === true && cap?.action === 'work.start');
  };

  const isBindNeeded = card => {
    if (!card) return false;
    const currentPlan = getPlan();
    return Boolean(card.binding?.plan_revision_id !== currentPlan?.revision_id);
  };

  const canStartWorkspaces = () => {
    if (!available()) return false;
    const scopes = caps()?.scopes || [];
    if (!['observe', 'manage', 'start'].every(s => scopes.includes(s))) return false;
    const actions = caps()?.actions || [];
    if (!actions.some(a => a.action === 'work.start' && a.allowed)) return false;
    return Boolean(permitted(false));
  };

  const writer = (card = activeCard) => {
    if (!canStartWorkspaces()) return false;
    return Boolean(permitted(isBindNeeded(card)));
  };

  const startable = (card = activeCard) => {
    if (!card) return false;
    if (partialAttempts) return false;
    if (latestAttempt) {
      const terminal = ['failed', 'cancelled'].includes(latestAttempt.status) && latestAttempt.stage !== 'start_unknown';
      if (!terminal) return false;
    }
    return writer(card);
  };

  async function fetchAttempts(workId, ticket) {
    let after = '';
    let pages = 0;
    const list = [];
    let visFiltered = false;
    let part = false;

    while (pages < 10) {
      pages++;
      const query = new URLSearchParams({limit: '100'});
      if (after) query.set('after', after);
      const res = await api('GET', `/pm/workspaces/${encodeURIComponent(workspaceId)}/work/${encodeURIComponent(workId)}/attempts?${query}`);
      if (!valid(ticket)) return null;
      if (res.visibility_filtered) visFiltered = true;
      const arr = Array.isArray(res.attempts) ? res.attempts : [];
      for (const row of arr) {
        if (!/^att_[0-9a-f]{32}$/.test(row.attempt_id || '')
          || row.workspace_id !== workspaceId
          || row.work_item_id !== workId
          || typeof row.stage !== 'string') {
          throw Error(t('wst_invalid_attempt'));
        }
        list.push(row);
      }
      if (res.next_cursor == null) {
        break;
      }
      after = String(res.next_cursor);
      if (pages === 10 && res.next_cursor != null) {
        part = true;
      }
    }
    return {attempts: list, visibilityFiltered: visFiltered, partial: part};
  }

  function renderLatestAttempt(attempt) {
    attemptsView.replaceChildren();
    if (!attempt) return;

    const row = h('div', {class: 'pm-work-start-attempt'});
    let statusWord = '';
    let extraText = null;

    if (attempt.stage === 'start_unknown') {
      statusWord = t('wst_status_start_unknown');
      extraText = t('wst_check_bat_sessions');
    } else if (attempt.status === 'needs_attention') {
      statusWord = t('wst_status_needs_attention');
    } else if (['accepted', 'queued'].includes(attempt.status) && (!attempt.children || Object.values(attempt.children).every(c => !c))) {
      statusWord = t('wst_status_accepted');
    } else {
      switch (attempt.stage) {
        case 'preparing':
          statusWord = t('wst_status_preparing');
          break;
        case 'package_prepared':
          statusWord = t('wst_status_package_prepared');
          break;
        case 'starting':
          statusWord = t('wst_status_starting');
          break;
        case 'started':
          statusWord = t('wst_status_started');
          break;
        case 'start_unknown':
          statusWord = t('wst_status_start_unknown');
          extraText = t('wst_check_bat_sessions');
          break;
        case 'start_failed':
          statusWord = t('wst_status_start_failed');
          if (attempt.error_code) {
            const key = 'error_code_' + attempt.error_code;
            const msg = t(key);
            if (msg && msg !== key) {
              extraText = msg;
            } else if (attempt.status_reason) {
              extraText = attempt.status_reason;
            }
          } else if (attempt.status_reason) {
            extraText = attempt.status_reason;
          }
          break;
        case 'cancelled':
          statusWord = t('wst_status_cancelled');
          break;
        default:
          statusWord = attempt.stage || t('ui_status_unknown');
          break;
      }
    }

    const p = h('p', {class: 'pm-work-start-attempt-status'},
      h('strong', {}, statusWord),
      attempt.created_at ? ' · ' : null,
      attempt.created_at ? timeStamp(attempt.created_at) : null,
    );
    row.append(p);

    if (extraText) {
      row.append(h('p', {class: 'muted'}, extraText));
    }

    const links = h('div', {class: 'pm-work-start-links'});
    // Non-terminal: link the start operation (#/op/<start_operation_id>, "View operation")
    if (!['succeeded', 'failed', 'cancelled'].includes(attempt.status) && attempt.start_operation_id) {
      links.append(h('a', {href: `#/op/${encodeURIComponent(attempt.start_operation_id)}`}, t('wst_view_operation')));
    }

    // started with a session: show "Open live output" (use sessionHref with from: 'board') and "Open work" (#/item/<wid>)
    if ((attempt.stage === 'started' || attempt.status === 'succeeded') && attempt.session?.session_id) {
      const liveLink = h('a', {
        href: sessionHref({host: attempt.session.host, sessionId: attempt.session.session_id, from: 'board'}),
        'data-work-start-session': '',
      }, t('wst_open_live_output'));
      const workLink = h('a', {
        href: `#/item/${encodeURIComponent(activeCard.work_item_id)}`,
        'data-work-start-item': '',
      }, t('wst_open_work'));
      if (links.childNodes.length > 0) links.append(' · ');
      links.append(liveLink, ' · ', workLink);
    }
    if (links.childNodes.length > 0) {
      row.append(links);
    }

    // No IDs in visible text; put them in a Details disclosure.
    const detailsContent = h('div', {},
      h('p', {class: 'mono'}, `${t('wst_attempt_id')}: ${attempt.attempt_id}`),
      attempt.start_operation_id ? h('p', {class: 'mono'}, `${t('wst_operation_id')}: ${attempt.start_operation_id}`) : null,
      attempt.execution_operation_id ? h('p', {class: 'mono'}, `${t('wst_execution_op_id')}: ${attempt.execution_operation_id}`) : null,
      attempt.session ? h('p', {class: 'mono'}, `${t('wst_session_id')}: ${attempt.session.host} · ${attempt.session.session_id}`) : null,
    );
    row.append(disclosure({label: t('wst_details'), body: detailsContent}));

    if (visibilityFiltered) {
      row.append(h('p', {class: 'muted'}, t('wst_visibility_filtered')));
    }
    if (partialAttempts) {
      row.append(h('p', {class: 'muted'}, statusPill('partial'), ' ', t('wst_attempts_partial')));
    }

    attemptsView.append(row);
  }

  function getEditor(card) {
    if (!card) return null;
    const workId = card.work_item_id;
    if (editors.has(workId)) {
      return editors.get(workId);
    }

    const bindNeeded = isBindNeeded(card);
    const bindBox = h('div', {class: 'pm-work-start-bind'});
    const candidatesBox = h('div', {class: 'pm-work-start-candidates'});
    const factsBox = h('div', {class: 'pm-work-start-facts-box'});
    const checksBox = h('div', {class: 'pm-work-start-checks-box'});
    const staleNotice = h('p', {class: 'muted', hidden: true, 'data-work-start-stale': ''}, t('wst_stale_preview'));
    const disabledNotice = h('p', {class: 'muted', hidden: true, 'data-work-start-disabled': ''});
    const errorStatusBox = h('div', {role: 'status'});

    const instruction = h('textarea', {
      'aria-label': t('wst_instruction'),
      maxlength: 4000,
      required: true,
      rows: 4,
      class: 'pm-work-start-instruction',
      'data-work-start-instruction': '',
    });
    instruction.value = defaultInstruction;
    instruction.dataset.pmDefault = defaultInstruction;

    const agent = h('select', {'aria-label': t('wst_agent')});
    const agentList = capability()?.agents || ['claude', 'codex'];
    for (const a of agentList) {
      agent.append(h('option', {value: a}, a === 'claude' ? 'Claude' : a === 'codex' ? 'Codex' : a));
    }
    agent.value = 'claude';
    agent.dataset.pmDefault = 'claude';

    const delivery = h('select', {'aria-label': t('wst_delivery')});
    const deliveryList = capability()?.delivery_goals || ['pull_request', 'reviewable_changes'];
    for (const d of deliveryList) {
      const label = d === 'pull_request' ? t('wst_delivery_pr') : d === 'reviewable_changes' ? t('wst_delivery_changes') : d;
      delivery.append(h('option', {value: d}, label));
    }
    delivery.value = 'pull_request';
    delivery.dataset.pmDefault = 'pull_request';

    const model = h('input', {
      type: 'text',
      'aria-label': t('wst_model'),
      maxlength: 256,
      placeholder: t('wst_model_placeholder'),
    });
    model.dataset.pmDefault = '';

    const requirementsInput = h('input', {type: 'hidden'});
    requirementsInput.dataset.pmDefault = '[]';
    requirementsInput.value = '[]';

    const reviewInput = h('input', {type: 'hidden'});
    reviewInput.dataset.pmDefault = '';
    reviewInput.value = '';

    const currentFingerprint = () => {
      const reqs = bindNeeded ? JSON.parse(requirementsInput.value || '[]') : null;
      const targetChoice = chosenCandidate ? `${chosenCandidate.repository}:${chosenCandidate.host}:${chosenCandidate.workspace_id}` : null;
      return JSON.stringify({
        instruction: instruction.value.trim(),
        delivery: delivery.value,
        requirement_ids: reqs,
        target_choice: targetChoice,
      });
    };

    const onFingerprintChanged = () => {
      if (editorInstance?.locked()) return;
      if (prepared && reviewInput.value) {
        const isStale = currentFingerprint() !== prepared.fixed.fingerprint;
        staleNotice.hidden = !isStale;
      }
      editorInstance?.update();
      updateDisabledNotice();
    };

    instruction.addEventListener('input', onFingerprintChanged);
    instruction.addEventListener('change', onFingerprintChanged);
    agent.addEventListener('change', onFingerprintChanged);
    delivery.addEventListener('change', onFingerprintChanged);
    model.addEventListener('input', onFingerprintChanged);

    // Build bindBox checkboxes if bindNeeded
    const renderBindBox = () => {
      bindBox.replaceChildren();
      if (!bindNeeded) return;
      const curPlan = getPlan();
      if (!curPlan) {
        bindBox.replaceChildren(h('p', {class: 'muted'}, t('wst_no_adopted_plan')));
        return;
      }
      const planReqs = curPlan.document?.requirements || [];
      const boundIds = new Set(card.binding?.requirement_ids || []);
      const checkboxes = [];
      const reqWarning = h('p', {class: 'error', hidden: true}, t('wst_requirements_required'));

      const updateSelectedReqs = () => {
        const selected = checkboxes.filter(cb => cb.checked).map(cb => cb.value);
        requirementsInput.value = JSON.stringify(selected);
        reqWarning.hidden = selected.length > 0;
        onFingerprintChanged();
      };

      const list = h('div', {class: 'pm-work-start-reqs'},
        h('h4', {}, t('wst_requirements_to_bind')),
        ...planReqs.map(req => {
          const cb = h('input', {type: 'checkbox', value: req.id});
          if (boundIds.has(req.id)) {
            cb.checked = true;
          }
          cb.addEventListener('change', updateSelectedReqs);
          checkboxes.push(cb);
          return h('label', {class: 'pm-work-start-req'}, cb, req.text || req.id);
        }),
        reqWarning,
      );
      bindBox.replaceChildren(list);
      const initialSelected = checkboxes.filter(cb => cb.checked).map(cb => cb.value);
      requirementsInput.value = JSON.stringify(initialSelected);
      requirementsInput.dataset.pmDefault = JSON.stringify(initialSelected);
    };

    renderBindBox();

    const readinessButton = button({
      variant: 'secondary',
      label: t('wst_check_readiness'),
      'data-work-start-check': '',
      onClick: () => void inspectReadiness(),
    });

    const updateDisabledNotice = () => {
      if (editorInstance?.locked()) {
        disabledNotice.hidden = true;
        return;
      }
      const reasons = [];
      if (bindNeeded && !getPlan()) {
        reasons.push(t('wst_no_adopted_plan'));
      }
      if (bindNeeded && !permitted(true)) {
        reasons.push(t('wst_owner_required_to_bind'));
      } else if (!writer(card)) {
        reasons.push(t('wst_permission_missing'));
      }
      if (prepared && prepared.readinessStatus === 'blocked') {
        reasons.push(t('wst_readiness_blocked'));
      }
      if (prepared && currentFingerprint() !== prepared.fixed.fingerprint) {
        reasons.push(t('wst_stale_preview'));
      }

      if (reasons.length > 0) {
        disabledNotice.replaceChildren(h('span', {}, reasons.join(' ')), ' ', h('span', {class: 'muted'}, t('wst_draft_retained')));
        disabledNotice.hidden = false;
      } else {
        disabledNotice.hidden = true;
      }
    };

    async function inspectReadiness() {
      if (reading || !writer(card)) return;
      const ticket = ++serial;
      reading = true;
      factsBox.replaceChildren();
      checksBox.replaceChildren();
      errorStatusBox.replaceChildren();
      staleNotice.hidden = true;
      readinessButton.disabled = true;
      readinessButton.textContent = t('wst_checking_readiness');
      updateUI();

      try {
        const text = instruction.value.trim();
        if (!text) {
          instruction.focus();
          return;
        }
        const body = {
          instruction: text,
          delivery: delivery.value,
        };
        if (bindNeeded) {
          const reqs = JSON.parse(requirementsInput.value || '[]');
          if (!reqs.length) {
            throw Error(t('wst_requirements_required'));
          }
          body.requirement_ids = reqs;
        }
        if (chosenCandidate) {
          body.code_target = chosenCandidate;
        }

        const data = await api('POST', `/pm/workspaces/${encodeURIComponent(workspaceId)}/work/${encodeURIComponent(card.work_item_id)}/start-preview`, body);
        if (!valid(ticket)) return;

        if (data.workspace_id !== workspaceId || data.work_item_id !== card.work_item_id) {
          throw Error(t('wst_invalid_preview'));
        }

        // Candidates handling
        if (!data.code_target) {
          prepared = null;
          if (!editorInstance.locked()) editorInstance.set('review', '');
          const candidates = data.candidates || [];
          if (candidates.length === 0) {
            candidatesBox.replaceChildren(h('p', {class: 'muted', 'data-work-start-no-candidates': ''}, t('wst_no_candidates')));
          } else {
            const select = h('select', {
              'aria-label': t('wst_choose_candidate'),
              'data-work-start-candidate': '',
            }, h('option', {value: ''}, t('wst_choose_candidate')),
            ...candidates.map((c, i) => h('option', {value: String(i)}, `${c.repository} · ${c.host} · ${c.workspace_id}`)));

            select.addEventListener('change', () => {
              if (select.value === '') return;
              const idx = Number(select.value);
              chosenCandidate = candidates[idx];
              void inspectReadiness();
            });
            candidatesBox.replaceChildren(h('label', {}, t('wst_choose_candidate'), select));
          }
          return;
        }

        // Code target present - validate preview
        candidatesBox.replaceChildren();
        const source = data.source;
        if (!source || !/^[0-9a-f]{40}$/.test(source.source_sha || '')
          || !Number.isSafeInteger(source.repository_id) || source.repository_id <= 0
          || !/^[0-9a-f]{64}$/.test(source.binding_digest || '')) {
          throw Error(t('wst_invalid_preview'));
        }

        const pKeys = Object.keys(data.preconditions || {});
        if (pKeys.length !== 10 || !PRECONDITION_KEYS.every(k => k in (data.preconditions || {}))) {
          throw Error(t('wst_invalid_preview'));
        }

        if (!['ready', 'blocked'].includes(data.readiness?.status)) {
          throw Error(t('wst_invalid_preview'));
        }

        // Success
        const fp = currentFingerprint();
        const fixed = {
          fingerprint: fp,
          code_target: data.code_target,
          requirement_ids: data.binding?.bind_needed ? body.requirement_ids : null,
          preconditions: data.preconditions,
        };
        prepared = {
          fixed,
          readinessStatus: data.readiness.status,
          data,
        };

        if (!editorInstance.locked()) {
          editorInstance.set('review', JSON.stringify(fixed));
        }

        // Render facts
        const branchName = (source.source_ref || '').replace(/^refs\/heads\//, '');
        const shaShort = (source.source_sha || '').slice(0, 12);
        const hostWs = `${data.code_target.host} · ${data.code_target.workspace_id}`;
        const pkgText = data.package_plan?.package_reused ? t('wst_package_reused') : t('wst_package_composed');
        const bindNote = data.binding?.bind_needed ? t('wst_bind_needed_note') : null;

        factsBox.replaceChildren(
          h('div', {class: 'pm-work-start-facts', 'data-work-start-facts': ''},
            h('h4', {}, t('wst_readiness_checked')),
            h('p', {}, `${data.code_target.repository} · ${branchName} · ${shaShort} · ${hostWs}`),
            h('p', {class: 'muted'}, pkgText),
            bindNote ? h('p', {class: 'muted'}, bindNote) : null,
            h('p', {class: 'muted'}, timeStamp(data.observed_at || Date.now() / 1000)),
          )
        );

        // Render readiness checks
        const checks = (data.readiness.checks || []).map(check => {
          const [kind, ...suffix] = check.check.split(':');
          const key = 'wp_check_' + kind;
          const label = t(key);
          const help = t(key + '_help');
          const passed = check.status === 'passed';
          return h('li', {'data-readiness-check': check.check},
            h('strong', {}, label === key ? check.check : label + (suffix.length ? ' · ' + suffix.join(':') : '')),
            ' — ',
            t(passed ? 'wp_passed' : 'wp_attention'),
            !passed && help !== key + '_help' ? h('p', {class: 'muted'}, help) : null,
            !passed && check.detail ? h('p', {class: 'muted'}, check.detail) : null,
          );
        });

        checksBox.replaceChildren(h('ul', {class: 'pm-work-start-checks', 'data-pm-wp-checks': ''}, ...checks));

      } catch (err) {
        if (!valid(ticket)) return;
        prepared = null;
        factsBox.replaceChildren();
        checksBox.replaceChildren();
        if (!editorInstance.locked()) {
          editorInstance.set('review', '');
        }
        if ([401, 403, 404].includes(err.status)) {
          withdraw();
        }
        errorStatusBox.replaceChildren(errorBox(err, {within: errorStatusBox}));
      } finally {
        if (valid(ticket)) {
          reading = false;
          readinessButton.disabled = false;
          readinessButton.textContent = t('wst_check_readiness');
          editorInstance.update();
          updateUI();
        }
      }
    }

    const optionsDisclosure = disclosure({
      label: t('wst_options'),
      body: h('div', {class: 'pm-work-start-options'},
        h('label', {}, t('wst_model'), model),
      ),
    });

    const extra = h('div', {class: 'pm-work-start-fields'},
      bindBox,
      h('label', {}, t('wst_instruction'), instruction),
      h('p', {class: 'muted'}, t('wst_instruction_help')),
      h('label', {}, t('wst_agent'), agent),
      h('label', {}, t('wst_delivery'), delivery),
      optionsDisclosure,
      candidatesBox,
      staleNotice,
      h('div', {class: 'pm-work-start-readiness-action'}, readinessButton),
      factsBox,
      checksBox,
      disabledNotice,
      errorStatusBox,
    );

    const editorInstance = form({
      namespace: `${namespace}.work-start`,
      action: 'work.start',
      target: {workspace_id: workspaceId, work_item_id: card.work_item_id},
      requiredScope: 'start',
      savedText: 'wst_started_note',
      pendingText: 'wst_pending',
      statusText: status => status === 'waiting_external' ? t('wst_status_waiting_external') : operationStatusLabel(status),
      fields: {
        instruction,
        agent,
        delivery,
        model,
        requirements: requirementsInput,
        review: reviewInput,
      },
      params: values => {
        if (!canSubmit()) {
          throw Error(t('wst_not_ready_to_start'));
        }
        const rev = JSON.parse(values.review || '{}');
        const p = {
          instruction: values.instruction,
          agent: values.agent,
          delivery: values.delivery,
          code_target: rev.code_target,
        };
        if (rev.requirement_ids) {
          p.requirement_ids = JSON.parse(values.requirements || '[]');
        }
        if (values.model?.trim()) {
          p.model = values.model.trim();
        }
        return p;
      },
      preconditions: () => {
        try {
          return JSON.parse(reviewInput.value || '{}').preconditions || {};
        } catch {
          return {};
        }
      },
      permitted: () => writer(card) && startable(card) && !reading,
      recoverPermitted: () => writer(card),
      label: 'wst_start',
      onStateChange: () => {
        updateUI();
      },
      onSaved: async result => {
        prepared = null;
        await refreshAttempts();
        updateUI();
      },
      onError: error => {
        prepared = null;
        if (!editorInstance.locked()) {
          editorInstance.set('review', '');
        }
        void refreshAttempts();
        updateUI();
        return false;
      },
      extra,
    });

    const canSubmit = () => {
      if (!writer(card) || !startable(card) || reading) return false;
      if (!prepared || !reviewInput.value) return false;
      try {
        if (!equal(JSON.parse(reviewInput.value), prepared.fixed)) return false;
      } catch {
        return false;
      }
      if (currentFingerprint() !== prepared.fixed.fingerprint) return false;
      return prepared.readinessStatus === 'ready';
    };

    const origUpdate = editorInstance.update;
    editorInstance.update = () => {
      origUpdate();
      readinessButton.disabled = reading || !writer(card) || editorInstance.locked();
      updateDisabledNotice();
      const submitBtn = editorInstance.box.querySelector('button[type="submit"]');
      if (submitBtn && !editorInstance.locked()) {
        submitBtn.disabled = submitBtn.disabled || !canSubmit();
      }
    };

    editorInstance.currentFingerprint = currentFingerprint;
    editorInstance.inspectReadiness = inspectReadiness;
    editorInstance.renderBindBox = renderBindBox;
    editorInstance.instructionNode = instruction;
    editorInstance.bindNeeded = bindNeeded;

    editors.set(workId, editorInstance);
    return editorInstance;
  }

  async function refreshAttempts() {
    if (!activeWorkId) return;
    const ticket = serial;
    try {
      const data = await fetchAttempts(activeWorkId, ticket);
      if (!data || !valid(ticket)) return;
      attempts = data.attempts;
      visibilityFiltered = data.visibilityFiltered;
      partialAttempts = data.partial;
      latestAttempt = attempts.length > 0 ? attempts[attempts.length - 1] : null;

      const editor = editors.get(activeWorkId);
      if (editor?.locked()) {
        editor.recheck();
      }
    } catch (err) {
      if (valid(ticket)) {
        statusView.replaceChildren(errorBox(err, {within: statusView}));
      }
    }
  }

  function updateUI() {
    const editor = activeWorkId ? editors.get(activeWorkId) : null;
    renderLatestAttempt(latestAttempt);

    if (editor) {
      const isLocked = editor.locked();
      const activeAttempt = latestAttempt && (!['failed', 'cancelled'].includes(latestAttempt.status) || latestAttempt.stage === 'start_unknown');
      const show = isLocked || (canStartWorkspaces() && !activeAttempt && !partialAttempts);
      editorView.hidden = !show;
      if (editorView.firstChild !== editor.box) {
        editorView.replaceChildren(editor.box);
      }
    } else {
      editorView.hidden = true;
    }

    onStartableChange(startable());
  }

  function withdraw() {
    serial++;
    reading = false;
    prepared = null;
    attempts = [];
    latestAttempt = null;
    visibilityFiltered = false;
    partialAttempts = false;
    chosenCandidate = null;
    attemptsView.replaceChildren();
    editorView.replaceChildren();
    statusView.replaceChildren();
    updateUI();
  }

  async function setWork(card) {
    if (!card || !card.work_item_id) {
      activeCard = null;
      activeWorkId = null;
      withdraw();
      return;
    }

    const sameWork = activeWorkId === card.work_item_id;
    activeCard = card;
    activeWorkId = card.work_item_id;

    if (!sameWork) {
      prepared = null;
      chosenCandidate = null;
      attempts = [];
      latestAttempt = null;
      visibilityFiltered = false;
      partialAttempts = false;
      attemptsView.replaceChildren();
      statusView.replaceChildren();
    }

    const ticket = ++serial;
    reading = true;
    updateUI();

    try {
      // Pre-fetch work-item details if creating new editor
      if (!editors.has(card.work_item_id)) {
        defaultInstruction = '';
        try {
          const itemData = await api('GET', `/work-items/${encodeURIComponent(card.work_item_id)}`);
          if (valid(ticket)) {
            const item = itemData?.work_item;
            defaultInstruction = (item?.request || item?.goal || item?.title || '').trim().slice(0, 4000);
          }
        } catch {
          // If read fails, leave empty and explain
          defaultInstruction = '';
        }
      }

      if (!valid(ticket)) return;

      const editor = getEditor(card);
      editor?.renderBindBox();

      const attemptsData = await fetchAttempts(card.work_item_id, ticket);
      if (!valid(ticket)) return;
      if (attemptsData) {
        attempts = attemptsData.attempts;
        visibilityFiltered = attemptsData.visibilityFiltered;
        partialAttempts = attemptsData.partial;
        latestAttempt = attempts.length > 0 ? attempts[attempts.length - 1] : null;
      }

      if (editor?.locked()) {
        editor.recheck();
      }
    } catch (err) {
      if (valid(ticket)) {
        statusView.replaceChildren(errorBox(err, {within: statusView}));
      }
    } finally {
      if (valid(ticket)) {
        reading = false;
        const editor = editors.get(card.work_item_id);
        editor?.update();
        updateUI();
      }
    }
  }

  async function refresh() {
    if (!activeCard) return;
    await setWork(activeCard);
  }

  function dispose() {
    withdraw();
    for (const ed of editors.values()) {
      ed.dispose();
    }
    editors.clear();
  }

  function focus() {
    const editor = activeWorkId ? editors.get(activeWorkId) : null;
    if (editor?.instructionNode) {
      editor.instructionNode.scrollIntoView({behavior: 'smooth', block: 'nearest'});
      editor.instructionNode.focus();
    }
  }

  return {
    element: panelElement,
    setWork,
    refresh,
    withdraw,
    dispose,
    startable: () => startable(),
    focus,
  };
}
