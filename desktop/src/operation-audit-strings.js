export const operationAuditStrings = {
  en: {
    audit_result_title: 'Audit result could not be saved',
    audit_result_accepted: 'This request was accepted. Its audit result could not be written; the original receipt is saved. Do not send it again.',
    audit_result_error: 'The request returned an error. The original error is saved, but its audit result could not be written. Read this operation before deciding what to do next.',
    audit_result_unknown: 'The request may have been sent. Its result was unknown when the audit result could not be written. Read this operation’s current receipt; do not resend.',
    audit_result_repair: 'Check the original operation and repair the audit storage.',
  },
  'zh-TW': {
    audit_result_title: '稽核結果未能儲存',
    audit_result_accepted: '此請求已被接受，但稽核結果未能寫入；原回執已保留。請勿再次傳送。',
    audit_result_error: '此請求已回覆錯誤。原錯誤已保留，但稽核結果未能寫入。請先查看此操作，再決定後續處理。',
    audit_result_unknown: '請求可能已送出；稽核結果未能寫入時，結果尚未確定。請查看此操作目前的回執，勿重新傳送。',
    audit_result_repair: '請查看原操作並修復稽核儲存。',
  },
};
