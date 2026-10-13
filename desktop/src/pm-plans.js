import {discussionLabel} from './pm-discussions.js';
import {planComparison} from './pm-plan-diff.js';
import {messageBlocks} from './message-format.js';

// Text-only document structure; no HTML, URL loading or executable task checkboxes.
export function planDocument(h, markdown) {
  const body = h('div', {class: 'pm-plan-document pm-plan-reading'});
  for (const block of messageBlocks(markdown || '')) {
    if (block.kind === 'code') {body.append(h('pre', {}, h('code', {}, block.text))); continue;}
    if (block.kind === 'table') {
      body.append(h('div', {class: 'message-table', tabindex: '0'}, h('table', {},
        h('thead', {}, h('tr', {}, ...block.header.map(cell => h('th', {scope: 'col'}, cell)))),
        h('tbody', {}, ...block.rows.map(row => h('tr', {}, ...row.map(cell => h('td', {}, cell))))))));
      continue;
    }
    let paragraph = [], list = null;
    const flush = () => {if (paragraph.length) body.append(h('p', {}, paragraph.join('\n'))); paragraph = [];};
    for (const line of block.text.split(/\r?\n/)) {
      const heading = /^#{1,6}\s+(.+)$/.exec(line), bullet = /^\s*[-*+]\s+(.+)$/.exec(line);
      if (heading) {flush(); list = null; body.append(h('h4', {}, heading[1]));}
      else if (bullet) {flush(); if (!list) {list = h('ul'); body.append(list);} list.append(h('li', {}, bullet[1]));}
      else if (!line.trim()) {flush(); list = null;}
      else {list = null; paragraph.push(line);}
    }
    flush();
  }
  return body;
}

