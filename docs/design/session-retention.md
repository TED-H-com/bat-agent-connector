# Connector 保存的 session 對話

2026-11-05，`feat/session-retention-20261105` 的未合併增量契約。
對應既有 E04／E08／E10 與 EC-1.1 T3／T4 的資料保全和歷史閱讀，並沿 T6／E09
補明確請求的 PM 接續摘要；不代表
原生 CLI 完整收集、PM 判讀、E00–E11／R0–R5／A01–A40 或 PM v1.2 已完成。
沒有發布、安裝、真實 BAT／模型或實體裝置驗收聲明。

Python Connector／Task Service 在自己的 SQLite journal 保存已取得的 BAT 對話文字。
Dashboard 是閱讀入口，不直接寫 SQL，也不必保持開啟。保存內容是來源證據；
session 離線、清單不再列出或正文已保存，都不能證明程序已被終止或工作已完成。

## 收集範圍與生命週期

`SessionArchive` 與 inventory 同屬 daemon，持有既有 owner lease 才可收集及提交。
每 60 秒檢查各已設定 host 是否有可開始的新一輪；同一 host 不重疊執行，
每輪選上次嘗試最早的最多四個 `sessions_observed` 完整 session ID。
歷史上已觀察、現在消失的 ID 仍會嘗試 BAT archive；不只收集目前清單或 UI 選中的 session。
先前完全沒有觀察到、也沒有可讀來源的 session 無法憑空重建。

每次使用自己的 `Fleet(read_only=True, idle_timeout=0)`，沿用 state 安全判斷與
confinement journal，不啟動、不 resume、不送訊息或改動人工／unknown session 和工作樹。
單一 session wall deadline 為 30 秒；live state 最多 5 秒、archive 最多 20 秒，
前後 metadata 查核各最多 2.5 秒，避免其中一路阻擋另一路保存。
host 間獨立執行；結束或取消時關閉自己的 Fleet，daemon 停止時取消並回收背景工作。

先讀來源 metadata；安全時也讀可用 live state。即使 live terminal 不在，仍可讀 archive。
提交前重新確認來源 native ID、目前 host binding 及 owner lease；來源身分改變不能
覆寫另一個身分的資料。source 不可用只更新嘗試時間與狀態，保留先前正文、版本與成功 cursor。

## 有界回填與覆蓋資訊

每輪讀取最新 200 筆 archive，再按已保存的 cursor 回填一頁較早的 200 筆。
若有前輪 anchor，先讀回該頁並核對完整頁 digest；BAT offset 從最新訊息往回計數，
因此依確認的新增筆數位移。新增量超出最新一頁、總數縮減、anchor 改變或無法確認時，
重新開始有界掃描，不能直接跳過中間可能未保存的區段。
每輪至多讀最新頁、anchor 驗證頁與一頁回填，不因長歷史阻塞整個 daemon。

頁面必須有一致的 `messages`、整數 `total` 與 boolean `hasMore`，同輪頁面總數亦須一致。
不符合時不推進成功 cursor。archive 後續頁失敗但最新頁或 live 文字可讀時，仍可保存已成功取得的文字，
並保留舊回填 cursor、標為 `archive_unconfirmed`。較早頁待補時標為 `backfill_pending`。

保存範圍為 `observed_user_assistant_text`：

- 只保存具有唯一穩定原 message ID 的 user／assistant 文字；不為缺少或重複 ID 的內容製造 ID。
- 不保存工具呼叫／結果、附件 bytes、私密思考或完整原始協定 payload。
- 每則最多 16,000 個文字 codepoint；單次正規化批次的序列化資料最多 2 MiB。
  live state 每輪最多處理最後 1,000 筆。被截短或省略的來源資料明列覆蓋限制。
- 每個來源身分的 snapshot 最多 20,000 個 message ID；所有正文 revisions、snapshot manifests
  與摘錄共用 256 MiB 的序列化儲存預算。這不是整個 SQLite 檔案或所有 hosts 的磁碟上限。
  達上限標為 `storage_limit`，不刪舊資料，也不推進成功 cursor。

`coverage.history_complete` 與 `atomic_source_snapshot` 均為 `false`。
`source_end_observed=true` 只表示本輪 BAT archive 確認結尾，不能提升為完整歷史備份。
`omitted_messages`／`clipped_messages` 是本輪來源出現次數，
`counts_basis=latest_capture_pass_source_occurrences`，不是歷年遺失總數。
`generation_known` 揭露是否有原生 session ID 證據；未知時不能猜測 incarnation。
來源未曾提供、超限或不可辨識的內容，摘要也不能補回。

## SQLite 身分、不可變版本與交易

資料鍵包含 host、完整 session ID 和 source binding digest。binding 由目前 host binding、
已觀察的 provider native session ID、registry origin 與 agent kind 組成；
host 別名相同、路徑相同或標題相同均不轉移舊內容。
卸載後暫缺 native ID 時，只在相同 host binding、creation 和 agent kind 下沿用最後保存的
已確認來源；一旦觀察到不同 native ID 即分開保存，不能因 metadata 缺失遺失原本的閱讀入口。
讀取亦須核對目前 discovery binding；來源被移除或重新綁定時，不能用舊 digest 繞回歷史。

