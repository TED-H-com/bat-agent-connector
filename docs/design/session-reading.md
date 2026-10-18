# 對話未讀與跨入口閱讀位置

Web、Tauri、HTTP／RPC session observation 共用中央的個人閱讀狀態。鍵是中央 server、
effective principal（actor／scopes／admin digest）、完整 host／session ID。相同身分換 token
不會重設；不同身分或權限組合互相隔離。專案工作入口與獨立 session 入口使用同一份狀態。

## 訊息與閱讀證據

- `session_read` 依 BAT 原有 message ID 與完整文字摘要的 SHA-256 建立 metadata index，
  中央只存 ID／digest／順序，不複製另一份對話。工具呼叫不計入對話未讀數，也不會取得 human read receipt。
  `include_tools=true` 時工具佔用頁面 offset；回傳的位置以該次請求的 filter 計算，
  不借用另一個並行請求的頁碼。
- 同一 ID 的文字改變會有新 revision，舊 receipt 不會清除新版未讀。沒有唯一穩定 ID
  的訊息仍可閱讀，但不虛構閱讀 ID，`complete` 為 `false` 並於 `incomplete_reasons` 記為 `message_ids`。
- `messages` 回傳個別 `reading={revision,unread,can_mark}` 與整體 `reading`；後者包含
  `unread_count,known_count,complete,observed_at,position`，以及隨當次讀取回傳的
  `incomplete_reasons`（由 `archive_unconfirmed`、`archive_budget`、`message_ids` 組成）與
  缺少唯一字串 ID 的可見訊息數 `unindexed_count`（`> 0` 時附帶）。列表／詳情／project work／bootstrap
  也讀取同一狀態。沒有觀察過的 session 回傳 null 未讀數，不冒稱為零。
- 數字是**上次觀察的訊息版本**，不是 BAT 即時精確總數。每次觀察保留原有 deadline 與
  archive 3,000 raw items 掃描上限：BAT 省略 `hasMore`、回傳非物件或方法不可用時記為
  `archive_unconfirmed`；掃描達到 3,000 則上限記為 `archive_budget`；缺 ID 或有重複 ID
  記為 `message_ids` 並附 `unindexed_count`。完整讀取的 `incomplete_reasons` 為 `[]`。
  對話列與工作階段詳情依原因安靜提示（僅 `message_ids` 且 archive 完整時顯示 `N+` 未讀下界並在詳情說明
  部分訊息無 ID；`archive_budget` 與 `archive_unconfirmed` 也保留 `N+` 未讀下界，分別附顯示最近 3,000 則訊息與較早訊息可能未載入
  並在詳情說明 BAT 未確認歷史結尾；舊版 central 未附原因時顯示中性提示），不使用錯誤色或「部分紀錄」字樣。
  BAT live／archive 不是 atomic snapshot；不從 idle、時間、event ACK 推算完整對話。
- 觀察請求在網路 I/O 前取得中央遞增序號。較舊請求晚回來仍可顯示其原版本，但不得
  回寫較新的 index／順序。已顯示的舊 revision 仍可提交自己的 receipt。

`session.read` 是 observe scope 的 registered durable action。target 為完整 host／session_id；
params 為 1–100 個 `{message_id,revision}`，中央必須曾觀察該 revision。只記錄明確列出的
訊息，不使用 high-water cursor 清除中間未看過的歷史。重試原 operation/key 保留同一 receipt；
effect 與 receipt 同一交易，其他 principal 不可借同 actor replay／resume／cancel。

共用介面只有點「標記畫面中的訊息已讀」才送出，當時不在可見 viewport 的訊息不會被包含。
尚被文字長度截斷的訊息不可標記。畫面讀取、捲動、回到最新、載入較早訊息與事件 ACK
都不會新增 read receipt，也不解除 pending、完成確認或改動 BAT 資源。

## 閱讀位置

