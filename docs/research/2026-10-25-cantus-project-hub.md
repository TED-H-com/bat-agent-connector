# Cantus 與 Project Hub：接續比較與落地

本輪比較的是 **Cantus Panel** 與 **Project Hub**。先前其他參考產品的研究與
[#479](https://github.com/TED-H-com/bat-agent-connector/pull/479) 保留；本輪不依賴該 PR。
Connector 對照基準為 `f9473c4118cf3ed1b93190b253ea8b015738ab1a`。
這是既有 [EC-1.1](../product/experience-convergence.md) 與
[體驗增量](../product/experience-convergence-delta.md) 的具體接續，並未縮小
[PM v1.2](../product/pm-v1.2-plan.md) 的需求、範疇、決策、交付及維護責任。

## 固定來源與證據邊界

| 參考 | 本輪固定來源 | 相較前次的差異 |
| --- | --- | --- |
| Cantus Panel | [`08fa5da7`](https://github.com/arumwu/cantus-panel/tree/08fa5da721a874796f2b3990f66c51e0f53d892f) | 前次為 `496dc178`。現在文件包含桌面 Agent Run、Steering、Task 及獨立對話；手機仍走原入口。 |
| Project Hub | [`cfe3ab22`](https://github.com/kieiken/project-hub/tree/cfe3ab221f03f84d334ae7b97608dd31f3e643be) | 前次為 `e5e1696a`／4.124.0。README 明列公開 source 為 **4.125.0**，配布 Mac **4.125.9**／Windows **4.125.2**；不能把配布版本當作已讀過的程式。 |

以上是公開文件與 source 審閱，沒有執行它們的 AI、接管既有 session 或驗證私人部署。
Cantus 的私人實機測試敘述不算 Connector 的驗收。Cantus 目前為 UNLICENSED；
本輪不搬程式、圖片或品牌。Hub 為 MIT；本輪同樣沒有移植其程式，而是延用
Connector 已有共用元件，保留模式來源連結。

## 兩個產品各自值得學什麼

| 面向 | Cantus | Project Hub | Connector 的判斷 |
| --- | --- | --- | --- |
| 日常工作入口 | Work／Lineage／Live／Task 分開看對話、關係、活動及工作管理。 | 專案、工作、對話、終端與設定分工；清單可調寬。 | Work／Lineage／Live 與 PM Board 已有，不再加一份 Task 資料庫。補工作台清單調寬。 |
| 執行中輸入 | Codex 原生 steer 與 Claude 排隊明確區分；暫停是中斷回合。 | 受付時固定帳號；後續切換不改已排隊指令，保存失敗保留草稿。 | 保留 server-owned queue 與 operation key。來源沒有能力就不顯示 steer；外部 session 不取得控制權。 |
| 執行證據 | Agent Run 集中歷史回合、步驟、輸入輸出、錯誤；用量和成本只採來源回報。 | 原紀錄、交接、殘件與操作結果可追溯，未確認保存不直接重啟。 | 既有 Attempt／Result／journal 是權威。可改善讀取層次；不能用 UI idle、退出碼或摘要推斷成功，也不能補猜成本。 |
| 手機連續工作 | 全螢幕清單、集中 composer、觸控入口、可視高度與草稿保留。 | 手機清單獨立於桌面寬度偏好，設定失敗不丟輸入。 | 全螢幕清單、返回與 sheets 已採用；本輪確保桌面新控制不污染手機。實體手機鍵盤仍須具名驗收。 |
| 長對話閱讀 | 長文／工具區塊收合，刷新保留展開狀態。 | 程式碼及引用可複製，引用只去掉最外一層標記；生成中不提供引用複製。 | 已有安全格式化、工具收合、複製及穩定訊息 ID。引用區塊複製可接續，但須先證明訊息完成狀態，不用 session idle 猜測。 |
| 摘要後接續 | 同一原生對話新回合，重試先重新檢查現況。 | 摘要先列下一步，保留原文；區分待決策、沒有殘件與證據不足。 | 已有具固定來源的 PM continuation。接續工作是檢驗摘要品質與已否決決策不復活，不再建立摘要系統。 |
| 大量清單 | 自動讀取來源變動，保持選取與草稿。 | 清單讀摘要，詳細步驟按需讀；單次請求共用讀取，Git 非同步。 | 已有增量來源與按需讀取。應補 200-work／stream 的量測，任何快取都不能跳過操作前最後檢查。 |

## 本輪實作：可調整的工作台清單

Classic 已有 `workspace-layout.js` 的調寬元件；Workbench 隱藏該控制且把清單寫死成
256／220px，讀長標題時無法調整。這是從 Hub 模式找到、可直接補上的現有體驗缺口。

- 重用同一拖曳、鍵盤與取消邏輯；增加 Workbench 獨立的本機寬度偏好。
- 導覽欄之外保留主內容至少 360px，清單限制 200–500px。縮窄只夾限顯示，回到寬螢幕恢復偏好。
- 支援方向鍵、Shift、Page Up／Down、Home／End、雙擊重設，以及 Escape、失去 capture、切換頁面／手機時取消拖曳。
- 英文／繁中標籤、粗指標 44px 觸控範圍；手機與全螢幕清單不顯示調寬控制。
- 沿用原 list/search、conversation、composer、事件讀取及權限；沒有中央寫入、schema、ID 或 operation key 變更。

驗證涵蓋 mock HTTP／IPC、兩種語言、窄視窗往返、重載、草稿、選取、取消拖曳及
Classic 回歸。確切測試結果與交付 head 記在本輪 PR，這份研究不宣稱已合併、安裝或實機驗收。

## 值得接續的順序

1. **既定實機穩定化仍優先。** Fleet Apply／detach 與本機 participant 的缺口依
   [主線進度](../product/progress-2026-10-23.md#尚未完成--still-open) 推進；參考產品的桌面
   本機執行器不能代替中央 creation evidence、持久操作及已送出效果的查回。
2. **整理後下一步的品質。** 在已有 PM continuation 上量測最新決策優先、已完成項去重、
   待決策／無殘件／資料不足的區別；每項仍可回原紀錄。延續 EC-1.1 的真實 PM 品質驗收。
3. **執行證據更易讀。** 將現有回合／工具／錯誤按穩定 ID 分組，保留展開及閱讀位置；
   不增加執行權威。引用複製先釐清完整訊息邊界、巢狀引用及串流更新行為。
4. **大量資料的延遲。** 對 200-work 清單與多來源 stream 記錄請求量、載入時間及重繪，
   先找重複讀取再優化。最後一道授權與資源檢查保持原路徑。

不採用 Hub snapshot importer、來源映射或自動保存人的 Git 工作目錄；不複製其權限
繞過旗標、自動模型接手及帳號搬移。也不將 Cantus 的桌面獨立 runtime 塞進 Tauri。
普通 artifact 傳輸、固定 checkpoint 接續、專業 PM 全生命週期與既有 backlog 都保留。

## 原始資料

- Cantus [README](https://github.com/arumwu/cantus-panel/blob/08fa5da721a874796f2b3990f66c51e0f53d892f/README.md)、[桌面行為與驗證範圍](https://github.com/arumwu/cantus-panel/blob/08fa5da721a874796f2b3990f66c51e0f53d892f/docs/DESKTOP.md)、[原閱讀層](https://github.com/arumwu/cantus-panel/blob/08fa5da721a874796f2b3990f66c51e0f53d892f/app.js)。
- Hub [source／配布版本說明](https://github.com/kieiken/project-hub/blob/cfe3ab221f03f84d334ae7b97608dd31f3e643be/README.md)、[摘要／帳號／閱讀操作](https://github.com/kieiken/project-hub/blob/cfe3ab221f03f84d334ae7b97608dd31f3e643be/hub/README.md)、[變更紀錄](https://github.com/kieiken/project-hub/blob/cfe3ab221f03f84d334ae7b97608dd31f3e643be/hub/CHANGELOG.md)。
- Hub [側欄模式](https://github.com/kieiken/project-hub/blob/cfe3ab221f03f84d334ae7b97608dd31f3e643be/hub/public/sidebar-resize.js)、[引用與程式碼閱讀](https://github.com/kieiken/project-hub/blob/cfe3ab221f03f84d334ae7b97608dd31f3e643be/hub/public/app-messages.js)、[MIT license](https://github.com/kieiken/project-hub/blob/cfe3ab221f03f84d334ae7b97608dd31f3e643be/LICENSE)。
