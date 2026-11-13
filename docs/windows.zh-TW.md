# Windows Fleet 與桌面資源

[English](windows.md) · **繁體中文** · [產品總覽](../README.zh-TW.md)

Windows Fleet 是 Tauri 裡的本機連線與啟動層。Python Connector／Task Service 授權並派工，BAT 在選定主機執行工作。桌面包包含本機 Python 中央 runtime；加入既有中央（例如 Linux 中央）保留為進階選項。

使用[穩定版 0.2.19](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.19)：Connector 0.2.19／Desktop 0.1.15。下載 `better-agent-dashboard_0.1.15_windows_x64.exe` 並核對 `release-manifest.json`、`SHA256SUMS`。更新時從系統匣退出 Dashboard，再執行安裝器，保留既有應用程式資料。舊未簽章測試包不支援內建簽章更新。來源、CI 與獨立的驗收證據見[交付狀態](product/implementation-status.md)。已發布的 0.2.18 使用 0.2.12 與 0.2.17 核心升級基準。後續開發候選可從 [`main` 成功的 desktop workflow](https://github.com/TED-H-com/bat-agent-connector/actions/workflows/desktop.yml?query=branch%3Amain) 取得；目前候選版的核心升級 fixture 使用 0.2.12 與 0.2.18 固定發布來源，且要求候選版的 Connector 版本較高。須核對來源與 Windows 安裝 fixture；Actions artifacts 可能到期。

Linux 與 Windows 套件未簽章；macOS 採 ad-hoc 簽章，未公證。沒有 signed updater feed。本版不新增 owner 安裝、實體裝置或真 BAT／模型驗收證據。

## 每一層負責什麼

| 層次 | 責任 |
| --- | --- |
| Windows Tauri／Fleet | 選定背景連線、SSH tunnel ownership、路由／就緒檢查、BAT profile 啟動、登入選擇、憑證、檔案與系統匣。 |
| Python Connector／Task Service | Agent 身分、權限、派工、task 協調、operations、回執與歷史。 |
| BAT 主機 | Workspaces、人工 sessions／worktrees，以及 Agent 各自的 managed 資源。人的 BAT desktop／mobile 照常使用。 |
| GitHub／已配置的部署目標 | 經中央操作管理已發布 commit、整合 PR、merge 與部署證據。 |

專案管理是這套系統的一個 Dashboard 視角。Project Hub 提供互動參考，並不是本產品的
runtime 或資料後端。

## 首次啟動與 BAT 存取

1. 安裝相符的 NSIS `.exe` 並開啟 Dashboard。新安裝先選用途：「提供本機工作區」或「兩者兼有」會準備內附本機 Connector；「使用既有服務」則加入既有 Connector。既有安裝保留原連線與身分。本機流程不需預裝 Python／uv 或複製中央 API token。
2. 開啟「設定 → 連線 → 準備開始工作」，在「連線至 BAT」選擇可匯入的 profile，或填入實際 BAT 位址、可信指紋、workspace profile ID 與 BAT token。按「預覽連線」檢查變更，再按「套用連線」；受管權限與專用遠端目錄依需要從進階設定選擇。
3. Git 同步與成果操作使用「此電腦已設定的 SSH alias」。只有預定的直接 BAT 開工需要共用 clone metadata 時，才勾選「允許共享 clone 的 Git worktree」；這些選項都不會接管人工工作。
4. 在「連結 GitHub 儲存庫」讀取所選主機的 workspaces，綁定精確 repository、Git remote 與 workspace ID，依需求開啟整合／merge／metadata 權限。儲存只驗證存取，不會推送或建立 PR。
5. 「設定驗證命令」使用精確 Task Service project key，設定執行程式、每行一個完整參數及逾時秒數；儲存不執行。來源選擇、恢復與 Agent 存取詳見[完整設定指南](getting-started.zh-TW.md)。

共用 Dashboard 提供可調整的專案樹、對話與發送回執、模型偏好、經檢視的技能選取、成果及修復工作入口。選取技能不會自動把它啟用到 Agent。

## Fleet 連線、profiles 與登入

首次設定 Fleet 可直接使用公開的 [Rust 設定指南](design/fleet-configuration.md#繁體中文設定摘要)、[fleet.example.json](../desktop/fleet.example.json) 與[通用 inventory／profile／SSH 範例](../desktop/fleet.example/README.md)。明確指定 `"backend":"rust"` 不需其他 repository 或舊 PS／VBS scripts；範例連線資料必須換成自己的可信設定。已有 Fleet 時保留原 inventory、profiles、SSH 設定及 owner，backend 變更沿用原生遷移流程。中央 onboarding 不會猜測 SSH 拓撲或接管既有 Fleet owner。

在連線設定查看目前 backend，分別選擇**背景連線、BAT profiles、Dashboard**，保存或啟動前預覽必要條件。Dashboard-only 不需本機 BAT executable。Tunnel、固定 TLS 指紋、BAT 驗證／workspace、中央 observe 存取是不同的就緒檢查。

需要 Tailscale 登入時，從面板開啟已安裝的 Tailscale 程式，在該程式登入後刷新。可保留登入選擇器或檢視已保存啟動選項。未指定 `backend` 時沿用 **PowerShell**；既有 PowerShell 安裝切換 Rust 須使用明確遷移流程，先證明舊 owner 已停止。

Rust Fleet 監控會以設定的 Tailscale IP 自動喚醒已知的閒置節點，並限制重試次數及設定冷卻時間。
面板會顯示喚醒進度；主機仍無法連線時會自動重試。展開「詳細資料」可查看診斷代碼。
未知或無法唯一識別的節點不會收到喚醒探測。

「登入電腦時啟動」會啟動自有 managed 安裝與已選 Windows Fleet 登入流程。「在瀏覽器開啟 Dashboard」使用本機認證交接。關窗後中央背景工作繼續；明確「退出 Dashboard」會依正常停止條件處理已證明持有的 Fleet monitor，不結束中央任務或人工 BAT sessions。

「進階：加入既有中央」經原生確認與獨立憑證登錄後，改變目前 client 連線；原本機背景工作保留。已配置的遠端 bootstrap 用固定 SSH recipe 查詢／啟動指定中央，不會任意安裝服務，也不因連線失敗另造資料庫。

## 資源索引

| 主題 | 對應資源 |
| --- | --- |
| 連線、視窗與登入選擇 | [原生 Fleet 整合](design/desktop-fleet-native.md) · [共用 UI](../desktop/src/fleet-desktop.js) |
| Rust runtime 與 PowerShell 相容 | [Fleet core](../desktop/fleet-core/README.md) · [backend 路由](../desktop/src-tauri/src/fleet.rs) |
| Tailscale、已配置的備援路由與就緒 | [路由政策](design/fleet-routes.md) · [Tailscale 登入](design/tailscale-recovery.md) |
| 登入啟動與 owner 遷移 | [遷移合約](design/fleet-migration.md) · [Windows ownership](design/fleet-windows.md) |
| 指定中央服務啟動 | [固定 bootstrap recipe](design/fleet-bootstrap.md) |
| Credential Manager、系統匣與原生檔案 | [桌面 client](design/desktop.md) · [檔案操作](design/native-files.md) |
| 驗證安裝包與 installed fixtures | [Desktop CI](https://github.com/TED-H-com/bat-agent-connector/actions/workflows/desktop.yml?query=branch%3Amain) · [驗收矩陣](product/acceptance-v2.md) |

Windows 套件／WebView fixture、Fleet 原始碼測試與使用者實際 SSH／Tailscale／BAT 環境是不同的證據。驗證紀錄須與套件來源 commit 相符。正式簽章與正式更新通道已排除於本次交付；完整實際環境驗收由使用者負責。排除不等於能力或測試已完成，Mac Dashboard 支援也不表示 Windows Fleet 已移植。
