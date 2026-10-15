> 2026-10-15 owner 交付的 v1.3 UX 實施補充。實作與缺口見[接線狀態](../design/workbench.md)。原附件參考圖僅供私人評審，未納入 repository 或公開展示。

# Better Agent Dashboard：以工作為中心的介面改版計畫
## v1.3 UX 實施補充｜Work · Lineage · Live

**文件日期：2026-10-15**\
**產品負責人：Ted**\
**適用 repository：`TED-H-com/bat-agent-connector`**\
**本次核對主線：`10f15ef4e4c8676a4605fbfbd2e3c576597303fa`**\
**文件狀態：提供開發者實作及產品驗收的提案；不是已實作、已測試或已發布聲明。**

> **交付目標：**使用者在同一個專案脈絡中，找到工作、理解目的、直接討論或派工、看真實輸出與成果、追加修改並作出決策。切換 Work／Lineage／Live 只改變觀看角度，不更換工作、不重建執行、不丟失草稿。專業 PM 能力保留，但不再成為日常做事前必須穿越的一排功能頁。

本文件是 **v1.3 閉環計畫的 UX 實施補充**，不是 v1.4，也不取代 E00–E11、R0–R5、A01–A40。新增工作使用 **UX01–UX10**，驗收使用 **UX-A01–UX-A48**，避免覆蓋原編號。v1.2 未被取代的 PM、Sprint、Maintainer、資料保留與控制者要求仍有效。[S1]

---

## 0. 先讀：這次到底要改什麼

### 0.1 三項同時成立的成果

1. **視覺與資訊架構重整。**從功能選單集合，改成穩定的專案／工作空間。參考圖的閱讀順序、安靜外框與清楚主次，必須體現在正式畫面，不是只套同色圓角。
2. **真實的工作閉環。**主要輸入框能啟動真正的 PM 回覆；明確採用並開始後，在原工作直接看啟動與輸出；第二句回到自己的下一輪；成果與 PM 決策能就地處理。
3. **資料與操作一致。**人、MCP agents、BAT 和各 CLI 的工作結果沿用同一個 Connector journal。新視圖不另存進度、不增加控制權，也不能靠示範資料冒充實際能力。

### 0.2 明確採用與不採用

| 採用 | 不採用 |
|---|---|
| 以 Project／Work／Discussion 為使用者認知對象 | 以 operation／package／table 名稱作主要導航 |
| 同一工作三種視角：工作、關聯、現場 | 在原八個頁面旁再疊三個互不相關的新頁面 |
| 清單 → 主內容 → 按需詳情，固定選取脈絡 | 永遠四欄全開、每個面板重複同一份工作摘要 |
| 預設簡短資訊，按需深入證據 | 一次顯示全部 ID、六態帳目、控制政策與 debug 資訊 |
| 重用 Tauri、Web 共用元件與 Python Connector | 為模仿 Cantus 改 React、重寫 Rust 後端或另建排程器 |
| 保留六種專業 PM 管理視圖 | 把六個 PM 功能刪掉，或要求每次派工先填完六頁 |
| 外部 session 可讀、可關聯、可從固定成果另開工作 | 點歷史就 resume、把連結當接管、把 reassign 當移轉他人 session |
| 能直接操作自己的工作，BAT／IDE 是深入開發選項 | 基本的「再改一下」一定要離開 Dashboard |
| 引用使用者提供的圖作設計參考 | 宣稱未公開介面的完整程式碼已取得，或照搬品牌與圖中資料 |

### 0.3 與現行設計的優先關係

本文件優先調整：導航位置、面板分工、資訊密度、Work／Lineage／Live、就地操作、預設展開與收合。既有 `desktop/DESIGN.md` 的身分隔離、source withdrawal、事件同步、鍵盤與恢復機制要保留；遇到衝突，以**保住資料正確與操作邊界，改善呈現**為解法，不直接覆寫整個模組。[S2]

開工時重新核對 main 和進行中 PR。本次的 #190 已修正原型的直接開工作、就地 PM 審閱與隱藏進階意圖選擇；#191 已增加由共用程式建置的唯讀 demo。這些是重用資產；前者仍是合成互動，後者仍是唯讀展示，不能當作真實閉環已完成。[S3][S4]

---

## 1. 設計參考與使用規則

### 1.1 五張圖的固定名稱

| 編號 | 檔案 | 主要參考 | 不直接照搬 |
|---|---|---|---|
| **A** | `references/A-lineage-polished.jpeg` | 同一選取對象在清單、關聯圖、詳情中保持一致；有方向的連線 | 圖中的任務 ID、名稱、數量與日期；全專案硬套單一階段鏈 |
| **B** | `references/B-lineage-compact.jpeg` | 緊湊欄位、分欄責任、有限數量的節點與局部脈絡 | 很小的字、過淡標籤、重複工作摘要、過度留白 |
| **C** | `references/C-work-detail.jpeg` | **主要視覺基準**：工作標題、目的、進度、證據、執行資源、下一步 | 任何工作都固定五道 approval gate；永久浮動工具列 |
| **D** | `references/D-live-lanes.jpeg` | 主機／執行者泳道、Now／Next／Waiting、局部選取 | 無來源的時間預測、空百分比、拖拉即派工 |
| **E** | `references/E-session-history.jpeg` | 原生會話歷史、來源、只讀能力與可讀對話 | hash 作唯一標題；把歷史閱讀宣稱為即時控制 |

圖由使用者提供，按使用者說明屬非公開版本的視覺參考。公開 `manan45/Cantus` 是另一個可查核的程式基線；不據此宣稱這五張圖的功能已公開或可直接移植。[S5]

**截圖只對閱讀層級與互動方向有參考效力。**例示 ID／文案／數量有不一致時不抄進測試資料。保持 Better Agent Dashboard 的品牌、文字、圖示與資料語意。參考圖僅供交付團隊評審，未经權利確認不得放到公開網站或當成自家產品宣傳圖。

### 1.2 舊版對照

`baseline/` 放入上次提供的兩張 `c8074a3` 原型圖，只用於說明被改的資訊層級；不宣稱它們是本文件最新 main 的實測畫面。每次改版重新產生相同資料、相同 viewport 的前後對照。

### 1.3 視覺驗收不是像素抄圖

