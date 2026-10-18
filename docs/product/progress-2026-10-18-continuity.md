# 2026-10-18 討論接續與手機連續操作交付（UTC）

[#287](https://github.com/TED-H-com/bat-agent-connector/pull/287)–[#291](https://github.com/TED-H-com/bat-agent-connector/pull/291) 已合併；固定發布來源為 `001662eba19cc43a1a5d2085d1f4f9a6fe4a8457`。
[Connector 0.2.9／desktop 0.1.5](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.9-preview.20261018) 接續 EC-1.1 T1／T3／T6／T8、E01／E03／E09／E11。
這是已整合工作循環的增量；完整計畫與實機驗收仍開放。

## 使用行為

- Discussion 資訊面板的 Back／Forward 保留原分頁與捲動，來源撤回後不重開舊資料。
- 回到前景或從頁面快取恢復時，沿原事件讀取器重新核對狀態；原 key、回執與草稿保留，不自動重送。
- Work／Session／Discussion 使用同一可視 viewport 觀察，橫向、鍵盤與縮放不重建原輸入區。
- 已存設定檔在開始前揭露該 BAT 路線的下一輪限制；其他路線仍須以實際自有 Attempt 驗證。
- PM 可明確建立持久接續摘要，保留原目標、中央採用／排除事實、固定引用、涵蓋範圍及最多兩項下一步。
  新摘要沿用前版與新增訊息；一般 PM 回合可在原有字數預算內使用仍可讀的摘要。

摘要是模型判讀，不能自行採用需求、改寫決定或認定成果通過。無效輸出保留待審，
來源撤回沿衍生摘要與一般回覆傳遞。沒有新的 executor、assessment 帳本或自動付費分析。
[摘要契約與合成桌面／手機對照](../design/pm-continuation-brief.md)、
[手機契約](../design/workbench.md)保留；Classic 仍為預設。

## 發布與安裝證據

| 層級 | 本輪結果 |
| --- | --- |
| 主線 | #287–291 已合併，發布固定於 `001662eb`。 |
| CI | 精確 main 的[後端](https://github.com/TED-H-com/bat-agent-connector/actions/runs/39120716370)與[桌面／browser／工作循環](https://github.com/TED-H-com/bat-agent-connector/actions/runs/39120716393)通過；四平台二進位與 bundled Connector 的來源另行核對。 |
| 套件 | 六個套件、manifest 與 SHA256SUMS 共八個檔案；tag、來源內容與伺服器 digest 已核對。 |
| Connector | 七台已登記 preview 的主機沿既有 updater 更新至 0.2.9，逐台讀回來源、canonical skill、入口、timer、service 與原程序持續性。 |
| Desktop | 三台支援的 Linux 圖形主機安裝 0.1.5，二進位與 runtime 同源；舊包保留。 |
| 實機 | 真 BAT／模型兩輪、PM 判斷品質、一般登入 Tauri、實體 iPhone Safari／Android Chrome 仍未驗收。 |

各 PR 保留原始失敗與修正後結果。包含舊離線送出狀態斷言、手機 breakpoint 斷言、
摘要讀取呼叫、測試資料、E10 動作清單與延遲控制列的捲動競態修正；不以最後通過改寫先前失敗。
正常順序完整測試與分片 CI 分開：原 b238 的 Python 3.13 為 7057 通過、60 跳過、
5 失敗與 5 error；3.10 為 7067 通過、60 跳過。十個異常個案獨立重跑通過，
尚不是原始原因修復。後續固定版本順序完整結果仍由 [#283](https://github.com/TED-H-com/bat-agent-connector/issues/283) 追蹤。

## 下一段

已合併的手機、前景恢復、設定檔提示及摘要不重做。#292 已在發布來源之後合併清單摘要與
單次讀取共用，尚未納入本版套件；其 HTTP fixture 為 36,645 → 982 bytes，多 client 的
合成量測為每分鐘 3.506 → 0.140 MB／reader，沒有把這些數字當作實機延遲。
未送出指令退回 Idea 由 #293 追蹤。低干擾來源同步、跨 CLI、第二條完整路線、AHP、
Overview PM 判讀與擁塞選項仍依原優先順序。真實兩輪與 PM 判讀品質另需專用環境證據。
[本日較早的 0.2.8 交付](progress-2026-10-18.md)與原套件保留為歷史紀錄。