新增表使用冪等 DDL，不配置 `user_version` 或新增 data migration step：

| 表 | 用途 |
| --- | --- |
| `session_archive_sources` | 該來源的最新 snapshot、成功回填 cursor、嘗試／保存時間與可用狀態。 |
| `session_archive_messages` | 以 message ID＋revision 保存的不可變正規化文字正文。 |
| `session_archive_snapshots` | 依序列出的 exact message ID＋revision、identity、coverage 與摘錄。 |

`source_digest` 雜湊截短前的正規化文字訊息；`revision` 雜湊保存正文、來源 digest 與截短標記。
它不是原始 BAT payload 的 byte hash，也不同於個人閱讀 index 的 revision。
`snapshot_id` 是含有序 members、identity 與 coverage 的 canonical JSON SHA-256；
JSON 使用 UTF-8、排序 keys、無多餘空白。較早 snapshot 始終解析其原始 message revisions，
不因相同 ID 的新內容而改變。重複觀察相同內容不新增正文 revision 或相同 snapshot。
正文與 snapshot 表拒絕 UPDATE／DELETE；來源指標可更新。

新正文 revisions、snapshot manifest、摘錄、最新指標與成功 cursor 在同一 SQLite transaction
提交。不能先把 cursor 推過尚未保存的文字，也不能以摘要失敗或來源失敗刪掉最後成功版本。
本增量沒有 TTL、背景清除或刪除操作；容量用停止新增處理，不自動淘汰歷史。
整體磁碟配額、操作者明確匯出／刪除政策及更大的歷史容量是後續工作。

## 有來源的摘錄與 PM 邊界

每份 snapshot 同時保存 `brief`：`kind=extractive`、
`extractor_version=session-excerpts/v1`、`source_snapshot=<snapshot_id>`、
`model=null`、`execution_status=unknown`。只有兩種可選摘錄：

- `latest_request`：此保存版本最後一則 user 文字。
- `latest_response`：此保存版本最後一則 assistant 文字。

每則帶原 `message_id`、exact `revision`、至多 800 個 codepoint 的 `text` 與 `clipped`。
缺少某個 role 就省略該項；兩項不保證是同一個 request／response pair。
這些文字可能包含建議、錯誤或未確認主張，不能被改標為已採用決定、已完成工作或驗收證據。
背景保存及摘錄沒有模型呼叫或 embedding 費用。

語意摘要透過既有已設定、已授權的 `pm.continuation.prepare` 明確請求，
可帶 `retained_session_ref` 選定一份保存版本。目標、曾討論的選項、問題與可能待辦
沿用 [continuation brief](pm-continuation-brief.md) 的版本 1 契約和 SQL run／receipt，
不另建摘要權威、模型排程器或自動費用。背景摘錄與模型摘要的版本分開保留。
語意判讀與摘錄都不代替 PM 全生命週期。
全文搜尋、跨 session memory 合併、長期 persona 與自動注入 agent context 不在本增量內。

### 固定保存版本的語意接續

讀取回應提供完整 `retained_session_ref`：`version=1`、`server_id`、`host`、
完整 `session_id`、`binding` 和 `snapshot_id`。呼叫端原樣保存，不從路徑、標題、
目錄或目前 UI 選取猜測來源身分、工作區關係或所有權；只提供 `latest`、短 ID 或
不同 Connector／binding 的 reference 均不能使用。

admission 從選定 snapshot 的 immutable members 讀取最多最新 20 則、合計最多
16,000 個文字 codepoint，並依現有 PM context 的 2,000–64,000-character 上限
再保留可放入的最新連續區段。使用保存的完整正文，不使用閱讀頁面的 display clipping；
此步不再截短正文。至少一則保存訊息及固定 PM 決策放不下時，在模型送出前拒絕。
凍結 context 保存實際入選的 `message_id`＋`revision` 順序、selection digest、
原 snapshot、保存時間、coverage、省略數與 `execution_status=unknown`。
新增 capture 或相同 ID 的新正文都不能改寫已凍結的來源。

每則模型引用是 `retained_session_message`，帶 exact snapshot／message revision，
沒有偽造的 operation ID，disposition 固定為 `context`。過去對話聲稱「決定」或
「完成」仍是來源主張；已採用／排除決策繼續由中央 PM 狀態供給。
只引用保存對話不能產生 `completed_evidence`，且 partial coverage 禁止
`no_remaining_work`。模型解讀始終標示未經語意驗證。

每次 derived run、brief、operation、event 或後續接續讀取，仍須通過目前 observe、
工作區與原 session 的 PM source lineage 權限；送出與 import 沿既有 final-frame checks。
當前 Connector config 的 host binding 由 connection-local callback 即時查核，
不等下一次 inventory scan 才撤銷。來源離線仍可摘要已保存文字；移除／改綁 host、
觀察到另一原生 session 身分或原權限撤回則來源不可用，不回退成舊的可見摘要。

## HTTP、RPC／MCP 與目前權限

