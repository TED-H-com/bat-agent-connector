import {iconButton, emptyState} from './ui/components.js';
// Instruction receipts describe central sends, never a provider queue position.
const oid = v => typeof v === 'string' && /^op_[0-9a-f]{32}$/.test(v);
const phases = new Set(['accepted', 'not_accepted', 'unconfirmed', 'operation_cancelled', 'operation_failed', 'pending']);

export const instructionReceiptStrings = {
  'en-US': {instruction_receipts: 'Sent instructions', instruction_receipts_note: 'These are central send receipts. “Was queued” records submission history; it does not show a current queue position. Interrupt affects the session.',
    instruction_accepted: 'Accepted by BAT', instruction_not_accepted: 'Not accepted', instruction_unconfirmed: 'Acceptance unconfirmed',
    instruction_operation_cancelled: 'Central operation cancelled', instruction_operation_failed: 'Central operation failed', instruction_pending: 'Send pending',
    instruction_was_queued: 'Was queued at submission', instruction_requested_queue: 'Queueing requested', instruction_operation: 'View original operation',
    instruction_empty: 'No central send receipts for this session.', instruction_more: 'Older instructions', instruction_refresh: 'Refresh receipts',
    instruction_stale: 'Receipts could not be refreshed. Previously loaded records remain visible.', instruction_excerpt: 'Preview only; open the operation for the full instruction.'},
  'zh-TW': {instruction_receipts: '已送出指示', instruction_receipts_note: '這裡顯示中央送出紀錄。「曾排隊」是提交當時的紀錄，不代表目前排隊位置。中斷會作用於整個工作階段。',
    instruction_accepted: 'BAT 已接受', instruction_not_accepted: '未被接受', instruction_unconfirmed: '尚未確認接受',
    instruction_operation_cancelled: '中央操作已取消', instruction_operation_failed: '中央操作失敗', instruction_pending: '等待送出結果',
    instruction_was_queued: '提交時曾排隊', instruction_requested_queue: '已要求排隊', instruction_operation: '查看原操作',
    instruction_empty: '這個工作階段尚無中央送出紀錄。', instruction_more: '較早的指示', instruction_refresh: '重新讀取紀錄',
    instruction_stale: '無法更新紀錄，保留上次讀取的內容。', instruction_excerpt: '這裡是內容預覽；完整指示請查看原操作。'}
};

export function instructionReceiptReader({api, guard, host, sessionId}) {
  let pages = 1, saved = null, pending = null;
  async function load(older = false) {
    guard();
    if (pending) return pending;
    const wanted = Math.min(5, pages + (older && saved?.next_cursor ? 1 : 0));
    pending = (async () => {
      const rows = new Map();
      let cursor = null, readAt = null, loaded = 0;
      do {
        const query = new URLSearchParams({limit: '30', ...(cursor ? {cursor} : {})});
        const doc = await api('GET', `/sessions/${encodeURIComponent(host)}/${encodeURIComponent(sessionId)}/instructions?${query}`);
        guard();
        if (doc?.version !== 1 || doc.host !== host || doc.session_id !== sessionId || doc.live_queue_available !== false ||
            doc.per_message_cancel !== false || !Array.isArray(doc.instructions) || doc.instructions.length > 30 ||
            doc.next_cursor !== null && typeof doc.next_cursor !== 'string') throw new Error('Invalid instruction receipts');
        for (const row of doc.instructions) {
          if (!oid(row?.operation_id) || row.host !== host || row.session_id !== sessionId || !phases.has(row.phase) ||
              row.queue_position !== null || row.per_message_cancel !== false || typeof row.text_excerpt !== 'string' ||
              rows.has(row.operation_id)) throw new Error('Invalid instruction identity');
          rows.set(row.operation_id, structuredClone(row));
        }
        loaded++; cursor = doc.next_cursor; readAt = doc.read_at;
      } while (cursor && loaded < wanted);
      guard(); pages = loaded;
      saved = {instructions: [...rows.values()], next_cursor: cursor, read_at: readAt, can_load_more: Boolean(cursor) && pages < 5};
      return structuredClone(saved);
    })();
    try {return await pending;} finally {pending = null;}
  }
  return {load, snapshot: () => {guard(); return structuredClone(saved);}};
}

export function instructionReceiptPanel({h, t, when, api, guard, host, sessionId, errorBox}) {
  const reader = instructionReceiptReader({api, guard, host, sessionId});
  const list = h('div'), status = h('div', {role: 'status'});
  const more = h('button', {type: 'button', class: 'mini', hidden: true, onclick: () => refresh(true)}, t('instruction_more'));
  const reload = iconButton({icon:'refresh-cw', label:t('instruction_refresh'), ...{type: 'button', class: 'mini', onclick: () => refresh()}});
  const box = h('details', {class: 'workspace-evidence', 'data-instruction-receipts': ''}, h('summary', {}, t('instruction_receipts')),
    h('p', {class: 'muted'}, t('instruction_receipts_note')), list, status, h('div', {class: 'actions'}, reload, more));
  const nodes = new Map();
  let disposed = false, refreshQueue = Promise.resolve();
  const alive = () => {if (disposed) return false; try {guard(); return true;} catch {return false;}};
  function refresh(older = false) {
    // An event arriving during a read needs a subsequent snapshot before its ACK.
    const work = refreshQueue.catch(() => {}).then(() => refreshNow(older));
    refreshQueue = work;
    return work;
  }
  async function refreshNow(older = false) {
    if (!alive()) return;
    reload.disabled = more.disabled = true;
    try {
      const value = await reader.load(older); if (!alive()) return;
      const retained = new Set();
      for (const row of value.instructions) {
        let entry = nodes.get(row.operation_id);
        const signature = JSON.stringify(row);
        if (!entry) {entry = {node: h('article', {class: 'msg', 'data-instruction-operation': row.operation_id}), signature: null}; nodes.set(row.operation_id, entry);}
        if (entry.signature !== signature) {
          entry.node.replaceChildren(...[h('p', {class: 'muted'}, t('instruction_' + row.phase), ' · ', when(row.created_at * 1000)),
            h('div', {class: 'message-body'}, row.text_excerpt),
            row.was_queued === true ? h('p', {class: 'muted'}, t('instruction_was_queued')) : null,
            row.queue_requested && row.was_queued === null ? h('p', {class: 'muted'}, t('instruction_requested_queue')) : null,
            row.text_truncated ? h('p', {class: 'muted'}, t('instruction_excerpt')) : null,
            h('a', {href: `#/op/${row.operation_id}`}, t('instruction_operation'))].filter(Boolean));
          entry.signature = signature;
        }
        retained.add(entry.node);
      }
      for (const node of [...list.childNodes]) if (!retained.has(node)) node.remove();
      let position = list.firstChild;
      for (const row of value.instructions) {const node = nodes.get(row.operation_id).node;
        if (node === position) position = position.nextSibling; else list.insertBefore(node, position);}
      if (!value.instructions.length) list.replaceChildren(emptyState({title:t('instruction_empty')}));
      for (const [id, entry] of nodes) if (!retained.has(entry.node)) nodes.delete(id);
      status.replaceChildren(); more.hidden = !value.can_load_more;
    } catch (error) {if (alive()) status.replaceChildren(h('p', {class: 'muted'}, t('instruction_stale')), errorBox(error));}
    finally {if (alive()) reload.disabled = more.disabled = false;}
  }
  return {box, refresh, dispose() {disposed = true;}};
}