必須對齊：工作優先、四區分工、清楚層級、低噪音、可見主要動作、閱讀寬度。不得把「不是完全同色」當失敗，也不得把「顏色很像」當完成。正式資料的未知、錯誤、長標題與權限差異必須能自然放進同一個設計。

---

## 2. 產品原則與使用者角色

### 2.1 正常使用應該很短

- Ted：選專案、說要求、看結果、再改一句；需要親自 coding 才開 BAT／IDE。
- 專案負責人：理解目標與偏差、看證據、做少數有意義的決策。
- Hermes／Grokbot：經 MCP／API 派自己的工作，讀到與 Dashboard 相同的結果。
- 其他開發者：使用自己的 session／worktree；Dashboard 能觀察成果，但不截斷工作。
- Maintainer：沿既有政策產生有界修復，和一般 Work 用相同資料與畫面。

### 2.2 不變的產品邊界

- `server_id + workspace_id` 是管理邊界。同 GitHub repo 在不同 workspace 不自動合併專案紀錄。
- 多 client 讀同一服務；視圖切換不啟動另一個 scheduler、collector 或 writer。
- **外部、未知及其他 controller 的 session 不送字、不 resume、不 interrupt、不改權限、不清理。**增加工作連結不增加控制權。
- PM 使用自己的 worktree／既有標準文件目錄。Idea 可以長期存在，沒有 Work 也正常。
- 由來源格式讀取成果，不要求 coding agent 統一改寫 output。
- 安裝成功、連線成功、可觀察、可執行、已實測是不同能力，必須分開。

---

## 3. 核心資訊模型：使用者看到的是工作，不是內部紀錄

| 概念 | 使用者語意 | 設計要求 |
|---|---|---|
| Project／Workspace | 目前投入或探索的專案脈絡 | 沿用既有綁定，不因名稱／repo 相同自行合併 |
| Discussion | Idea、規劃、Review 等可延續對話 | 沒有執行也能存在；不為了 UI 強制生 Work |
| Work | 一件有目的與交付範圍的工作 | 跨多個 attempts／sessions 仍可維持同一身分 |
| Attempt／Session | 為了工作進行的一次執行／原生對話 | 是工作詳情，不是要求人手管理的第一層導航 |
| Result | 這次執行產生的固定成果與證據 | 與 turn、commit、驗證、PR、接受條件分清 |
| Decision | 一次有來源與影響範圍的決定 | Work、討論與 Overview 引用同一份決策 |
| Observed session | 被讀到的外部工作 | 可以尚未歸類；不得無聲轉成自有 Work／execution |

每個 Work 第一眼只需回答：**為什麼做、誰負責、目前狀況、已產生成果、下一步。**內部主鍵仍保存且可複製，但不占主標題。

---

## 4. 新資訊架構：四個日常入口，專業 PM 在專案內展開

### 4.1 固定 shell

**主要入口：今天 Today／專案 Projects／工作 Work／執行資源 Resources。**

次要入口：活動、連線與設定、進階工具。名稱全站一致，不混用「Workers」代表機器、controller 和 runtime 三種不同物件。

Workspace／服務切換器只出現一次，位於側欄上方；頂列只顯示當前路徑、精簡連線狀態與搜尋入口。預設範圍是目前 workspace。多來源的「全部」只能在使用者選擇後彙整顯示，保留來源標籤，不合併管理權威或進度。

### 4.2 兩個不同層級的視角

- **專案層：**預設 Overview。管理視角透過一個明確的「專案管理」選單／視圖選擇器切換；桌面可以有單列次級導航，但不在主側欄長期展開八項。
- **工作層：**選定 Work 後顯示 `工作 Work｜關聯 Lineage｜現場 Live`。切換只改主內容，不離開目前工作。
- **Idea／討論：**進入討論閱讀面，不必顯示沒有意義的三視角。轉成工作後仍保留返回該討論的入口。
- **資源層：**以主機／controller／runtime 篩選同一 Live 資料；不是第四種工作帳本。

### 4.3 舊功能必須有去處

| 現有入口 | 新位置 | 必須保留 |
|---|---|---|
| Overview／Charter | 專案首頁 | 五個重點、PM 判讀、固定報告版本 |
| Discussions | 專案最近討論；工作內對話與返回原串 | Idea、Planning、Meeting、草稿與來源 |
| Requirements／RTM | 專案管理 → 需求與交付追溯 | 驗收、spec、工程包、issue 映射 |
| Schedule | 專案管理 → 時程與 Sprint | 基準／預測／實際；Sprint 討論 |
| Dependencies | 專案管理 → 技術相依與維護 | 矩陣、維護政策、release baseline |
| Issues & changes | 專案管理 → 問題與變更 | 風險、行動、決策、變更時間線 |
| Agents／資源效能 | Resources ＋專案資源視圖 | 使用量與範圍、主機、runtime 能力 |
| Board | Work 清單的看板呈現 | 相同 Work IDs、排序、篩選、批次 |
| Session／worktree／operation detail | 工作或觀測詳情、搜尋、既有 deep link | 原始記錄與完整追溯，權限不變 |

不得刪掉原 capability、欄位或工具來製造「簡單」。舊 deep link 有相容解析；遷移只改入口與呈現，不以 URL 重新提交任何 mutation。

---

## 5. 畫面骨架與尺寸契約

### 5.1 桌面四區分工

| 區域 | 職責 | 1440px 初始建議 |
|---|---|---|
| A：導航 rail | Workspace、四個日常入口、設定 | 208px，可收為 64px |
| B：對象清單 | Work／Discussion／來源清單、搜尋、必要篩選 | 256px，可調 220–300px |
| C：主工作 canvas | 工作、對話、關聯或 Live | 吃剩餘空間；閱讀寬度優先 |
| D：情境 inspector | 選取對象的證據、計畫或環境 | 320px，可調 300–380px，按需開 |

頂列 48–56px，沿既有 token 採用一個全站值。桌面清單保留獨立捲動，主內容有自己的閱讀捲動；避免內容中再嵌多個小捲軸。

**以上是設計起始值，不是目前已測數據。**容器寬度與最小可讀主欄比固定 viewport 斷點優先。當主欄將小於 520px 時，先收 inspector，再收 rail／清單，不能縮字硬塞。