共用既有 `session_read`，加入 `source=retained`；HTTP 為：

```text
GET /api/v1/sessions/{host}/{full_session_id}/messages?source=retained
GET /api/v1/sessions/{host}/{full_session_id}/messages?source=retained&snapshot_id={digest}&offset={next_offset}
```

省略 `snapshot_id` 時選最新保存版本；後續帶回其 digest，使用回應 `next_offset`。
offset 從選定版本的最新訊息向前計數，各頁仍按保存的來源順序呈現。
數值限制沿用 [session observation](session-observation.md)：最多 100 則、60,000 characters，
單則另受 `max_message_chars` 限制。版本清單最多回傳最近 50 份；已知較早 digest 仍可明確讀取。
回應包含 `source=retained`、`snapshot_id`、保存時間、上次查核時間、`source_status`、
`coverage`、`brief`、`messages`、`retained_session_ref` 與分頁資訊，
不把歷史填成 live pending／turn result。

retained 不接受 `after`；live 不接受 `snapshot_id`。`session_wait` 繼續走既有 live 路徑。
完整 session ID、observe scope、目前 host／source binding 及既有 `require_session` 的
PM source membership／lineage 檢查同時適用於正文、摘錄及版本列表，回傳前再驗 token／principal。
retained 讀取不連 BAT、不使用其他 admin credential、不更新個人閱讀 receipt／位置。
沒有保存資料回 `ARCHIVE_NOT_FOUND`；舊版本不得藉讀取最新值悄悄替換。

BAT 來源離線且目前授權仍成立時，可讀先前保存版本並顯示來源不可用。
**Connector 本身離線仍無法讀這個中央保存庫**；Web／Tauri 沒有持久正文副本或本機旁路。
撤回權限與暫時來源離線不同，不能因為「已保存」繼續展示不再獲准的內容。

## 共用前端

Web／Tauri 使用同一收合的「已保存的對話／Saved conversation」面板。
使用者讀取後釘選 snapshot；較早／較新頁、版本選單與「此版本的最新訊息」
只在保存面板內操作。新 capture 不移動正在閱讀的固定版本。
展開且已選版本時，每 30 秒重讀同頁驗證存取；同一頁不重建正文，保留文字選取。
錯誤、失去權限、來源切換、關閉面板或離開 session 會清除保存面板中的正文與摘錄，
延遲回應不得把它們放回。顯示保存時間、文字範圍及不完整原因，英文與 zh-TW 對齊。

保存面板不能重設 live transcript、turn marker、pending、event ACK、read receipt、
輸入草稿或原 operation key，也不讓歷史文字授權新的 send／start／resume。

共用 PM 接續表單可選擇一份保存來源，再使用原 durable action／已設定 adapter
請求摘要；選取、讀取與重新整理本身不呼叫模型。摘要的來源連結以 snapshot digest
開啟原保存版本。一般草稿恢復時重新驗來源；結果未確認的原請求維持原 reference
與 operation key，不更換來源重送。

## 固定上游研究與採用範圍

本實作獨立撰寫，未複製以下專案程式碼／prompts，未新增套件、代理或 server 依賴。

| 固定來源 | 借鏡及改接 |
| --- | --- |
| TencentDB Agent Memory `2002196f2d7c22ea267cfcaf67c5a6fa861df681`：[L0](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/2002196f2d7c22ea267cfcaf67c5a6fa861df681/MemoryCore/src/core/conversation/l0-recorder.ts)、[generation provenance](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/2002196f2d7c22ea267cfcaf67c5a6fa861df681/MemoryCore/src/core/memory-generation-log/types.ts) | 分開保存原文與派生記憶，保留固定來源和產生版本；改接既有 SQLite transaction。未採用其 proxy、向量服務、模型自動去重／覆寫或 persona。 |
| Ken／Project Hub `9f5f6b5a71bfd2699ce2a259083036dae73ab743`：[輸出落盤](https://github.com/kieiken/project-hub/blob/9f5f6b5a71bfd2699ce2a259083036dae73ab743/hub/lib/cli-spool.js)、[歷史 manifest](https://github.com/kieiken/project-hub/blob/9f5f6b5a71bfd2699ce2a259083036dae73ab743/hub/lib/task-history.js) | 執行程序之外保存可追溯歷史；採用固定 digest 的原文／摘錄關係，不做 Hub 匯入、來源 mapping 或修改人工 checkout。 |
| Arum／Cantus Panel `ae9a11f9c28cf9fc0df05be1e53eaa00c503edd8`：[session reads](https://github.com/arumwu/cantus-panel/blob/ae9a11f9c28cf9fc0df05be1e53eaa00c503edd8/docs/SESSION-READS.md) | 閱讀與來源掃描分離、有界分頁及來源驗證。該原生閱讀路徑的快取在記憶體，本增量另外保存 Connector 的文字版本，不把快取當永久備份。 |

Tencent 上游 [MIT license](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/2002196f2d7c22ea267cfcaf67c5a6fa861df681/LICENSE)
與固定來源留供後續程式碼重用時核對；若日後實際複製 code／prompt，需另行保留適用授權聲明。