// Immutable central plan revisions, displayed beside the source discussion.
export function planRevisions({h, t, api, alive, workspaceId, threadId, thread, form, input, labelled, permitted, refresh, errorBox}) {
  let head = null, capability = null, signature = '', busy = false;
  const title = input('pm_revision_title', {max: 200, value: thread().thread.title});
  const markdown = input('pm_revision_markdown', {multiline: true, max: 128000, required: false});
  const structure = h('input', {type: 'hidden', value: '{}'}); structure.dataset.pmDefault = '{}';
  const editor = form({action: 'pm.plan.draft', target: {workspace_id: workspaceId, thread_id: threadId},
    fields: {title, markdown, structure}, label: 'pm_revision_save',
    params: values => ({title: values.title, ...(values.markdown.trim() ? {markdown: values.markdown} : {}),
      ...JSON.parse(values.structure || '{}'), previous_revision_id: head.latest_revision_id}),
    preconditions: () => ({expected_version: thread().thread.version, expected_plan_version: head.version}),
    permitted: () => Boolean(head) && permitted(false) && thread().thread.mode === 'planning',
    onSaved: () => refresh(), extra: h('div', {class: 'pm-fields'}, labelled('pm_revision_title', title),
      labelled('pm_revision_markdown', markdown), structure, h('p', {class: 'muted'}, t('pm_revision_draft_help')))});
  const draftBox = h('details', {'data-pm-revision-editor': ''}, h('summary', {}, t('pm_revision_new')), editor.box);
  const current = h('div', {'data-pm-accepted-plan': ''}), rows = h('div', {class: 'pm-revision-list'});
  const box = h('section', {class: 'pm-plans', 'data-pm-plans': ''}, h('h3', {}, t('pm_plan_versions')), current, draftBox, rows);
  const comparison = planComparison({h, t, api, alive, workspaceId, errorBox});
  box.append(h('details', {'data-pm-plan-comparison': ''}, h('summary', {}, t('pm_compare_plans')), comparison.box));
  const endpoint = `/pm/workspaces/${encodeURIComponent(workspaceId)}/plans`;

  function revisionRow(summary) {
    const detail = h('div'), item = h('details', {'data-pm-revision': summary.revision_id},
      h('summary', {}, t('pm_revision_number', {number: summary.sequence}),
        summary.revision_id === head.accepted_revision_id ? ' · ' + t('pm_plan_accepted') : ''), detail);
    let pending = false, fetched = false;
    item.addEventListener('toggle', async () => {
      if (!item.open || pending || fetched) return;
      pending = true;
      try {
        const data = await api('GET', `${endpoint}/${encodeURIComponent(summary.revision_id)}`);
        alive(); if (!item.isConnected) return;
        const plan = data.plan;
        if (plan?.workspace_id !== workspaceId || plan?.revision_id !== summary.revision_id) throw Error(t('pm_scope_mismatch'));
        const document = plan.document, source = h('div', {class: 'pm-plan-domains'});
        for (const [domain, section] of Object.entries(document.sections || {})) {
          source.append(h('details', {}, h('summary', {}, discussionLabel(t, 'pm_slot_', domain)),
            ...(section.statements || []).map(statement => h('p', {},
              h('span', {class: 'chip'}, discussionLabel(t, 'pm_status_', statement.status)), ' ', statement.value,
              h('small', {class: 'muted pm-source'}, [statement.source_ref?.ref || statement.source_ref?.message_id,
                statement.source_ref?.path, statement.source_ref?.revision].filter(Boolean).join(' · ')))),
            section.coverage === 'unresolved' ? h('p', {class: 'muted'}, t('pm_plan_domain_unknown')) : ''));
        }
        const titleInDocument = document.markdown?.split(/\r?\n/, 1)[0] === '# ' + document.title;
        detail.replaceChildren(titleInDocument ? '' : h('h4', {}, document.title), planDocument(h, document.markdown),
          h('details', {}, h('summary', {}, t('pm_plan_sources')), source));
        if (document.requirements?.length) detail.append(h('section', {class: 'pm-plan-requirements'},
          h('h4', {}, t('pm_plan_acceptance')), ...document.requirements.map(requirement => h('details', {},
            h('summary', {}, requirement.text || requirement.id),
            h('ul', {}, ...(requirement.acceptance || []).map(criterion => h('li', {}, criterion.text))),
            h('code', {class: 'pm-source'}, requirement.id)))));
        if (plan.adoption) detail.append(h('p', {}, t('pm_plan_adoption_evidence', {actor: plan.adoption.adopted_by}),
          h('code', {}, plan.adoption.commit_sha)), h('p', {class: 'pm-note-text'}, plan.adoption.rationale));
        if (plan.thread_id === threadId) detail.append(h('button', {type: 'button', class: 'secondary', disabled: editor.locked() || !permitted(false),
          onclick: () => {
            if (editor.locked() || !permitted(false)) return;
            editor.set('title', document.title); editor.set('markdown', document.markdown);
            editor.set('structure', JSON.stringify({sections: Object.fromEntries(Object.entries(document.sections || {}).map(([key, value]) => [key, value.source_input_ids])),
              requirements: (document.requirements || []).map(requirement => ({id: requirement.id, source_input_id: requirement.source_input_id,
                acceptance_input_ids: requirement.acceptance.map(value => value.source_input_id), priority: requirement.priority, depends_on: requirement.depends_on}))}));
            draftBox.open = true; markdown.focus();
          }}, t('pm_revision_edit')));
        if (permitted(true)) {
          const rationale = input('pm_adopt_rationale', {multiline: true, max: 8000});
          const baseline = data.head.accepted_revision_id;
          let adopt, adoption;
          adopt = form({action: 'pm.plan.adopt', target: {workspace_id: workspaceId, revision_id: plan.revision_id},
            fields: {rationale}, params: values => ({rationale: values.rationale}),
            preconditions: () => ({expected_accepted_revision_id: baseline}),
            permitted: () => !plan.is_current_accepted && permitted(true) && capability?.plan_adoption_available === true
              && head.accepted_revision_id === baseline && plan.base_accepted_revision_id === baseline,
            recoverPermitted: () => permitted(true), label: 'pm_adopt_revision', onSaved: () => refresh(),
            onStateChange: () => {if (adopt) {
              adopt.box.hidden = plan.is_current_accepted && !adopt.locked();
              if (adoption) {adoption.hidden = adopt.box.hidden; if (adopt.locked()) adoption.open = true;}
            }},
            extra: h('div', {}, labelled('pm_adopt_rationale', rationale), h('p', {class: 'muted'},
              t(plan.base_accepted_revision_id !== baseline ? 'pm_plan_baseline_changed'
                : capability?.plan_adoption_available ? 'pm_adopt_help' : 'pm_plan_worktree_required')))});
          adoption = h('details', {'data-pm-plan-adopt': '', open: adopt.locked()}, h('summary', {}, t('pm_adopt_revision')), adopt.box);
          detail.append(adoption);
          adopt.update();
        }
        fetched = true;
      } catch (error) {if (item.isConnected) detail.replaceChildren(errorBox(error));}
      finally {pending = false;}
    });
    return item;
  }

  function renderRows(page, append = false) {
    if (!append) rows.replaceChildren();
    rows.append(...(page.revisions || []).map(revisionRow));
    if (!rows.children.length) rows.append(h('p', {class: 'muted'}, t('pm_plan_no_revisions')));
    if (page.next_cursor !== null && page.next_cursor !== undefined) {
      const more = h('button', {type: 'button', class: 'secondary', onclick: async () => {
        if (busy) return;
        busy = true; more.disabled = true;
        try {
          const next = await api('GET', `${endpoint}?after=${page.next_cursor}&limit=20`); alive();
          if (next.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
          if (!more.isConnected) return;
          more.remove(); renderRows(next, true);
        } catch (error) {if (more.isConnected) more.after(errorBox(error));}
        finally {busy = false; more.disabled = false;}
      }}, t('pm_plan_more'));
      rows.append(more);
    }
  }

  return {box, update(page, accepted, capabilities) {
    if (page.workspace_id !== workspaceId || accepted.head.workspace_id !== workspaceId) throw Error(t('pm_scope_mismatch'));
    if (page.head.accepted_revision_id !== accepted.head.accepted_revision_id) throw Error(t('pm_stale'));
    head = page.head; capability = capabilities; comparison.update(page);
    draftBox.hidden = thread().thread.mode !== 'planning' && !editor.locked();
    const nextSignature = JSON.stringify([page, accepted, capabilities]);
    if (signature !== nextSignature) {
      signature = nextSignature;
      current.replaceChildren(h('p', {}, h('strong', {}, accepted.plan ? accepted.plan.document.title : t('pm_plan_none_accepted'))));
      if (accepted.plan) current.append(h('p', {class: 'muted'}, t('pm_revision_number', {number: accepted.plan.sequence}), ' · ', t('pm_plan_accepted')));
      if (accepted.plan?.adoption?.commit_sha) current.append(h('details', {}, h('summary', {}, t('pd_details')), h('code', {}, accepted.plan.adoption.commit_sha), h('button', {type: 'button', class: 'secondary', onclick: async event => {const details = event.currentTarget.parentElement; try {await navigator.clipboard.writeText(accepted.plan.adoption.commit_sha);} catch (error) {if (details.isConnected) details.append(errorBox(error));}}}, t('pd_copy'))));
      renderRows(page);
    }
    editor.update();
  }};
}