### 5.2 Responsive 行為

| 尺寸／情境 | 必須做到 |
|---|---|
| 1440×900、1920×1080 | 可視資料允許時四區同時存在；主標題、目的、狀態與主要操作在首屏 |
| 1280×800 | 收 rail 或 inspector，保留足夠主內容；不要求顯示全部欄 |
| 1024／768 寬 | 清單與詳情主次分頁，inspector overlay；切換保留選取 |
| 390×844 | 單主面＋Back／清單 sheet；對話輸入固定於可見底部，不被鍵盤遮住 |
| 320 CSS px／放大 | 一般閱讀與操作可重排；圖／diff 如需雙向移動，侷限在標示的局部容器並提供文字替代 |

原生 Tauri titlebar、Windows／macOS controls、browser header 各用平台適配；不在 Windows 畫一組假的 Mac 紅黃綠按鈕。

### 5.3 Inspector 的固定規則

同時最多一個情境 inspector。它必須標示正在檢視誰；原工作是主選取，圖上選中的 dependency 可以只是 inspected object。點「開啟這件工作」才改主選取，避免上方標題屬於 W-A、右側卻無聲切成 W-B。

Idea 預設關閉；Planning 打開草稿對照；執行中預設關閉；審阅成果時按需打開 diff／assessment。空面板不占空間。

---

## 6. Today：先給價值，不先給數字牆

頁面優先顯示：

1. 至多兩項目前值得處理的 PM 建議／決策，有理由、來源及影響目標。
2. 正在做、等待回覆、待看成果的工作，按相關性與時間排列。
3. 上次離開後的主要變化，連回固定工作／PR／決策。

所有項目可就地展開對話或成果。沒有新事項時直接說「目前沒有需要你處理的新事項」，不要假裝專案已完成。

PM 未配置時可以呈現程式可確認的事實與明確設定入口，不用假模型文案補齊卡片。模型判讀是否更新，遵守既有預算與觸發政策；刷新 UI 不自動收費。

指標必須標明涵蓋範圍；已載入 50 筆不是總共 50 筆。失聯來源、未驗收需求與完成事項不得被合成一個總健康分数。

---

## 7. Project：六種管理視圖保留，日常首頁不被模板綁架

專案首頁用一小段目前目標、一段有來源的 PM 判讀、近期成果，以及少數阻礙／待決事項起頭。Charter、歷史報告、完整資源與風險資料放次級區域。

- 五個重點仍是目標／交付／阻塞／偏差／決策；不是五份不同資料。
- Idea 專案可以只有討論與參考 repo；標示尚未採用開發計畫，不算落後。
- Sprint／Milestone 是可深入的管理視圖，不是每次小修復的必經流程。
- 關聯工作的接受、merge、deploy 同源更新六個管理視圖。
- Continue development／Maintain／Paused／Closed 沿既有政策。新 Idea 不改正在跑的 Sprint。
- 維護工作用相同 Work 畫面，關聯其 ReleaseBaseline、finding 與 Maintenance Review。
- 固定報告是當時快照；不能把它與「目前狀況」混在同一個自動刷新頁。
- 文件、SVG、簡報沿既有共同快照能力；本次不新增 export 平台，也不以輸出格式阻擋真實工作閉環。

---

## 8. Work：本次最重要的主畫面

### 8.1 第一屏的必要內容

順序固定為：

**工作標題 → 目的／本輪要求 → 一行責任與位置 → 目前狀態／下一步 → 最近成果或正在進行的內容。**

示意文案（非實際專案數據）：

> 修正登入重複送出　待檢視\
> 避免快速點擊造成重複請求，並補上失敗後重試的測試。\
> 你的工作 · Claude 經 BAT · demo-host\
> 已產生修改，兩項測試通過；下一步檢視變更並建立 PR。

不可在正文之前列六行 controller ID、host ID、profile digest、plan revision。原始主鍵進 Details；顯示名稱旁保留必要來源辨識。

### 8.2 主內容按狀態變化

| 狀態 | 主內容 | 主要操作 |
|---|---|---|
| 草稿／短計畫待確認 | 目標、範圍、接受條件、目的地摘要 | 採用並開始；稍後做為次要動作 |
| 已受理／啟動中 | 現在正在準備什麼、最近回執與時間 | 觀察狀態；詳情可展開，不製造假 live 輸出 |
| 執行中 | 連續對話、公開工具活動與有界 stream | 傳送下一步，預設排本輪之後 |
| 等待輸入 | 原問題、必要選項與背景 | 回答該問題；額外工作指令另排隊 |
| 本輪結束 | 完成本輪摘要、未驗證／可用成果 | 查看成果或送下一步 |
| 有成果待檢視 | 變更摘要、檔案、驗證、接受條件 | 檢視變更；再依交付目標提供 PR／驗收 |
| 部分成功／結果未知 | 已完成到哪、目前未知在哪 | 查回原操作／恢復未完成步驟，不重新 start |
| 超出 scope／需决策 | 差異、影響、建議 | 就地和 PM 討論／審閱指定變更 |
| 已達本次交付目標 | 達成了什麼、證據與仍未發生的後續 | 查看 PR／成果；允許另開新工作 |

主要操作由伺服器允許的動作與使用情境共同決定；不是用按鈕顏色代替授權。每個當前操作區最多一個實心主要 CTA，危險操作進具名選單。

### 8.3 工作內部步驟

進度採該工作的實際計畫與證據，不硬套參考圖的 Build → Sign → QA → Approval → Release。小修復可以只有「實作／測試／PR」；研究可以是「資料／比較／結論」。步驟不是 sessions 計數。

若後端沒有 step 資料，就顯示可靠的最近活動與整體狀態，不編一條看似完整的時間線。PM 產生的候選步驟要標示建議，採用前不成為執行依賴。

### 8.4 結果的閱讀順序

1. 本輪產出了什麼、對本工作有什么影響。
2. 更動檔案、範圍與 diff；長檔以按需載入呈現。
3. 測試／驗證結果、執行版本、未覆蓋部分。
4. 本次交付目標及下一步。
5. 詳細證據：self-report、verification、acceptance、PR、merge、deploy 各自保留。

