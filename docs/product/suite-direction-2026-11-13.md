# 單一產品方向：BAT 相容分支＋內嵌 Dashboard

日期：2026-11-13。來源：產品負責人當日的口頭決議與後續澄清。本頁記錄決議、相容規則、
被取代與保留的既有決策，以及分期。**這是方向與規則，不是完成、安裝或實機驗收聲明。**
分期內容在各期開工前仍須經設計審查；本頁列出的分期是 lead 依授權提出的順序。

## 決議

1. **產品改以上游 BAT 的相容分支為基礎。** 上游
   [`tony1223/better-agent-terminal`](https://github.com/tony1223/better-agent-terminal)（MIT）
   維持自己的產品定位，本產品的功能不在其範圍內。因此功能不向上游提案；
   純錯誤修正仍可回饋。分支位於 `TED-H-com/better-agent-terminal`。
2. **使用者只安裝一個程式。** Dashboard 內嵌在 BAT 桌面介面中；BAT 連線設定（profile）與
   Fleet 連線是同一件事；Dashboard 與 BAT 開發介面可互相跳轉；兩者走同一條連線。
3. **保留上游手機 app。** 上游 app 對我們的主機照常運作，不重做。本產品新增的功能在手機上
   以行動版網頁提供。
4. **與上游保持相容，同時加入我們的功能**（規則見下節）。持續合併上游發行版。
5. 暫定產品名稱為「Better Agent Terminal – Full Project Suite」（負責人提出，首次公開建置前
   確認）。程式識別碼不沿用上游識別碼。macOS／Windows 簽章由負責人日後處理；
   既有桌面簽章與生命週期門檻不變。
6. Repository 結構及與 v1.3 的先後順序授權 lead 判斷（見「結構」「順序」）。

## 相容規則

以下三項是分支上所有變更的約束，並各自有自動檢查。

**連線相容（上游 client 不受影響）**

- 上游 `bat-remote/v2` 的 channel、event、payload、錯誤與限制，在所追蹤的上游版本上行為不變。
- `serverVersion` 回報所追蹤的上游版本；auth 的 `bac` capability 僅宣告協定版本，
  本產品的版本與建置識別由新增的 `bac:hello` 回覆提供。
- 新功能只使用 `bac:*` channel 與驗證結果中一個新增的 `bac` capability。本產品的事件只送給
  明確啟用的 client。
- 從未使用 `bac` 的 client 看到的行為須與上游相同：以同一份腳本 client 分別對上游
  `bat-server` 與分支 `bat-server`（同一上游版本）執行並比對回應。

**資料相容（主機可換回上游）**

- 不改上游檔案格式，也不在上游檔案中加欄位。本產品的主機資料放在 `<data-dir>/bac/`。
- 主機換回同版上游 `bat-server` 後，上游功能照常運作，只是沒有本產品的功能；須有測試。

**合併相容（跟得上上游）**

- 我們的程式放在自己的 crate、目錄、sidecar 模組與 i18n namespace。
- 上游檔案內的改動只留小型接點（hook）。每個接點有標記並列入清單；測試比對上游基準版本，
  出現未列入清單的差異即失敗。
- 分支 `main` 以機器可讀方式記錄所追蹤的上游版本。上游每次發行後合併。
- 既有只在分支上的修補（resume 強化、歷史分頁、安全修補、PM producer）逐項處理：
  上游已有等效修正則採用上游；其餘改為清單內接點或移入擴充。

技術命名（`bac:*`、`tauri-plugin-bac`、`<data-dir>/bac/`）與產品名稱無關，改名不影響它。

## 取代與保留

| 既有決策 | 狀態 |
| --- | --- |
| [R00](realignment-v2.md#固定決策r00) 1：新桌面程式放在本 repo 的 `desktop/` | **終點更新**：獨立桌面程式在整合版達到同等能力後退場。`desktop/src` 仍是唯一的共用前端來源；瀏覽器入口保留 |
| R00 2：Python Connector／Task Service 是唯一中央業務後端 | **保留**。分支只新增主機端合約，不新增排程、授權或帳本 |
| R00 5：人工 BAT 資源唯讀；R00 8：結果不明先查回、不重送 | **保留** |
| [next-gen Connector](../design/next-gen-connector.md) Stage 4「this repository does not modify BAT」與[不改 BAT 評估](../design/no-bat-change-evaluation.md) | **取代**：其中列出的主機端合約改為在分支實作。Connector 對未修改的上游主機仍須可用，並沿用該評估記載的限制 |
| [Fleet profiles](../design/fleet-profiles.md) 的外部 BAT 協調（僅在 BAT 未執行時寫入 profile、launch receipt） | 維持到 P3；P3 以程式內整合取代 |
| Dashboard「在 BAT 開啟」只能開到 profile、不能聚焦 session | P2 以程式內跳轉取代 |
| v1.2／v1.3／EC-1.1 的產品範圍、生命週期、保留與安全要求 | **保留**。本頁改的是外殼、連線與主機合約，不是 PM 產品範圍 |

## 結構

- `TED-H-com/better-agent-terminal`：產品桌面程式與主機端 `bat-server`。保留 GitHub fork 關係，
  上游合併維持一般 `git merge`。
- `TED-H-com/bat-agent-connector`（本 repo）：中央、Dashboard 前端來源、產品文件與追蹤 issue。
- 依賴單向：分支以固定版本取用本 repo 的產物，不複製原始碼。兩邊以既有的 version／capability
  判定相容。

## 分期

主機合約：[Connector 握手合約](../design/host-contracts.md)記錄 P1 第一段的 capability
偵測、hello、記憶中的身分及降級行為；此段不啟用任何主機功能，也不是 P1 完成或實機驗收。

各期的驗收都分開回報 branch、merged、installed、live-tested；CI 不是實機驗收。

| 期 | 內容 | 完成條件 |
| --- | --- | --- |
| P0 分支成為可維護的下游 | 合併上游最新發行版；PR CI；上游基準記錄、接點清單與測試；對上游 `bat-server` 的差異測試；不動上游設定檔的品牌／識別碼／更新來源覆蓋；同步流程文件 | 差異測試無未解釋差異，CI 通過 |
| P1 主機端合約 | `bac` capability；原子新增 terminal；以 ID 查詢的命令收據；所有權／控制版本檢查；持久受理紀錄；session 開啟／聚焦請求。Connector 偵測能力，上游主機走現行路徑 | 不改 BAT 評估中列出的競態在使用分支主機時由 fixture 測試證明已消除；上游主機行為不變 |
| P2 單一桌面程式 | Dashboard 內嵌；native 能力改為 plugin；雙向跳轉；本機中央隨安裝提供；唯讀匯入既有 BAT 桌面設定 | 只裝整合版即可完成目前兩個桌面程式的工作（對照表） |
| P3 Fleet 與 profile 合一 | 一份主機／連線清單；每台主機一條共用連線；Fleet 支援 macOS | 外部程式協調路徑不再使用 |
| P4 獨立桌面程式退場 | 既有安裝的遷移路徑；文件與網站更新。瀏覽器入口保留 | 新使用者文件只描述一個安裝 |

## 順序

進行中且接近完成的項目（結果稽核修正、路線圖報告儲存、外部結果接續的第一段後端）先收尾，
並發行一版作為基準。尚未開工的 v1.3 項目在 P1 落地前暫緩，以免在即將被取代的跨程式協調上
再投入。P0 與 P1 的設計同時開始。

## 尚未決定

- 產品名稱與程式識別碼的最終值（首次公開建置前由負責人確認）。
- macOS／Windows 簽章與更新金鑰（負責人處理）。
- 內嵌方式（子 webview 或 iframe）：P2 先做技術驗證再定。
- 手機瀏覽器如何到達中央（中央目前只接受 loopback）：P2 設計時提出方案。
- 本產品的事件與 API 是否經 `bat-remote` 連線多工：上游協定對慢速 client 會中斷連線並有佇列
  上限，第一版預設不多工，而是共用同一條主機連線；P3 設計時確認。

## 現況

2026-11-13：決議已記錄。尚無任何整合版建置、合併、安裝或實機驗收。
