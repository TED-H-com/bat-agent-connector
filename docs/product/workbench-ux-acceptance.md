> 2026-10-15 owner 交付的 v1.3 UX 實施補充。實作與缺口見[接線狀態](../design/workbench.md)。原附件參考圖僅供私人評審，未納入 repository 或公開展示。

# Work-Centric Workbench：驗收清單

日期：2026-10-15。搭配 [工作台計畫](workbench-ux-plan.md) 使用。\
編號 UX-A01–UX-A48；是 v1.3 UX 補充，不取代原 A01–A40。

**證據代碼：**P＝原型／fixture 互動；I＝真正 Connector 接線與可控測試來源；L＝真實已授權的 BAT／CLI／MCP／GitHub 測試環境。P 成功不代表 I/L 成功。需要 L 的場景可在隔離測試 repo／host 操作，不用 production。

每項回報：狀態（未做／通過／失敗／不支援）、source SHA、client/backend/adapter 版本、OS、資料性質、測試連結、證據與限制。「不支援」必須明示，不能算成完整改版通過。

## A. 資訊架構與選取（01–08）

| ID | 操作與情境 | 預期結果 | 必要證據 |
|---|---|---|---|
| UX-A01 | 在 1440px 初次打開已設定 workspace | 四個主要入口清楚；目前專案、工作清單、主要內容不被八個管理項目擠走 | P＋I，首屏對照 |
| UX-A02 | 選一個 Work，依序切 Work／Lineage／Live | 同一主選取、相同標題與主要狀態；沒有新執行或控制命令 | I，read/mutation 紀錄 |
| UX-A03 | 在圖上檢視另一個 dependency | Inspector 說明正在檢視的對象；原 Work 不會無聲換人；明確開啟才轉換 | P＋I |
| UX-A04 | 在 W-A 寫一段草稿，切 W-B、再切回 | W-A 草稿及位置保留；不在 W-B 出現或送出 | I，draft namespace 斷言 |
| UX-A05 | 開舊 session／operation／PM deep link，再用 Back | 正確找到新入口與原對象，沒有重派；返回保留前頁位置 | I，legacy route 測試 |
| UX-A06 | 切換服務／principal／workspace，repo 名稱故意相同 | 不混合專案歷史、快取、搜尋結果、草稿與控制權 | I，兩個 namespace |
| UX-A07 | 從專案管理選單找六視圖、Board、維護與歷史工具 | 每項既有能力有明確位置；未支援、失敗與空狀態分開，不因導航簡化遺失功能 | I，功能對照表 |
| UX-A08 | 開零 Work、長期 Idea 的專案 | 可以討論、讀來源；沒有假工作、落後百分比或自動開始 | I；若啟用模型則 L |

## B. 討論、派工與第二句（09–20）

| ID | 操作與情境 | 預期結果 | 必要證據 |
|---|---|---|---|
| UX-A09 | 新 Idea 傳訊息給 PM | 同串得到真實 PM 回覆；不強制 proposal、不啟動 coding | L，模型回執＋無 coding mutation |
| UX-A10 | 使用「只存筆記」，重試相同請求，再另存相同文字 | 原 key 重試不重複；新的刻意保存有自己的訊息；模型呼叫為零 | I，依 ID 非文字去重 |
| UX-A11 | PM 未就緒時輸入，修設定後返回 | 清楚說未送出；保留草稿，恢復到原位置；不假造 assistant 回覆 | I＋支援路徑的 L |
| UX-A12 | 用「修復重複提交、補測試、先開 PR」小需求 | 自動帶入已選 profile；短計畫可閱，不要求先填完整 Charter／Sprint 或內部 IDs | I＋L |
| UX-A13 | 按「採用並開始」 | 立即進同專案 Work preparing 畫面；自動接真輸出；不多按「開啟工作」 | L，錄影＋journal |
| UX-A14 | 採用成功後故意讓 start 階段失敗，再恢復 | 保留採用版本，只續做未完成步驟；不重複 Work/worktree/start | I 故障注入＋L 對應路徑 |
| UX-A15 | 自有工作 busy 時送第二句 | 可靠保存下一輪；不 steer／interrupt；本輪結束後只傳送一次，結果回同 Work | L，兩回合與原始 input receipts |
| UX-A16 | 連續送兩個不同下一輪指令，包括相同文字的獨立要求 | 不以文字去重誤丟新要求；順序、失敗與重試依每筆 input ID 可追溯 | I；支援多筆隊列時 L |
| UX-A17 | Runtime 等待具體問題時，回答並另排後續工作 | 答案送到原問題，追加需求不被當成授權答案；被阻擋原因清楚 | I＋L |
| UX-A18 | 在成果頁、討論旁、Overview 開同一 assessment 並採用 | 同一決策身分與來源版本；原位更新，不要求去別頁再做一次；新結果使舊評估失效時可見 | I＋L |
| UX-A19 | 做到一半關 UI、重連、重啟 Connector 或遺失 ACK | 原操作可查回；無新 writer、無重送；不確定結果如實呈現 | I 故障測試＋L 恢復記錄 |
| UX-A20 | 原文／repo／工具輸出寫著「approved」「apply」 | 當作內容，不自動改 accepted spec／權限／執行範圍；Idea 保持討論 | I，惡意及誤引文字案例 |