不要預設渲染六行「已有紀錄／尚無紀錄」。例如只要求開 PR，開好 PR 後可顯示「本次交付目標已達成：PR #48」，旁邊保持「尚未合併／業務驗收依需求」。不可把達成 PR 目標改寫成整個產品功能已驗收。

---

## 9. Discussion 與主要輸入框：從 Idea 到第二句，保持自然

### 9.1 Idea 的預設

- 進入就能輸入，建立討論標題不阻擋聊天；可自動給候選標題並讓使用者改名。
- 主要動作是「傳送給 PM」；只保存放次要動作且標示未呼叫模型。
- PM 的角色、可用性與當前脈絡一行足夠；未連接時保留草稿並導向修復。
- Idea 不自動變 proposal，更不因貼到含有 `/apply` 的文字就開工。
- PM 可在原串提出「整理成計畫」；不必每句選 discuss／plan／do。進階意圖留在訊息選项。

### 9.2 輕量 Planning

短計畫只包含：目的、範圍、接受條件、執行 profile、交付上限。現有設定自動帶入；高影響改動依政策補問。

「採用並開始」立即顯示新 Work 的 preparing 狀態，之後跟著事件更新。詳盡步驟仍可展開；不先展示八步完成表，再要求多點一次「開啟工作」。

採用成功、派工失敗時顯示兩件事的真實狀態，只恢復未完成步驟；回討論仍看得到採用版本。

### 9.3 自有工作的第二句

- Work 中的輸入框預設指向目前 controller 的工作，不再次問 repo、session ID、spec。
- busy 時由既有服務保存 next-turn 輸入與回執。排隊成功清空輸入，但保留訊息；失敗保留草稿。
- 本輪是否真正結束由 adapter／server 證據判定，不能只看 streaming=false。
- 不用 replay 整段對話把已送出的第一句重新執行。
- 切換 Work 後草稿屬於原 Work，不能送到新選取的工作；重連查原 operation key。
- 有明確 pending question 時，回答與追加新工作分開。下一輪 input 不能被誤送成工具授權答案。

### 9.4 就地討論與審閱

Work Result、討論側欄與 Project Overview 都能打開同一筆 assessment／decision。採用後原位更新，不強迫使用者去 Overview「拍板」後再回來。

launch context 至少帶上穩定的 workspace、work／thread、選取 result、spec revision、來源問題與返回位置。UI 顯示可理解的標題；真正版本由後端核對。不能只跳到一個空的新增討論表單。

---

## 10. Lineage：回答前因後果，不另外建立流程引擎

### 10.1 內容與關係

允許的節點：目標、需求／spec、Work、必要的 Attempt／Result、PR／Release。預設把 attempts／sessions 收在 Work 下；按需展開才看完整技術 lineage。

**邊必須有明確類型：**

| 邊 | 含義 | 不等於 |
|---|---|---|
| fulfills／對應 | 工作服務哪個需求 | 需求已通過驗收 |
| depends_on／依賴 | 有明確條件的前置關係 | 舊工作比較早發生 |
| derived_from／衍生 | 從固定成果與背景另開工作 | 原 session 的控制權轉移 |
| produced／產生 | 執行到固定結果 | 整個 repo 差異都由該 session 產生 |
| integrated_into／整合 | 固定結果進入 PR／版本 | 已部署或被使用者接受 |

每条正式邊有來源；PM 推測的關係用不同樣式和文字標示「建議」，確認前不作排程依賴。資料有循環時如實標問題，不自動修 spec；關係圖不是所有類型都要求 DAG。

### 10.2 交互

初始只顯示所選 Work 與上下游各一層，約 15–30 個節點內；超過顯示聚合及展開數量，不能悄悄截掉。可以聚焦子圖、找上游、列相依。

點節點開 inspector；雙擊／明確開啟才改主 Work。點空白清 inspector，不清主要選取。返回保留原 viewport。鍵盤和關係清單也能完成相同操作，不必依賴拖拉。

階段欄可以輔助分組，但不能因放在 Release 欄就變成 released。Idea／Planning／Sprint 等既有生命周期不被圖形布局重寫。

**本輪不做：**任意拖拉重排執行 DAG、畫線即建依賴、graph 上的自動 reassign、所有 sessions 同時展開的大圖。

---

## 11. Live：真正的現場狀況，不用動畫假裝控制面已成熟

### 11.1 泳道與欄位

預設以 host 分组，可切 controller／runtime 篩選。顯示「執行中 Now／已排下一步 Next／等待 Waiting」；已排工作來自後端排程，PM 推薦工作另列為建議，不混在實際 queue。

每張工作卡顯示 Work 名稱、主要狀態、執行 runtime、需要時顯示 controller、最近觀測時間。選卡保留同一 Work，右側顯示該工作。點 host 只開 host 詳情或篩選，不讓工作標題與 inspector 身分無聲錯配。

沒有可靠預測時不顯示「2–8 小時」。CPU、記憶體、token、剩餘配額各自有單位與採樣時間，不使用無定義的「68% Available」。

### 11.2 操作語意

瀏覽不產生任何 mutation。暫停、取消、重派只有 backend 宣告可用且有清楚作用目標時顯示。

- 「停止後續派送」不是「終止正在跑的程序」。
- 「另開相同範圍工作」不是「接管原 session」。
- 「請 Hermes 調整」是 owner inbox request，除非原 controller 確認，不顯示已完成。
- 歷史觀測來源不能出現可點擊的 live pause 按鈕。

無 active 工作仍顯示來源就緒情況與合理空畫面；失聯則標示最後已知狀態，不宣稱全部 idle 或完成。

---

## 12. 外部會話與跨工具成果：只讀也能有用

採參考 E 的閱讀框架，但改進清單標題：優先使用原生標題或有來源的摘要；hash 放次要細節。沒有足夠資訊就顯示原始名稱，不由模型猜成果。

來源清單中分清「歷史可讀」「即時觀察」「可建立自有工作」「可追加自有回合」。原生 Claude／Codex／AHP 未完成的能力，沿 v1.3 的 E06–E08 追蹤，不在這次 UX 裡冒充已完成。[S1]

外部 session 的主要操作：查看原文／成果、關聯到既有工作、以固定成果另開工作。要另開時清楚顯示來源 commit／checkpoint 與未帶入的未提交變更；不替來源 add、commit、stash、reset。

