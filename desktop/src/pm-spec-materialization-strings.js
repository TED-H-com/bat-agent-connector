const rows={
  title:['Save fixed specification artifacts','保存固定規格產物'],intro:['Save this engineering package in the owned PM worktree and as an immutable attachment for implementation.','將這份工程工作包保存至自有 PM worktree，並建立可供實作使用的不可變附件。'],
  select:['Select an engineering work package above.','請先在上方選取工程工作包。'],refresh:['Refresh specification artifacts','重新讀取規格產物'],create:['Save fixed specification','保存固定規格'],
  boundary:['This writes planning artifacts only. Starting implementation remains a separate handoff.','此處只寫入規劃產物；開始實作仍是另一個交接操作。'],
  unavailable:['The specification or its original sources are unavailable. Original requests remain saved for recovery.','規格或其原始來源目前無法讀取；原請求仍保留以供查回。'],
  invalid:['The response does not match the original specification and Git receipt.','回應與原始規格及 Git 收據不符。'],not_supported:['This connection does not advertise fixed specification materialization.','此連線未提供固定規格產物功能。'],
  reading:['Reading the fixed package, readiness and original artifacts…','讀取固定工作包、就緒狀態與原產物…'],empty:['No completed materialization is saved for this package.','這份工作包尚無已完成的產物保存紀錄。'],
  blocked:['Current scope, work or dependency checks prevent a new materialization.','目前範圍、工作或相依檢查不允許新保存操作。'],filtered:['Some original artifact records are hidden by source access. This is not an empty history.','部分原產物紀錄因來源權限而隱藏，不能視為沒有歷史。'],
  permissions:['Saving requires current workspace write membership and observe, manage and start scopes.','保存需要目前工作區寫入資格，以及 observe、manage 與 start 權限。'],
  saved:['Fixed specification saved','固定規格已保存'],unassessed:['The original Git write is verified. Implementation quality and delivery remain unassessed.','已核對原始 Git 寫入；實作品質與交付仍未評估。'],
  commit:['Verified PM commit','已核對的 PM commit'],open_artifact:['Open fixed specification attachment','開啟固定規格附件'],tasks:['Stable task identities and revisions','穩定任務身分與修訂'],
  task_help:['Task keys preserve identity through reordered text and removals. Planning markers do not prove completed implementation.','任務 key 在文字重排與移除後仍保留身分；規劃標記不代表實作完成。'],
  task:['Task history','任務歷史'],task_key:['Authored task key','撰寫時的任務 key'],task_id:['Stable task ID','穩定任務 ID'],active:['Included in this revision','納入此修訂'],removed:['Removed from this revision · history retained','已從此修訂移除 · 保留歷史'],
  proof:['Original artifact and Git proof','原附件與 Git 證據'],identity:['Materialization','產物保存紀錄'],artifact:['Specification attachment','規格附件'],revision:['Attachment revision','附件版次'],artifact_digest:['Specification attachment digest','規格附件摘要值'],files_digest:['Generated file-set digest','產生檔案集摘要值'],
};
export const pmSpecMaterializationStrings=Object.fromEntries(['en','zh-TW'].map((locale,index)=>[locale,Object.fromEntries(Object.entries(rows).map(([key,value])=>['psm_'+key,value[index]]))]));