「記住閱讀位置」透過 `session.position` 保存第一個可見訊息的原 ID、revision、像素 offset。
它同樣只需 observe，使用 `expected_version` 防止另一入口的較舊意圖覆寫新位置；保存位置
不會標記訊息已讀。資料留在中央，關閉 UI、重啟中央與 token 輪替後保留。

進入同一 session 時，使用中央目前的 message offset 讀取包含 anchor 的視窗並恢復位置；
找不到原訊息時保留明確提示，不猜替代訊息。使用者可直接回到最新或載入較早歷史。
刷新保持原文字選取、焦點、對話 anchor 與輸入草稿；其他入口保存的位置不會在當前閱讀中
突然拉動捲軸。操作完成後讀回中央狀態，不以 accepted 代替成功。

新表是冪等 DDL，不配置新的 `user_version`，不搬動 Connector 自有 IDs／歷史。
測試涵蓋 actual HTTP 與 MockBat、principal 隔離、token rotation、skipped messages、內容更新、
晚到 observation、journal restart、原 key replay、位置競爭，以及共用 Web／IPC 操作與草稿保存。

## 訊息來源與工具細節

Dashboard 使用同一中央 transcript 載入工具列；已完成的工具輸入／結果預設折疊，
error、running、denied、deferred 與未知狀態初次出現或狀態改變時展開；同狀態刷新保留使用者的收合選擇。狀態一直顯示在訊息表頭。
保留原工具結果文字，輸入摘要與整份訊息仍受既有長度限制；工具紀錄不參與未讀標記。

原 BAT `ClaudeMessage` 契約通常只提供 role/content/timestamp，不能從 session 當前模型
推算歷史訊息的 agent、model 或耗時。中央僅保留原訊息本身明確提供且有效的
`agent`、`model`、`durationMs`；工具耗時只由其真實 timestamp/completedAt 計算。
缺少的欄位不補值，也不把訂閱額度或 session 累計 token/cost 當成單則訊息資料。

共用對話視窗依實際收到的訊息數顯示標題，有較早頁才標示「最近」。空視窗使用空狀態。
捲離底部而沒有新內容時顯示「回到最新訊息」；有新增或串流修訂才顯示「新訊息」。
回到最新會把鍵盤焦點放回對話；置底閱讀在視窗縮放、回覆區高度改變及自己的成功送出後保持置底。
程式設定捲動位置後排入的 scroll event，不得因稍晚載入的操作區改變可見高度而被當成使用者捲動。
真正離開底部閱讀時仍保留原位置；此判斷不增加 read receipt。
人工 BAT 工作階段中的 user 回合標為「使用者」，不推定為目前登入者。

手機版工作階段標題與中繼資料使用完整頁首寬度，操作按鈕排在下一列。
較寬的字型不應讓標題擠成過高的窄欄，導致待回答問題的捲動區放不下完整輸入框。

Session 詳情讀取回傳 403／404／410 時，整個工作空間撤回：對話、標題狀態、
交付、檢查點與詳情一起移除，只保留返回入口；輸入草稿及原操作 key 不刪除。
延遲的訊息或交付回應不得重新顯示已撤回資料；尚未完成的複製操作失敗時，也不能
再放回舊原文的手動複製欄位。撤回後停止自動重讀，新的 session
事件才可建立新的有效觀察。暫時讀取失敗依 1、2、4 秒直到 30 秒退避，
未確認的事件重播也遵守同一退避；讀取失敗不假裝事件已完成處理。
訊息窗超過大小限制時先以 10 則重讀，仍失敗則在對話內顯示重試入口。

回答前重新讀取工作階段也遵守同一撤回規則。若讀取期間曾撤回，舊回應不得
覆蓋後來的新觀察，也不得接著送出回答；已返回的新畫面不能授權先前的查核。
撤回狀態的錯誤代碼與 HTTP 狀態留在「錯誤詳細資料」，預設只顯示在地化說明與返回。