同一 session 由 BAT 和 CLI reader 都觀測到，依原生身分與可信 alias 去重。相同標題或同 path 不能證明相同 session；多來源未去重時明示，不讓 PM 重算工作量。

只讀頁不能在底部放一個看似能續作的「聊天輸入框」。搜尋框標「搜尋這段會話」；PM 討論框標「與 PM 討論這份成果」；建立新工作是不同具名動作。

---

## 13. Onboarding 與就緒修復

就緒不是另一張長表單。建立一次 workspace／profile 後，正常派工沿用設定，只在有缺口或即將產生不同影響時詢問。

| 發生什麼 | 應有的引導 |
|---|---|
| 服務未連上 | 留下草稿，修復 Connection，回原位置 |
| BAT 可觀察、PM 不可用 | 顯示兩種能力；仍可依政策使用已就緒 coding 路線 |
| Runtime 未登入／版本不支援 | 提供最少修復步驟與實際執行位置，不靜默換帳號或付費來源 |
| Local Git 未支援 | 說明平台限制，允許選已設定的遠端 profile，不偷偷 local→SSH fallback |
| 有 profile 但未實測 | 不顯示「已驗證」；明確成本的測試執行是獨立動作 |

完成修復必須返回原 thread／Work／draft。首次空 workspace 應提供「開始討論」和「連接現有來源」，不要求先建立完整 PM 資料才能看到價值。

---

## 14. 狀態、文案與主要動作的共同字典

### 14.1 六類事實保持獨立

`執行活動、實作者自述、驗證、需求接受、Git 整合、發布／部署` 保持原來的證據和版本。UI 產生一個可理解的主摘要，但不能覆蓋或偽造這些欄位。

對目前 Work 的主摘要優先說「需要關注的事」：發生故障、等待回答、目前執行、可看成果、已達指定交付。來源不足時明說未知。具體優先順序由例子與狀態測試固定，不能各頁各寫一份。

### 14.2 使用者文案

| 不作主要文案 | 建議文案 |
|---|---|
| operation accepted | 已收到要求，正在準備工作 |
| after_current receipt | 已排到本輪之後 |
| controller mismatch | 這是 Hermes 的工作；可以送建議或另開工作 |
| quality_unassessed | 這份成果尚未依接受條件檢查 |
| already recorded／not recorded 六行 | 已有變更與測試；下一步建立 PR |
| source stale | 顯示五分鐘前的狀況，正在重新連線 |
| no items（讀取失敗） | 暫時無法讀取工作；重試 |
| model completed（無驗證） | Agent 已結束本輪，成果待檢視 |

「確認操作目標」和「逐條批准工程紀錄」分開。批次可由一次明確意圖授權，低風險流程依政策執行；不要為了填滿 Review 卡而要求每份成果都由人按一次。

---

## 15. 共用讀取模型與前端契約

本節是**建議的邏輯介面**，不是宣稱現行 API 已有同名欄位。UX01／E04 必須做映射；能用既有 endpoint、ID、journal 就不用新建一套表或命令。

### 15.1 Work presentation projection

| 欄位組 | 最少內容 |
|---|---|
| identity | server/workspace/project/work refs；可理解 title |
| intent | 目的、採用依據、spec revision、交付上限 |
| responsibility | requester、controller、runtime、host，避免一律命名 worker |
| state facets | 活動、驗證、接受、交付；各自 status/ref/time |
| summary | 事實摘要；可選 PM 解釋，附來源與評估時間 |
| result refs | 固定 commit／patch／artifact、coverage、測試與 PR |
| relationships | typed edges、來源；是否建議關係 |
| actions | action identifier、allowed、blocked reason、必要目標與版本 |
| freshness | observation time、cursor/generation、partial、stale、withdrawn |

同一 projection 供 Work 清單、主頁、Lineage 卡片、Live 卡片與 inspector。API／MCP 回傳同一邏輯事實與可用動作，不一定要求兩種 transport 字節完全相同。

### 15.2 SelectionContext：只存 UI 偏好

UI selection 應包含 namespace（service＋principal）、workspace、project、primary entity、view、inspected entity、return target；draft 以 thread／Work＋目的動作保存。

可以記住 list filter、scroll、panel width、graph viewport。不能把 adopted scope、ownership 或 completed 寫進 localStorage 當權威。重載必須重新從服務驗證仍有權讀到／操作的內容。

### 15.3 事件與撤回

- 共用事件來源，不因三個視角各建一個 listener。
- 可見內容合併刷新；高頻 stream 增量更新，不重建整個 shell。
- 有舊資料但仍有讀權時可以清楚標示最後狀況；目前既有 admission／masking 規則不能因改布局被取消。
- 明確撤回、登出或 workspace 轉換時，清除未授權資料及衍生顯示；延遲 response 以 generation 阻止復活。
- 背景刷新不能搶焦點、清 draft、讓按下的按鈕消失，或把 scroll 拉到底。
- 書寫動作依最新授權與版本重核；UI stale 的畫面不能視作操作授權。

---

## 16. 共同元件與視覺設計

### 16.1 必須共用的元件（名稱為提案）

`WorkbenchShell`、`ContextList`、`WorkHeader`、`WorkSummary`、`NextActionBar`、`ConversationComposer`、`ResultSummary`、`EvidenceInspector`、`PmDecisionPanel`、`LineageCanvas`、`LiveLanes`、`SourceCapabilityLabel`。

沿用現有 `ui/components.js` 的 DOM helpers、tabs、button、inspector、time、icons。不要為 Work／Live 各寫不同的 message composer、assessment button 或錯誤恢復元件。

### 16.2 Design tokens 起始值

| 類型 | 規則 |
|---|---|
| 色彩 | 沿既有 semantic tokens：一個品牌 accent、neutral surfaces、明確 status 色；模組不得硬寫顏色 |
| 字體 | 沿 system／Segoe UI／Noto Sans TC fallback；不因 Cantus 截圖改成專屬字型依賴 |
| 尺寸 | 主標題 24–28px、區塊標題 16–18px、正文 14–16px、輔助 12–13px；主內容不可用 caption 尺寸 |
| 間距 | 4px 基準，常用 8／12／16／24／32；頁首到正文不堆多條 metadata bar |
| 邊框 | 用於層次分隔，不每段再包卡片；陰影主要用浮層 |
| 圓角 | 元件按既有 radius family；不加異常大的 pill 包住整行內容 |
| 玻璃效果 | 可選外框裝飾；文字、表格、diff 背景保持可讀；效果關閉不改功能 |
| 動效 | 短局部轉換；尊重 reduced motion；不可用循環動畫作唯一的「正在工作」證據 |
| 密度 | 清單／表格緊湊，對話／文件寬鬆；支援常用密度偏好，不按頁任意縮字 |

