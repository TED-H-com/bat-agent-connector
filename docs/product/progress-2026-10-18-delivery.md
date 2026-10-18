# 2026-10-18 PM 判讀、外部歷史與交付增量（UTC）

[#301](https://github.com/TED-H-com/bat-agent-connector/pull/301) 修正 checkpoint 讀取失敗及 PM 評估準備與刷新競態；[#300](https://github.com/TED-H-com/bat-agent-connector/pull/300) 整合 PM 總覽判讀、外部對話歷史、登記頁競態與 provider 錯誤分類，
並包含已合併的 #292／#293／#297／#299；發布固定來源 `b44a83abc7d548747983298b8290ab1d544898d6`。
[Connector 0.2.10／desktop 0.1.6](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.10-preview.20261018) 接續 EC-1.1 T2／T3／T4／T5／T6／T8、E08／E09／E11。
完整計畫仍有實作與實機驗收缺口；本輪交付不代表所有項目完成。

## 使用行為

- 總覽的 PM 判讀須明確啟動，保存固定依據、模型回覆、採用狀態、涵蓋範圍與歷史版本。
  來源撤回會移除可見內容；重新整理不自動付費分析，也不採用或執行建議。
- Resources 可讀取明確設定且逐來源授權的本機 Codex／Claude JSONL 對話。
  列表與內文分開載入；背景新增內容保留閱讀位置，按「讀取新內容」才更新內文。
  不掃描個人目錄，不繼承用量讀取權限，不把外部 session 變成受管工作。
- 工作清單先傳摘要，選中後取詳情；同一請求共用重複來源讀取，副作用前仍重新核對權限與版本。
- 證明尚未送出的第二句，可沿原操作取消並保存為 Idea；未知送出結果保留待查回。
- 登記頁在背景刷新期間保留明確選取意圖，取得有效詳情後才開面板，避免丟失點選或提前遮住導航。
- 配額／驗證錯誤按已知結構化 code 分類，原有政策與權限不變；沒有自動切換模型或帳號。
- 原生預檢記錄固定階段與耗時；Windows 安裝 fixture 只容許已移除的 registry key 消失。
  原期限、權限錯誤與失敗紀錄保留。

[PM 判讀契約](../design/pm-overview-assessment.md)、
[外部歷史契約](../design/external-conversation-history.md)及其合成畫面保留完整範圍。
外部歷史仍標示涵蓋不完整、執行狀態未知；不將跨 adapter 重複紀錄、衍生 Work、
原生 coding route 或 AHP 能力標成完成。Classic 仍為預設。

## 發布與安裝證據

| 層級 | 本輪結果 |
| --- | --- |
| 主線 | #300／#301 已合併，發布固定於 `b44a83ab`；組成 PR 的原提交及失敗紀錄保留。 |
| CI | 精確 main 的[後端](https://github.com/TED-H-com/bat-agent-connector/actions/runs/39137530624)與[桌面／browser／工作循環](https://github.com/TED-H-com/bat-agent-connector/actions/runs/39137530576)通過；四平台二進位及 bundled Connector 來源另行核對。 |
| 套件 | 六個套件、manifest 與 SHA256SUMS 共八檔；tag、來源內容與伺服器 digest 均核對。 |
| Connector | 七台已登記 preview 的主機沿既有 updater 更新至 0.2.10，逐台核對來源、canonical skill、入口、timer、service 與原程序持續性。 |
| Desktop | 三台支援的 Linux 圖形主機安裝 0.1.6，二進位與 runtime 同源；保留舊包。 |
| 實機 | 真 BAT／模型兩輪、真實 PM 判斷品質、一般登入 Tauri、實體 iPhone Safari／Android Chrome 仍未驗收。 |

組合回歸 Python 3.13／3.10 各 445 項、介面 124 項、Node 187 項、Rust 路由 79 項通過。
最後來源邊界修正另有 Python 3.13／3.10 各 62 項通過。原始 Windows registry、
登記頁競態及 Rust 格式 CI 失敗均保留；修正後重新檢查固定版本。
發布前 #301 的 checkpoint 讀取修正各有 79 項 Python 3.13／3.10 測試通過；
PM 評估準備競態修正有 161 項介面及 187 項 Node 測試通過；
外部歷史撤權測試的同步修正各有 37 項 Python 3.13／3.10 測試通過。
#300 main 的原撤權 fixture 失敗及 #301 的原瀏覽器競態失敗均保留。
原 #300 固定來源在發布前被取代，未曾作為 0.2.10 發布或安裝；修正沒有放寬期限或移除授權檢查。
正常順序完整套件另由 [#283](https://github.com/TED-H-com/bat-agent-connector/issues/283) 追蹤：
原 b238 Python 3.13 有 5 failures／5 errors；3.10 為 7067 通過、60 跳過。
原 d4 Python 3.13 為 7064 通過、60 跳過、1 failure／2 errors；不宣稱整批通過。
較後的 e78 固定版本 Python 3.13 正常順序整批為 7071 通過、60 跳過、8 warnings；3.10 尚在執行。
它早於本次發布來源，不算最新版本整批通過，也不是原卡住原因的修復。
[#241](https://github.com/TED-H-com/bat-agent-connector/issues/241) 已按 confinement 自身的三份完整紀錄及附 I/O 壓力量測的雙版本檢查結案；
這不會關閉 #283 或把其他 deadline 問題標成已修。

## 原計畫逐項狀態

| 原工作 | 已交付部分 | 仍缺 |
| --- | --- | --- |
| T1 共用工作空間 | 共用 Workbench、手機清單／工作／sheet、返回與草稿保留 | 實體手機／一般登入桌面、目標環境預設 rollout 驗收 |
| T2 第一句到第二句 | 建立、採用並開始、server 持久第二句、原 key 恢復、未送指令退回 Idea | 真 BAT／模型兩輪及關閉 client 後的實際派送證據 |
| T3 來源與 profile | 固定 profile、能力／下一輪限制、清單摘要、獨立授權歷史來源 | 更多來源 adapter、完整擁塞選項及政策內替代 |
| T4 固定成果與現場 | 固定 Result／diff／驗證／queue、來源撤回與版本關係 | 更多 adapter 的同源成果、真環境讀取量測 |
| T5 Native／AHP | 具名本機 Codex／Claude 檔案歷史、既有 AHP 觀察 | 原生完整 start／next／result／recovery、AHP 自有工作、可信跨 adapter 去重及 derive |
| T6 PM 判讀與後續 | 兩份評估入討論、接續摘要、總覽判讀與不可變歷史 | 用真 Result 驗證接受條件、依賴解鎖、決策與維護循環；原 v1.2 缺口 |
| T7 MCP 與交付 | 核心 UI／HTTP／MCP／Tauri 契約與固定結果；套件發布 | 另一 controller 的真環境等價操作及完整維護交付驗收 |
| T8 遷移與可用性 | 版本化套件、七台自動更新、三台 Linux desktop、合成手機／故障測試 | 正常完整套件問題 #283、Windows cold start #131、實體平台與新手驗收 |

EC-1.1 D1／D2／D4 已有上述增量，但其真實環境條件沒有被勾掉；D3 仍是 P0 實機驗收。
D5 已交付；D6 本輪僅修正結構化錯誤分類，尚未完成所有擁塞選項。Connector 自動更新已交付且啟用。
R1–R5 與原 PM v1.2 W／A／M 驗收編號全部保留，不能以本表的「已交付部分」視為整項通過。

## 後續候選與本版界線

W24 工作區 PM 路線選擇 [#302](https://github.com/TED-H-com/bat-agent-connector/pull/302) 為待合併候選；W45 人工序列輸入觀察 [#303](https://github.com/TED-H-com/bat-agent-connector/pull/303) 為待合併候選。兩項均不在本次固定 0.2.10／0.1.6 套件內；本段不宣稱已安裝或實機通過。

## 下一段

已交付的核心循環、手機導航、接續摘要、總覽判讀、清單摘要與退回 Idea 不重做。
優先取得專用環境的真 BAT／模型兩輪與 PM 判讀證據；持續補外部歷史歸因／衍生、
第二條完整執行路線、AHP、本機 Git、擁塞時明示選項及原 PM 生命週期缺口。
真實 CLI 版本相容性與實體手機驗收不能以合成 JSONL 或瀏覽器 viewport 代替。
[前次 0.2.9 交付](progress-2026-10-18-continuity.md)及原始套件保留為歷史。