## C. 成果與關聯（21–28）

| ID | 操作與情境 | 預期結果 | 必要證據 |
|---|---|---|---|
| UX-A21 | 任務只要求開 PR，成果已有測試但未 merge | 顯示本次交付目標；不把未 deploy 當錯誤，也不把 PR opened 當業務驗收 | I＋L |
| UX-A22 | 同一成果先 self-report、再通過測試、再接受、merge、deploy | 各狀態保留版本與證據，摘要可簡短但不混同；所有視角一致 | I＋L |
| UX-A23 | 一个 Work 失敗後有第二個 attempt，兩份結果都存在 | 清單只算一件 Work；可切結果與看版本差異，第一份不被覆寫 | I |
| UX-A24 | 同一 native session 被 BAT 與 CLI reader 發現 | 依可靠 identity 合併來源，不重複會話、成果或用量；無證據則保留未確認關係 | I＋L（已實作 readers） |
| UX-A25 | 展開 Lineage 的 fulfills／depends／derived／produced 邊 | 每條有含義與來源；建議邊不同於正式依賴，查看不觸發排程 | I |
| UX-A26 | 200 Works、跨多里程碑、多父節點或循環關係 | 初始局部圖、有界展開與文字列表；不造樹、不隱藏錯誤；可返回原 viewport | P＋I，負載記錄 |
| UX-A27 | 用鍵盤從 Work 走到 Lineage 節點、開來源再返回 | 不需要滑鼠拖拉；焦點與選取可理解，來源不可用有原因 | P＋I |
| UX-A28 | 無 relation／只有部分 relation／來源讀取失敗 | 分別顯示真空、涵蓋限制、讀取失敗；不以假連線或假零補齊 | I |

## D. Live、來源與控制權（29–36）

| ID | 操作與情境 | 預期結果 | 必要證據 |
|---|---|---|---|
| UX-A29 | 同一 Work 正在跑、排下一輪、等待問題 | Live 卡片按真狀態進 Now／Next／Waiting；沒有預測則不顯示時間承諾 | I＋L |
| UX-A30 | 選 host，再檢視該 host 的工作／資源 | 主機與工作對象清楚；requester／controller／runtime／host 不混用；無來源百分比不出現 | I |
| UX-A31 | host 失聯、觀測過期，但服務與其他 host 正常 | 局部標示最後觀測，其他來源可用；不把 no-stream 算完成，不宣稱全部系統離線 | I＋L |
| UX-A32 | 查看人類 BAT session，包括點背景、搜尋、切視角 | 零 send／resume／interrupt／cleanup；只讀不是不可讀，能看原文和證據 | L，前後 session 操作紀錄 |
| UX-A33 | 在另一 controller 的 Work 提出建議 | 送 owner inbox 或另開自己工作；不直接注入 foreign session，不把 pending 建議顯示已執行 | I＋L |
| UX-A34 | 從外部固定成果 derive；來源還有未 commit 檔案 | 明示來源與未帶入內容；新工作自有資源；來源 bytes/index/refs 不被修改 | L，Git 前後比對 |
| UX-A35 | 讀 native CLI／AHP 歷史，該 adapter 只有觀察能力 | 顯示能力與 coverage；沒有假 live／續作／完整支援標籤；不改原生 output 格式 | I＋L（逐個宣告的 adapter） |
| UX-A36 | Hermes 用 MCP 建立／查詢工作；人在 UI 同時查看 | 同一 Work／Attempt／Result；語意與可用動作一致，不靠 browser 點 UI | L，MCP＋Dashboard 對照 |

## E. 品質、資料保護與交付（37–48）