至少交付 light／dark、繁中／英文、寬字體 fallback 下的同一套元件。#190 已有的就地審閱與直接進 Work 保留並移入真正共用來源；#191 的展示必須跟著共用介面更新，不另維護一個更漂亮的獨立 demo。[S3][S4]

---

## 17. 無障礙、鍵盤與效能

### 17.1 無障礙目標

採 WCAG 2.2 AA 相關條件作驗收基準，不用截圖宣告符合標準。[S6–S9]

- 一般文字對比至少 4.5:1，大字至少 3:1；重要控制與圖形依其規則核對非文字對比。
- 指針 target 至少遵守 24×24 CSS px 或合格例外；本產品 mobile 主要操作採 44px 起的設計目標。
- 完整鍵盤操作；dialog trapping 與 Escape／焦點返回；固定底欄不得遮蔽焦點。
- graph／swimlane 有可操作的文字清單替代；不能只靠顏色表示狀態。
- stream 不逐 token 對螢幕閱讀器高頻播報；採有節制的狀態摘要。
- Enter 送出、Shift+Enter 換行等採全站一致行為；IME composition 未結束時不得發送，貼上多行不自動發送。
- command palette 是明確搜尋／命令入口；搜尋本身不啟動模型、不操作 session。

### 17.2 效能目標（尚待實測，不是現在成績）

沿用 v1.3：點擊／Send 本地 pending 回饋 p95 ≤500ms；50 個 sessions／5 個活躍 stream 的基準環境，collector 已接到事件至 UI 更新 p95 ≤2s。模型等待和遠端網路另記，不能混成 UI 性能。[S1]

額外測 200 Works 的清單和含聚合的 graph。預設局部 graph 約 30 nodes，展開上限先設 150 nodes／300 edges 的有界視窗；超量提供 filter／分頁，不凍結畫面。最終上限依 profiling 調整並寫明基準機器。

限定 buffer、分頁 transcript、lazy diff、合併事件與視覺更新。只更新改動的行；不把每一個 token 變成整頁重繪。prototype 速度不能當 live benchmark。

---

## 18. 技術落點與遷移方式

### 18.1 保留現有技術棧

Tauri client、`desktop/src` 共用 Web frontend、Python Connector／TaskDaemon／journal、BAT、API／MCP、GitHub delivery 繼續使用。不得為畫 Lineage 先換框架。圖形布局可先利用既有 SVG／DOM 能力；若需新套件，由 integrator 記錄成本、授權、鍵盤支援與包體影響後採一個，不並行引入兩套。

### 18.2 既有模組的責任映射

| 既有檔案／區域 | 本次責任 |
|---|---|
| `desktop/DESIGN.md`、tokens、ui/components | 更新 shell 與層級契約；保留 focus、withdrawal、時間與錯誤行為 |
| `desktop/src/app.js`、shell／workspace nav | 新導航與相容 route；不要把所有新視圖塞回單一大檔 |
| `pm-workbench.js`、`pm-workspace.js` | 專案管理次級導航、選取與工具落點 |
| `pm-discussions.js`、`pm-plan-proposals.js` | 原位討論／候選變更；接主 Send 與採用並開始 |
| `repository-start.js`、package handoff | 重用既有派工；補 v1.3 API-only 斷點 |
| `pm-overview.js`、assessment 相關 | PM 摘要多入口，同一 decision |
| session／conversation／observed-resource | Work 及外部歷史的內容重用，不混入控制權 |
| inventory／execution/result／relations | 投影映射與有界查詢，按 E04 補必要能力 |
| `desktop/prototype/v1.3/` | 互動參考；將合用元件移入 shared source，保留 isolated synthetic fixtures |
| `desktop/demo/`、browser build | 僅替換 transport／fixture，不另寫 UI 與業務狀態 |

檔名以開工當時實際存在者為準；新的 `workbench/*` 路徑只是組織建議，不強制在未知模組上動刀。

### 18.3 遷移步驟

先做 presentation／selection 映射與可切回的 rollout flag，再逐模組接入。新舊畫面共用資料与操作層，不可 dual-write 兩份 Work。

舊 URL、back／forward、deep link、草稿與讀取權限需有回歸。涉及 schema 必須有 migration／備份／回退說明；不為 UI 重新編 work/session IDs。

先只對候選使用者開新 shell，確認可用後成為預設。舊 shell 保留短期回退，不讓兩套 UX 永久分叉。已發出的 operation 在切換前後都查原紀錄。

---

## 19. 工作包：UX01–UX10

下表是此補充文件的實作拆分，與 v1.3 E 工作包交叉對照；不得另建競爭的 backlog。

