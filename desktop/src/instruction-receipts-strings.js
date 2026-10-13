// Instruction receipt copy is independent of the renderer and i18n initialization.
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