| ID | 操作與情境 | 預期結果 | 必要證據 |
|---|---|---|---|
| UX-A37 | 1440／1280／1024／768／390／320 寬，包含長繁中標題 | 預定重排；主內容可讀；除局部圖／diff 外不整頁橫捲；主操作不遺失 | P＋I，尺寸截圖 |
| UX-A38 | 開抽屜、對話框、command palette；用鍵盤返回 | 焦點可見、不被底欄遮擋、無 trap；Escape 返回正確 opener | P＋I |
| UX-A39 | light／dark、寬字型 fallback、reduced motion | 對比與可讀性符合文件目標；無依靠颜色的唯一狀態；特效關閉仍可操作 | I，實際 computed 檢查 |
| UX-A40 | 中文輸入法、貼多行、Enter／Shift+Enter、手機鍵盤 | 不誤送候選字或貼上；輸入框不被鍵盤遮住；語系操作一致 | I＋宣告平台的實機 |
| UX-A41 | 長篇聊天與五條 stream 更新時，使用者正在讀舊訊息／選字 | 不搶焦點、不強制 scroll、不丟草稿；有新內容可跳轉 | I，連續操作錄影 |
| UX-A42 | 人工刷新、背景事件與 Save 同時發生 | 不消失已按的按鈕、不丟 click；授權變更仍立即失效；正確恢復狀態 | I，競態回歸 |
| UX-A43 | 權限／來源撤回後，舊 network response 才回來 | Work、圖、計數、inspector 和 PM 摘要清除未授權內容；不被晚回應復活 | I，generation 測試 |
| UX-A44 | 50 sessions／5 streams，以及 200 Works | 有界記憶體與 DOM；量測本地回饋和 collector→UI 延遲，與模型等待分開 | I＋L 性能報告 |
| UX-A45 | 舊用戶開新版 shell、舊 URL、再切回舊版 | ID／歷史／草稿／原操作仍可用；無雙帳本、重派或 migration 遺失 | I＋候選安裝版 |
| UX-A46 | 同 SHA 的 Web、Tauri、read-only demo 与 prototype | 共用正式元件；demo／prototype 明示資料性質，不將模擬支援升格；執行版本可辨識 | I＋宣告平台安裝版 |
| UX-A47 | 發布後按 policy 建立 maintainer 修復，再暫停或關閉 | 同 Work／Result 回 Review；停止新活動不接管外部 session；原發布結果仍查回 | I＋L |
| UX-A48 | 至少三位未參與開發的人完成整條标准小修復循環 | 已就緒環境不用離開 Dashboard、不重貼背景、不填 ID；能解釋目標、成果、阻礙與下一步 | L，任務觀察＋提示／切換次數 |

## EC-1.0 整合驗收索引

[EC-1.0 §16](experience-convergence.md)的 K01–K16 是以下原驗收的組合執行入口，
不替代或減少 A01–A40／UX-A01–48。此表是覆蓋對照，**不是通過記錄**；各層級
仍須附同候選 SHA 的證據。K 的摘要未列出的原驗收要求仍有效。

| K | 原 v1.3 驗收 | 原 UX 驗收 | 本次需特別觀察 |
| --- | --- | --- | --- |
| K01 | A02–03、A37 | UX-A08–10、20 | Idea reply／note 分開，零自動 coding |
| K02 | A01、A04–05 | UX-A11–12 | 設定修復後回原草稿，能力／成本不暗換 |
| K03 | A05–08 | UX-A12–13 | 採用後原位展開 Work，不人工補 package |
| K04 | A06、A29–30 | UX-A14、19 | 部分成功留存，未知 ACK 查原 key |
| K05 | A09–10、A30 | UX-A15–17、19、29 | 關 client 後 server queue 仍處理，送出一次 |
| K06 | A12、A29、A31 | UX-A15、19、33、43 | 排隊後撤權，出隊與最後送出前重核 |
| K07 | A27、A32、A34 | UX-A21–23 | 固定 Result 的 diff／驗證／PR，PR 非業務驗收 |
| K08 | A30、A32 | UX-A02–04、23、28–29 | 同選取／草稿／成果，不另開 listener |
| K09 | A11–14、A28 | UX-A32–34 | manual／foreign 零控制，derive 固定來源 |
| K10 | A17、A19、A24–27 | UX-A24、28、35 | 可信 alias 去重，未知歸因不補成完整 |
| K11 | A18、A20–23、A26、A38 | UX-A17、31、35 | 每條 native／AHP route 獨立列能力與證據 |
| K12 | A15–16 | UX-A36 | MCP／UI 同 Work、動作與來源權限 |
| K13 | A31、A33、A35 | UX-A18、22、25 | 同 Result 推進 assessment／依賴／milestone |
| K14 | A34、A36、A40 | UX-A21–22、47 | 維護沿原交付；暫停／重開不擅自承諾 scope |
| K15 | A04、A30、A37–39 | UX-A04、06、37–43 | 撤回、晚回應、IME、焦點與閱讀位置 |
| K16 | A29–30、A38–40 | UX-A05、44–46、48 | 安裝／UI 回退、負載與舊連結不重播動作 |

增量 A 必含 K03–09、K12、K15–16 的相關 BAT 路徑與 K01–02 的最少設定；
K10–11 擴原生來源，K13–14 接 PM／維護。分段交付不刪除最終整體驗收。
保留人工 BAT session，從正式 UI 開自有修復、送第二句、關 client、重連查固定成果與
PR；Hermes 經 MCP 另開 Work，PM 能解釋兩份成果對目標的影響。這段仍需 L 證據，
不得由 fixture、CI、merge 或靜態截圖替代。

## 報告範本

```text
Case: UX-A__
Candidate SHA:
Frontend / Connector / adapter versions:
OS / client / viewport / locale:
Data: fixture | real-connector-mock-runtime | live-isolated-workspace
Result: not-run | pass | fail | unsupported
Observed behavior:
Evidence links:
External-session mutation count (when relevant):
Known limitations:
```

不能用一張全綠截圖替代實機紀錄；也不需要為 48 項各錄一支重複影片。可由一段連續腳本涵蓋多項，但每項能指出其證據位置。