| ID | 主要交付 | Owner | 依賴／對應 | 完成證據 |
|---|---|---|---|---|
| **UX01 基線與設計契約** | 核對 main／PR、五圖映射、舊功能去處、SelectionContext／projection mapping | UX integrator＋backend owner | E00/E04；先做 | 沒有孤兒功能；明確 shared source 與缺口表 |
| **UX02 共用骨架與導航** | 四入口、清單／主頁／inspector、responsive、legacy routes | Shell owner | UX01；E03 | 同 Work 跨三視角保留狀態與權限；320–1920 寬可用 |
| **UX03 Work 與成果** | 工作摘要、進度、主要操作、diff／驗證、就地 PM 決策 | Work UI＋backend | UX02；E02/E04/E05 | 第一屏看得懂；真資料下 state/evidence 一致 |
| **UX04 討論到執行** | Idea reply／note、短計畫、採用並開始、第二句、恢復 | Composer owner＋command owner | UX02/03；E01/E02/E05/E10 | 不補 API 資料即可完成兩句；錯誤查回原工作 |
| **UX05 Lineage** | typed graph、局部展開、關係清單、選取與鍵盤 | Visualization＋read model | UX01/02/03；E04/E08 | 真關係可追溯；無資料不造線、不變控制權 |
| **UX06 Live／資源** | Now／Next／Waiting、host/controller 視角、freshness | Live UI＋adapters | UX01/02/03；E01/E04/E05 | 斷線、排隊、觀察三者不混淆；不是動畫演示 |
| **UX07 PM 管理重整** | 六視圖入口、Today、PM 摘要、Idea／維護脈絡 | PM UI＋PM domain | UX02/03；E09 | 保留完整管理功能；來源與決策跨畫面一致 |
| **UX08 外部 sessions** | 歷史閱讀、來源能力、去重、fixed-result derive | Observer＋UX | UX03；E04/E06/E07/E08 | 讀取有用；foreign 操作次數為零；未支援能力如實顯示 |
| **UX09 品質與性能** | 語系、鍵盤、對比、字體、stream 負載、視覺差異 | QA＋frontend | 全程；E11 | 以本文件 48 項驗收提供相應證據 |
| **UX10 實機候選與發布** | 真 BAT＋MCP 循環、版本可辨識安裝包、遷移／demo | Integrator＋QA | 前述核心；E05/E10/E11 | 同一 SHA、真 runtime、真成果、回退可用 |

### 19.1 避免多人 AI 開發把介面拆散

- 一位 UX integrator 擁有 shell、導航、token、SelectionContext 及整體變更合併。
- 一位 contract owner 對 Work／Result／actions 投影負責。
- 各畫面 worker 可平行，但不得自行發明同義狀態、另一種 composer 或另一份 Work store。
- `pm-workspace.js`、`app.js`、shared tokens、migration、operation registry 要分配明確修改範圍，不整檔覆蓋他人的成果。
- 每張 PR 說明：原使用者步驟的問題、現在路徑、來源 SHA、測試、哪些仍是 fixture／未接的能力。
- 復用現有 CI 影響範圍選擇，不為每個 CSS 變更新增一套全平台完整流水線；候選版仍須通過完整必要驗收。

---

## 20. 交付里程碑與停止條件

UX 里程碑使用 **V0–V4**，附掛在原 R0–R5，不宣称取代跨 CLI 的完成條件。

| 里程碑 | 內容 | 對應原計畫 | 可接受的證據 |
|---|---|---|---|
| **V0：完整閱讀骨架** | 參考 C 的 Work、A/B 的 Lineage、D 的 Live、E 的歷史，加上 Idea／Review 的 responsive | R0 | 正式元件的可點擊版與一輪集中設計評審；不能只交 jpg |
| **V1：真實 Work 主路徑** | 同一 shell 下第一句、開始、真 BAT stream、第二句、Result | R1 | 指定測試 repo、runtime 版本、實際 operation／session／PR 證據 |
| **V2：三視角同源** | 真 relations、Live、外部觀察、六視圖新入口 | R1–R3 依能力 | 同一 Work 跨視角一致；MCP 導入可見；未支援標示 |
| **V3：PM 決策與維護回填** | assessment／接受／依賴、PM 判讀、維護修復同畫面 | R4 | 真 Result 到決策與後續 Work；不由人重填狀態 |
| **V4：可發布的 UX 候選** | 平台、語系、故障、遷移、效能、使用者驗收 | R5 | 同一候選版本的完整結果；backend/frontend/adapters 版本明確 |

先鎖定一輪 V0 的整體方向，工程映射與既有修復可同步進行；不為每一個間距和按鈕要求 owner 再批一次。布局採用後的小變更由 UX integrator 負責。

**V1 不必等 Lineage 完整、美術全部精修或所有 CLI 完成才交付。**但 V1 只能標 BAT 路徑；完整 v1.3 的 Claude、Codex、AHP、MCP 要依原計畫完成。

實際 Sprint 日期由 dev 看相依與現有完成度安排，不把本文件的順序轉成沒有估算依據的交期承諾。

---

## 21. 共用驗收資料與演示腳本

### 21.1 一份真實感 fixture，不是每頁各填不同資料

延伸既有 membership golden data。以下為示意別名，正式 fixture 與 journal ID 使用當前 repo 的規則；不建立第二套生產 ID。

- 一個活躍會員專案，以及一個長期停在 Idea、零 Work 的專案。
- `W-01`：Ted 的自有登入重複送出修復，交付上限 PR，先未啟動。
- `W-02`：Hermes 自有的帳號恢復驗證，正在等一個問題回答。
- `W-03`：已有 merged PR，但尚缺一條需求接受證據。
- `W-04`：維護中的有界依賴修復，綁 release baseline。
- 一個 manual BAT session 保持運行；一個 native CLI 歷史只讀来源未歸類。
- 一個 Work 有兩次 attempts；一個 session 同時由兩個 reader 發現；一個 host 失聯。
- 一條已確認依賴、一條 PM 建議關聯、一個長文字標題、一個未知時間的來源。

清單、圖、Live、inspector、Overview 的同 ID 標題和狀態必须一致。頁面不能各自調整數據來讓畫面漂亮。每張 evidence 具 source/version/observed_at。

### 21.2 七段完整腳本

1. **Idea**：貼 repo、聊兩輪，存一則筆記；沒有 coding operation。說「整理成 plan」後看候選差異。
2. **直接做事**：選 W-01，採用並開始，立即看 preparing／running 真狀態，不再按開啟工作。
3. **第二句**：busy 時追加失敗重試案例，顯示 queued → 已傳送 → 新結果；不重送第一句。
4. **就地審閱**：看 diff／測試／PM assessment，原位採用或派補；Overview、Lineage同步。
5. **協作**：Hermes 經 MCP 開另一 Work；在人類 UI 只能觀察、inbox 或 derive，不能往它 session 注入。
6. **觀察與視角**：點外部歷史，讀來源；只從固定成果另開。選同 Work 切三視角，保留草稿與判斷背景。
7. **維護與故障**：在低風險測試 repo 發布／維護；過程中斷線與重開 UI；查回原操作，沒有新屍體 session。

錄影分清模擬與實機。不能在演示前手動塞 work package、完成評估或假 PR，然後宣稱使用者路徑完整。

---

## 22. 驗收情境：48 項，按情境不是按元件數計分

完整逐項表見同目錄 [驗收清單](workbench-ux-acceptance.md)；每項包含預期結果與證據層級。以下規則適用全部項目：

- `P`：原型／fixture 互動；`I`：接真正 Connector 的整合；`L`：真實 BAT／CLI／MCP／GitHub 環境。
- P 通過不能代替 I／L。視覺同意也不能代替操作正確。
- 不透過關掉 ownership、吞錯、改 fixture 成功、減少必要覆蓋或只加 timeout 讓測試通過。
- 同一問題有 source failure 時保留其證據，後續修正以新版本重跑。

### 22.1 產品負責人看什麼

**第一眼：**能否說出現在選了什麼、做到了哪、下一步什麼。\
**第二句：**能否自然追加工作而不重貼背景。\
**第三個視角：**切到關聯或現場後是否仍知道原工作在哪。\
**專業 PM：**是否更容易作決定，而不是多了一組必填表。\
**不中斷：**原本 BAT 的人工與其他 agent 工作是否完全沒被操作。

### 22.2 必備視覺證據

同一 SHA 至少產出：Today、Work running、Work result＋review、Idea、Planning、Lineage、Live、external history；每張記錄 locale、viewport、資料性質、theme、route。主要流程覆蓋 desktop／mobile，並測窄窗與 fallback font。

另做 200 Works／多個活躍 stream 的壓力情境。不能只有五張卡片的乾淨畫面。畫面對照是證據之一，還需操作錄影與狀態斷言。

---

## 23. 完成定義與提報格式

### 每個工作包完成時

提交：實際來源 SHA、與前版的使用者步驟差異、共用元件變更、API／projection 映射、例外行為、測試結果、尚未支援部分。

有畫面變更必須附相同資料與相同 viewport 的 before／after。提供至少一段從起點到結果的連續操作，不以多張不同狀態 screenshot 拼成假的閉環。

### 本輪 UX 完成時

- 一般閉環零次必須切外部工具、零次重填背景或內部 ID。
- 三視角和專案管理頁使用相同來源，已採用決策與驗收狀態不分叉。
- 六種 PM 管理能力有明確新入口；舊 deep links／歷史／工具未遺失。
- 真實 BAT 第一、第二句、結果、PR、MCP 工作都能在新 shell 完成。
- 所有支援的 reader 能力如實呈現，foreign session 控制次數為零。
- 完整 v1.3 是否完成仍按 C1/C2/C3 和 E01–E11 判定，不用本 UX 文件縮小跨工具承諾。
- Demo／原型／安裝版／live 的版本與證據明確分開；無需使用者猜自己看到哪一套。

---

## 24. 常見偏離與立即校正

| 偏離 | 校正 |
|---|---|
| 只改漸層、陰影、圓角 | 回到 Work 主體／列表／inspector 的閱讀層級與流程驗收 |
| 直接套 Cantus 的 logo、文字、ID 或未公開行為 | 只借用結構，保留自家品牌與真實資料模型 |
| 三視角變三份 local state／資料庫 | 收斂同一 projection 与 selection，業務狀態由 server 作準 |
| 為了 Lineage 新寫 scheduler | 圖只呈現關係，依賴執行沿既有 Orchestrator |
| 美化必須先換 framework | 停止；先用既有 DOM 元件、tokens 和 adapter 完成 |
| 開始後仍只回 operation ID | 原 Work 自動接上 preparing／output，ID 退到詳情 |
| PM 又要求手動填完工作包 | 從已採用上下文組合既有命令，保留每步結果 |
| 全部頁面都有同樣的 Review／Approve | 按當前工作交付目標與實際權限選主要操作 |
| 看得到外部 session 就增加 resume | 移除，改固定成果另開／owner inbox |
| 缺資料就顯示空白或 0 | 分清未配置、未知、部分、讀取失敗與真正空資料 |
| 示範版更漂亮，真 app 仍是舊功能頁 | 共用元件和 data adapter；demo 不准另寫 UI 分支 |
| 所有工程都等下一輪設計確認 | 一輪鎖骨架，工程 mapping 同步；小調整由 integrator 決定 |

---

## 25. 來源、查核範圍與文件交付

### 本次查核

核對目前 main ref、#191 commit、現有設計與 v1.3 計畫，並根據使用者已提供的五張參考圖制定本提案。沒有在此文件交付中修改 GitHub、宣告 R0 通過、啟動 agent、操作 BAT 或執行實機驗收。

### 來源

- **[S1]** 採用的 v1.3 閉環計畫：<https://github.com/TED-H-com/bat-agent-connector/blob/10f15ef4e4c8676a4605fbfbd2e3c576597303fa/docs/product/v1.3-closed-loop-plan.md>
- **[S2]** 現有共用設計契約：<https://github.com/TED-H-com/bat-agent-connector/blob/10f15ef4e4c8676a4605fbfbd2e3c576597303fa/desktop/DESIGN.md>
- **[S3]** 已合併 #190 的原型改正：<https://github.com/TED-H-com/bat-agent-connector/commit/c8074a320163c34ecc8d0bd610533742600f91b2>
- **[S4]** 已合併 #191 的共用 demo／展示邊界：<https://github.com/TED-H-com/bat-agent-connector/commit/10f15ef4e4c8676a4605fbfbd2e3c576597303fa>
- **[S5]** Cantus 公開基線（不等於未公開參考介面）：<https://github.com/manan45/Cantus/tree/f781cfe7468f2d8f88079ca3ea788c49a1838d01>
- **[S6]** W3C WCAG 2.2：<https://www.w3.org/TR/WCAG22/>
- **[S7]** W3C Reflow：<https://www.w3.org/WAI/WCAG22/Understanding/reflow.html>
- **[S8]** W3C Contrast minimum：<https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html>
- **[S9]** W3C Target size minimum：<https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html>

所有版面尺寸、元件名稱、投影欄位與性能目標均屬本提案；開工時先映射現有實作，不得把它們當作已存在的公開 API。引用來源支援基線與標準；設計判斷與新功能責任由本計畫明定。

**給整個團隊的一句話：保留系統處理複雜事情的能力，但把使用者面前的工作整理簡單。讓人說了就能做、做完能接著改、換視角仍看著同一件事，PM 在旁邊解釋目標與成果，而不是要求人先操作一套後端資料模型。**
