"""Generate the bilingual public introduction. Product data stays on the central."""

import argparse
import json
from html import escape
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "site"
repo = "https://github.com/TED-H-com/bat-agent-connector"
image_sizes = {entry["file"]: entry["image_size"] for entry in json.loads((root / "images/manifest.json").read_text())["images"]}


def bi(en, zh, tag="span", cls=""):
    attr = f' class="{cls}"' if cls else ""
    return f'<{tag}{attr}><span class="lang-en">{en}</span><span class="lang-zh" lang="zh-Hant">{zh}</span></{tag}>'


def link(url, en, zh, cls=""):
    attr = f' class="{cls}"' if cls else ""
    return f'<a href="{url}"{attr}>' + bi(en, zh) + "</a>"


def doc(path, en, zh, cls="text-link"):
    return link(repo + "/blob/main/" + path, en, zh, cls)


def heading(kicker, kz, title, tz, desc="", dz=""):
    return (
        '<div class="section-heading">'
        + bi(kicker, kz, "p", "eyebrow")
        + bi(title, tz, "h2")
        + (bi(desc, dz, "p", "lede") if desc else "")
        + "</div>"
    )


nav = "".join(
    link("#" + key, en, zh)
    for key, en, zh in [
        ("product", "Product", "產品"),
        ("workflow", "Workflow", "流程"),
        ("architecture", "Architecture", "架構"),
        ("windows", "Windows Fleet", "Windows Fleet"),
        ("start", "Get started", "開始使用"),
        ("docs", "Docs", "文件"),
    ]
)
parts = [
    f'''<a class="skip-link" href="#main">{bi("Skip to content", "跳到主要內容")}</a>
<header class="site-header"><div class="header-inner">
<a class="brand" href="#top"><img src="logo.svg" width="32" height="32" alt=""><span>Better Agent<span class="brand-sub">Dashboard</span></span></a>
<button class="menu-button" type="button" aria-expanded="false" aria-controls="site-navigation">{bi("Menu", "選單")}</button>
<nav class="site-nav" id="site-navigation" aria-label="Navigation / 導覽">{nav}
<div class="language-switch" role="group" aria-label="Language / 語言"><button type="button" data-language="en" aria-pressed="true">EN</button><button type="button" data-language="zh" aria-pressed="false">繁中</button></div>
<a href="{repo}" class="source-link">GitHub <span aria-hidden="true">↗</span></a></nav></div></header>
<main id="main"><section class="hero section" id="top"><div class="hero-grid"><div>'''
]
parts += [
    bi("See the project.<br>Decide with the PM.", "看懂專案，<br>與 PM 完成決策。", "h1"),
    bi(
        "Understand the project, find a problem and discuss it with the PM. Review a proposed update and adopt it in the same project workspace, shared by Web and desktop.",
        "先看懂專案狀況，找到問題，再直接與 PM 討論。從原紀錄帶入脈絡、審閱提案到明確採用，都在同一個專案裡完成；Web 與桌面版共用介面。",
        "p",
        "hero-description",
    ),
    '<div class="actions">'
    + '<a href="demo/?lang=en" class="button primary lang-en">Open interactive demo</a>'
    + '<a href="demo/?lang=zh-TW" class="button primary lang-zh">開啟互動展示</a>'
    + link("#start", "Install and start", "安裝與開始使用", "text-link")
    + "</div>",
    bi("Explore the current Dashboard with sample data. The v1.3 workflow preview simulates start, follow-up and decision. The real Start work panel is on main and needs a configured BAT host and a bound repository.",
       "用展示資料操作目前的 Dashboard。v1.3 工作流程預覽會模擬開始工作、追加修改與驗收決策。主線上已有真正的「開始工作」面板，它需要已設定的 BAT 主機與已綁定的儲存庫。", "p", "hero-description"),
    '</div></div></section>',
]
release_note = (
    '<div class="release-note">'
    + bi("Development preview", "開發預覽", "strong")
    + bi(
        "Screenshots pin merged main at 437ee6a7143a82e32eae1d6a008203b2df072aaf, the published 0.2.13 / 0.1.9 prerelease through #395 on October 22, 2026. They show the synthetic Dashboard in Development: Needs attention, a four-section live charter, an unadopted discussion proposal, twelve register records, and a manual session whose host is not set up for managed work. The gallery does not open Start work or the Result section. Gallery images do not establish installation or live acceptance. Match the package to its source before installing.",
        "截圖固定於已合併主線的 437ee6a7143a82e32eae1d6a008203b2df072aaf，即 2026 年 10 月 22 日已發布的 0.2.13／0.1.9 預覽版（截至 #395）。畫面是開發階段的合成 Dashboard：需要關注、四段即時專案章程、一份尚未採用的討論提案、十二筆登記，以及人工工作階段寫明此主機尚未設定受管工作。圖庫沒有打開「開始工作」或成果區。圖庫不代表安裝或實機驗收；安裝前請核對套件來源。",
    )
    + link("#status", "Current scope", "目前範圍")
    + "</div>"
)
parts.append('<section class="section product-tour" id="product" aria-label="Product tour / 介面導覽">')

shots = [
    ("overview", "Overview", "專案總覽",
     "Live Overview in Development: Needs attention, honest progress and a four-section charter. Saved reports stay separate.",
     "開發階段的即時總覽：需要關注、誠實進度與四段專案章程。已存報告另外保留。"),
    ("discussions", "Discussions", "討論",
     "The proposal stays in the conversation, Proposed and Unresolved. Discussion info is not open over it. Save note only and Make a plan are shown. This synthetic session says PM availability is unknown.",
     "提案留在對話中，狀態是建議、未決。討論資料沒有蓋在上面。畫面提供只存筆記與整理計畫。這個合成工作階段顯示 PM 可用性未知。"),
    ("requirements", "Requirements", "需求",
     "Expand the email sign-in row and read its work inspector. Acceptance, merged PRs and partial delivery each retain their evidence.",
     "展開電子郵件登入需求與工作詳情，分開查看驗收、已合併 PR 及部分交付證據。"),
    ("issues", "Issues &amp; changes", "問題與變更",
     "Both pages show twelve records, including named maintenance findings and one adopted decision. Open records stay open; one has no known age.",
     "兩頁共十二筆，含具名維護發現與一項已採用決策。未結案項目保持原狀態；其中一筆沒有已知時間。"),
    ("schedule", "Schedule", "時程",
     "Compare recorded baseline, forecast and actual dates. Recovery review is forecast three days late. Unscheduled (6) stays collapsed.",
     "對照已記錄的基準、預測與實際日期。復原審查預測晚三天。未排期（6）保持收合。"),
    ("dependencies", "Dependencies", "技術相依",
     "Read a pinned inventory graph with evidence styles and explicit partial coverage.",
     "查看固定版本的相依圖，分清證據種類與部分涵蓋。"),
    ("board", "Board", "工作看板",
     "Twelve demo work items in Not started, In progress, Waiting, In review and Content approved. Content approval stays separate from requirement acceptance and deployment.",
     "十二項展示工作分在未開始、進行中、等待中、審閱中與內容已核准。內容核准與需求驗收、部署各自獨立。"),
    ("session", "Session", "工作階段",
     "Read the manual BAT session on demo-host. View only says this host is not set up for managed work. There is no send or interrupt control.",
     "閱讀 demo-host 上的人工 BAT 工作階段。僅供檢視寫明此主機尚未設定受管工作。沒有送出或中斷控制。"),
]
parts.append(
    '<div class="tour-controls" role="group" aria-label="Screenshot view / 介面展示">'
    + bi("View:", "檢視：", "span", "tour-label")
)
for key, en, zh, _, _ in shots:
    parts.append(
        f'<button type="button" data-view="{key}" aria-pressed="{str(key == "overview").lower()}" aria-controls="view-{key}">'
        + bi(en, zh)
        + "</button>"
    )
parts.append("</div>")
for key, _, _, en, zh in shots:
    parts.append(f'<figure class="product-shot" id="view-{key}">')
    for lang, alt in [("en", en), ("zh", zh)]:
        size = image_sizes[f"{key}-{lang}.png"]
        parts.append(
            f'<a class="lang-{lang} image-link" href="images/{key}-{lang}.png" aria-label="{escape(alt)}"><img src="images/{key}-{lang}.png" width="{size["width"]}" height="{size["height"]}" loading="lazy" alt="{escape(alt)}"></a>'
        )
    parts.append(
        "<figcaption>"
        + bi(en, zh)
        + bi(
            "Actual UI · synthetic demo data · open image for full size",
            "實際介面 · 合成展示資料 · 點圖看原尺寸",
            "span",
            "caption-meta",
        )
        + "</figcaption></figure>"
    )
parts.append(
    release_note
    + doc("site/images/README.md", "Screenshot sources and reproduction →", "截圖來源與重拍方式 →")
    + '<div class="principles">'
)
for en, zh, d, dz in [
    (
        "Activity has a source",
        "活動要有來源",
        "A session going quiet does not establish completion.",
        "session 安靜下來，不能直接判斷已完成。",
    ),
    (
        "Acceptance has a reason",
        "驗收要有理由",
        "Record the criterion, result, evidence and responsible reviewer.",
        "保留條件、結果、證據與負責審閱者。",
    ),
    (
        "Recovery keeps the original",
        "恢復沿用原紀錄",
        "Read back an uncertain operation before starting another.",
        "結果未知時先查回原操作，再決定下一步。",
    ),
]:
    parts.append("<div>" + bi(en, zh, "h3") + bi(d, dz, "p") + "</div>")
parts.append("</div></section>")

parts.append(
    '<section class="section" id="workflow">'
    + heading(
        "One small project first",
        "先從小專案開始",
        "One project, from problem to decision.",
        "同一個專案，從問題走到決策。",
        "Try member sign-in and password reset. Keep the project selected as you move from its status to a sourced discussion and a reviewable update.",
        "先試會員登入與忘記密碼的小專案。從總覽找到問題、帶入討論，到審閱更新，都保留同一份專案脈絡。",
    )
    + '<ol class="workflow">'
)
for i, (en, zh, d, dz) in enumerate(
    [
        ("Understand the state", "看懂狀況",
         "Open Overview for the goal, accepted requirements, blockers and pending decisions. Read saved reports separately.",
         "在專案總覽看目標、已驗收需求、阻礙與待決事項；需要回顧時再切換已存報告。"),
        ("Find the problem", "找到問題",
         "Open a record in Requirements, Issues & changes or Schedule and inspect its original evidence.",
         "從需求、問題與變更或時程開啟紀錄，查看原始證據與下一步。"),
        ("Discuss with PM", "與 PM 討論",
         "Use the record's Discuss action. Its context and title follow you into Discussions; save a note and ask the PM.",
         "按紀錄上的討論入口，來源與標題會一起帶入；寫下想法，再請 PM 提案。"),
        ("Review and adopt", "審閱並採用",
         "Check the proposed wording, retained requirements and open questions. An authorized owner explicitly adopts the reviewed update.",
         "核對建議文字、保留需求與未決問題，再由有權限的負責人明確採用。"),
    ],
    1,
):
    parts.append(
        f'<li><span class="step-number">0{i}</span><div>' + bi(en, zh, "h3") + bi(d, dz, "p") + "</div></li>"
    )
parts.append(
    "</ol>"
    + bi(
        "Adoption needs the PM-owned Git worktree and owner authority. It does not start work or accept a requirement. Passing tests, merging and deployment retain their own evidence.",
        "採用需要 PM 自有 Git 工作區及工作空間擁有者權限。採用不會直接開工或完成需求驗收；測試、合併與部署各自保留證據。",
        "p",
        "section-note",
    )
    + doc(
        "docs/getting-started.md#try-one-pm-workflow",
        "Follow the PM guide →",
        "閱讀 PM 使用指南 →",
        "text-link lang-en",
    )
    + doc(
        "docs/getting-started.zh-TW.md#試跑一條-pm-工作流程",
        "Follow the PM guide →",
        "閱讀 PM 使用指南 →",
        "text-link lang-zh",
    )
    + "</section>"
)

parts.append(
    '<section class="section" id="features">'
    + heading(
        "The daily work", "日常工作", "Keep decisions close to their evidence.", "讓決策與證據放在一起。"
    )
    + '<div class="feature-list">'
)
for en, zh, d, dz, path in [
    ("Overview", "專案總覽", "Live goal, progress, blockers and recent decisions. Unknown sources stay distinct from complete empty records; fixed reports remain separate.",
     "看即時目標、進度、阻礙與近期決策。未知來源與完整空紀錄分開呈現，固定報告另讀。", "pm-overview-discussion-context.md"),
    ("Discussions", "討論", "Search source-bound discussions, save notes and review proposed updates. Context, Inputs and Plan open in Discussion info. Reported issue resolution remains unassessed.",
     "搜尋有來源的討論、儲存筆記並審閱建議更新；討論資料按需開啟脈絡、資訊與計畫。問題解決回報仍為未評估。", "pm-overview-discussion-context.md"),
    ("Requirements", "需求", "Expand requirements for criteria and bound work. Inspect acceptance, PRs and deployment; unchanged refreshes keep rows and focus stable.",
     "展開需求查看條件與關聯工作，再開詳情對照驗收、PR 與部署；資料未變的更新保留列位置與焦點。", "pm-project-control-center.md"),
    ("Issues &amp; changes", "問題與變更", "A filterable register with named findings, original records and processing timelines.",
     "用篩選登記表追蹤具名發現、原紀錄與處理時間線。", "pm-register.md"),
    ("Schedule", "時程", "Compare recorded dates on a labelled axis. Missing dates stay unknown.",
     "在標示日期的時間軸上對照已記錄日期；缺少的日期保持未知。", "pm-schedule.md"),
    ("Dependencies", "技術相依", "Pinned inventories, evidence styles and bounded impact reads in a matrix or graph.",
     "用矩陣或相依圖查看固定清冊、證據與有界影響。", "pm-dependencies.md"),
    ("Agents", "Agent 資源", "Sourced usage, rows and daily trends share one consistent read. Reported quota stays separate; unavailable data stays unknown.",
     "以同一次讀取呈現用量、來源列與每日趨勢；回報配額另列，無法取得的資料保持未知。", "agent-resource-observation.md"),
    ("Board", "工作看板", "Search, filter by requirement or agent, and open the shared work inspector. Work batches are in More actions.",
     "搜尋、依需求或 Agent 篩選，再開啟共用工作詳情；更多操作可開啟工作批次。", "pm-kanban.md"),
    ("Governance and meetings", "治理與會議", "Open Governance and Follow-ups from Issues &amp; changes. Meeting threads have a Meeting tab in Discussion info. Outcomes keep their fixed sources.",
     "從問題與變更開啟治理與後續追蹤；會議類討論的資料面板另有會議分頁。結果保留固定來源。", "pm-governance.md"),
    ("Maintenance", "維護", "Dependencies holds Release baselines and Maintenance policy. Preview a release before registration; adopted policy keeps explicit repair bounds.",
     "技術相依提供發布基準與維護政策；登錄前先預覽發布來源，已採用政策保留明確修復限制。", "pm-release-baselines.md"),
    ("Work tools", "工程工具", "Requirements → More actions opens quality, evidence, specification, package, handoff and issue publication tools.",
     "需求的更多操作開啟品質、成果證據、規格、工程工作包、工作包交接與議題發布工具。", "pm-work-packages.md"),
    ("Session", "工作階段", "Read conversations and results; authorized managed controls coexist with read-only manual sessions.",
     "一起讀對話與成果；自有工作依權限操作，人工工作階段保持唯讀。", "session-reading.md"),
]:
    parts.append(
        "<article>"
        + bi(en, zh, "h3")
        + bi(d, dz, "p")
        + doc("docs/design/" + path, "Read the contract →", "閱讀設計合約 →")
        + "</article>"
    )
parts.append("</div></section>")

parts.append(
    '<section class="section" id="architecture">'
    + heading(
        "Clear responsibilities",
        "清楚的分工",
        "One central history. Work on the chosen host.",
        "同一份中央歷史，工作留在選定主機。",
        "Project management builds on Connector, BAT and Fleet. Web and Tauri share the same product surface.",
        "專案管理建立在 Connector、BAT 與 Fleet 之上；Web 和 Tauri 使用同一份產品介面。",
    )
)
parts.append(
    '<figure class="architecture"><div class="native-lane">'
    + bi("Windows Fleet: connection and startup", "Windows Fleet：連線與啟動", "h3")
    + bi(
        "Manage selected SSH tunnels, route readiness, BAT profiles and sign-in choices. Rust and the existing PowerShell backend follow explicit ownership and migration rules.",
        "管理選定 SSH tunnels、路由就緒、BAT profiles 及登入選項；Rust 與既有 PowerShell backend 遵守明確的歸屬與遷移規則。",
        "p",
    )
    + '</div><div class="client-row">'
)
for en, zh, d, dz in [
    ("Web Dashboard", "Web Dashboard", "Authenticated browser entry", "已認證的瀏覽器入口"),
    ("Tauri desktop", "Tauri 桌面", "Shared UI, native windows and credentials", "共用介面、原生視窗與憑證"),
    (
        "CLI / MCP / agents",
        "CLI／MCP／Agents",
        "Each client has its own principal",
        "各 client 使用自己的身分",
    ),
]:
    parts.append("<div>" + bi(en, zh, "strong") + bi(d, dz, "small") + "</div>")
parts.append(
    '</div><div class="central-node">'
    + bi("Python Connector / Task Service", "Python Connector／Task Service", "strong")
    + bi(
        "Plans · permissions · dispatch · evidence · durable operations",
        "計畫 · 權限 · 派工 · 證據 · 持久操作紀錄",
    )
    + "</div>"
    + bi("↓ Governed BAT operations ↓", "↓ 受控 BAT 操作 ↓", "p", "flow-label")
    + bi("Selected BAT host and workspace", "選定的 BAT 主機與工作區", "h3", "bat-node")
    + '<div class="resource-row">'
)
for en, zh, d, dz in [
    (
        "Your coding resources",
        "人的開發資源",
        "Keep using BAT desktop or mobile. Connector reads human sessions and worktrees; continuation creates new owned work.",
        "照常使用 BAT desktop／mobile；Connector 唯讀人工 session 與 worktree，接續時建立新的自有工作。",
    ),
    (
        "Agent-owned resources",
        "Agent 自有資源",
        "The PM and engineering agents retain their own workspaces, fixed source revisions and original operation links.",
        "PM 與工程 Agent 保留各自工作區、固定來源版本及原操作關聯。",
    ),
]:
    parts.append("<div>" + bi(en, zh, "strong") + bi(d, dz, "p") + "</div>")
parts.append(
    '</div><div class="delivery-node">'
    + bi(
        "Fixed result → GitHub PR → merge / deploy → maintenance",
        "固定成果 → GitHub PR → merge／部署 → 維護",
        "strong",
    )
    + bi(
        "Cross-host code synchronization uses explicitly bound repositories and published commits.",
        "跨主機程式碼同步使用明確綁定的 repository 與已發布 commit。",
        "p",
    )
    + "</div><figcaption>"
    + bi(
        "Python is the sole business authority. Native code handles local facilities and transport. Project Hub is an interaction reference, with no importer or runtime dependency.",
        "Python 是唯一中央業務後端，原生程式負責本機能力與傳輸；Project Hub 提供互動參考，沒有 importer 或 runtime 依賴。",
    )
    + "</figcaption></figure>"
)
parts.append(
    '<div class="two-column"><div>'
    + bi("Keep the same central", "沿用同一個中央", "h3")
    + bi(
        "Desktop prepares one private local central. Explicitly join an existing central when needed; connection failure never replaces its database.",
        "桌面準備一套私人本機中央，需要時明確加入既有中央；連線失敗不會另造資料庫。",
        "p",
    )
    + "</div><div>"
    + bi("Know where PM runs", "知道 PM 在哪裡執行", "h3")
    + bi(
        "Bounded PM analysis runs on an eligible Linux central or BAT host. A Windows/Mac client can use that route; installing its desktop app does not add a native coding adapter.",
        "有界 PM 分析在合格 Linux 中央或 BAT 主機執行。Windows／Mac client 可使用該路徑，桌面安裝本身不會增加一般原生 coding adapter。",
        "p",
    )
    + "</div></div></section>"
)

parts.append(
    '<section class="section" id="windows">'
    + heading(
        "Windows Fleet",
        "Windows Fleet",
        "Choose what connects and what opens.",
        "選要連的主機，也選要開的視窗。",
        "Local connectivity and startup stay visible alongside project management.",
        "專案管理之外，本機連線與啟動控制仍有清楚入口。",
    )
    + '<div class="feature-list">'
)
for en, zh, d, dz, path in [
    (
        "Connections and readiness",
        "連線與就緒",
        "Maintain selected tunnels and configured route fallbacks. Check TLS, BAT identity and central access before work.",
        "維持選定 tunnels 與已配置備援路由，工作前核對 TLS、BAT 身分及中央存取。",
        "fleet-routes.md",
    ),
    (
        "Windows sign-in",
        "Windows 登入",
        "Choose background connections, BAT profile windows and Dashboard independently; retain the login picker or saved choices.",
        "背景連線、BAT profile 視窗、Dashboard 各自選擇，保留登入選擇器或已存選項。",
        "desktop-fleet-native.md",
    ),
    (
        "One connection owner",
        "明確的連線擁有者",
        "Use public Rust configuration or explicitly migrate an existing PowerShell setup after proving the old owner stopped.",
        "使用公開 Rust 設定，或證明原連線擁有者已停止執行後明確遷移既有 PowerShell 配置。",
        "fleet-migration.md",
    ),
    (
        "Native resources",
        "原生資源",
        "Credential Manager, files and tray controls remain available. Tailscale sign-in and fixed central bootstrap use configured local facilities.",
        "保留 Credential Manager、檔案及系統匣操作；Tailscale 登入與固定中央 bootstrap 使用已配置的本機能力。",
        "fleet-bootstrap.md",
    ),
]:
    parts.append(
        "<article>"
        + bi(en, zh, "h3")
        + bi(d, dz, "p")
        + doc("docs/design/" + path, "Read the guide →", "閱讀說明 →")
        + "</article>"
    )
parts.append(
    '</div><div class="actions">'
    + doc("docs/windows.md", "Windows guide and resources", "Windows 使用與資源", "button secondary lang-en")
    + doc(
        "docs/windows.zh-TW.md",
        "Windows guide and resources",
        "Windows 使用與資源",
        "button secondary lang-zh",
    )
    + doc("desktop/fleet.example.json", "Configuration example →", "設定範例 →")
    + "</div></section>"
)

parts.append(
    '<section class="installation-band" id="start"><div class="section">'
    + heading(
        "Start with a reviewed build",
        "從核對過的版本開始",
        "Install, connect, try a small project.",
        "安裝、連線，先試一個小專案。",
        "Desktop packages include Python and prepare the local central. Account trust and work destinations remain explicit choices.",
        "桌面包包含 Python 並準備本機中央；帳號信任與工作目的地仍由你明確選擇。",
    )
    + '<ol class="install-steps">'
)
for en, zh, d, dz in [
    (
        "Match the package",
        "核對套件",
        "Stable: Connector 0.2.12 / desktop 0.1.8, source 14c62830. Prerelease: Connector 0.2.13 / desktop 0.1.9, source 437ee6a7143a82e32eae1d6a008203b2df072aaf (437ee6a7). Choose the matching desktop asset below and check release-manifest.json and SHA256SUMS. Each release has a wheel, sdist, Linux deb, two macOS DMGs, Windows installer and those two records: eight assets.",
        "穩定版：Connector 0.2.12／desktop 0.1.8，來源 14c62830。預覽版（prerelease）：Connector 0.2.13／desktop 0.1.9，來源 437ee6a7143a82e32eae1d6a008203b2df072aaf（437ee6a7）。從下方選擇相符桌面套件，核對 release-manifest.json 與 SHA256SUMS。每版各有 wheel、sdist、Linux deb、兩個 macOS DMG、Windows 安裝器與這兩份紀錄，共八檔。",
    ),
    (
        "Connect your environment",
        "連接環境",
        "Get ready to work opens 1. Connect BAT. Paste BAT’s connection URL, check Host ID, then Verify and save host. After success, choose View sessions. Repository binding and verification commands stay collapsed until needed.",
        "準備開始工作會展開「1. 連線至 BAT」。貼上 BAT 連線網址、核對主機代號，再按「驗證並儲存主機」。成功後按「查看工作階段」；儲存庫與驗證命令需要時再展開。",
    ),
    (
        "Follow one result",
        "追完一份成果",
        "Start a small PM scope, inspect its original evidence and record what still needs a decision.",
        "從小範圍 PM 工作開始，檢視原始證據，記錄仍需決定的事項。",
    ),
]:
    parts.append("<li>" + bi(en, zh, "h3") + bi(d, dz, "p") + "</li>")
parts.append(
    '</ol><div class="install-current">'
    + bi("Validate the version you use", "驗證你實際使用的版本", "h3")
    + bi(
        "Advanced users can explicitly join an existing central. The published release includes fixed source and checksum records. These are unsigned or ad-hoc-signed development builds, not a stable signed update channel. Closing the UI leaves central work running.",
        "進階使用者可明確加入既有中央。已發布版本附有固定來源與校驗紀錄。這些是未簽章或 ad-hoc 簽章的開發版本，尚非穩定的簽章更新通道；關閉 UI 仍保留中央工作。",
        "p",
    )
    + bi(
        "For Connector, use the verified installer from the same tag with these arguments. Scroll the command blocks to read or copy the full arguments on a narrow screen.",
        "Connector 使用同一 tag 的驗證安裝器，參數如下。窄螢幕可橫向捲動命令區塊，閱讀或複製完整參數。",
        "p",
    )
    + bi("Stable", "穩定版", "p")
    + '<pre class="install-command" tabindex="0" aria-label="Stable installer arguments / 穩定版安裝器參數"><code>--tag v0.2.12 --channel-check stable</code></pre>'
    + bi("Prerelease", "預覽版", "p")
    + '<pre class="install-command" tabindex="0" aria-label="Preview installer arguments / 預覽版安裝器參數"><code>--tag v0.2.13-preview.20261022 --channel-check preview</code></pre>'
    + bi(
        "A tag is required; the default preview check accepts stable releases and prereleases, while stable refuses previews. On Linux, <code>--enable-updates</code> enrolls and schedules that channel. The updater offers eligible newer stable releases on stable, and eligible newer stable releases or prereleases on preview. Updates affect future launches; existing processes keep running.",
        "必須指定 tag；預設 preview 可接受穩定版與預覽版，stable 拒絕預覽版。Linux 加上 <code>--enable-updates</code> 才會登記該通道並啟用排程。更新器在 stable 提供符合條件的較新穩定版，在 preview 提供符合條件的較新穩定版或預覽版。更新供下次啟動使用，既有程序繼續執行。",
        "p",
    )
    + '<div class="actions">'
    + doc("docs/getting-started.md", "Installation and first use", "安裝與首次使用", "button light lang-en")
    + doc(
        "docs/getting-started.zh-TW.md",
        "Installation and first use",
        "安裝與首次使用",
        "button light lang-zh",
    )
    + link(
        repo + "/releases/tag/v0.2.12",
        "Download stable 0.2.12 →",
        "下載穩定版 0.2.12 →",
        "text-link",
    )
    + link(
        repo + "/releases/tag/v0.2.13-preview.20261022",
        "Download prerelease 0.2.13 →",
        "下載預覽版 0.2.13 →",
        "text-link",
    )
    + doc("scripts/install_connector.py", "Installer source", "安裝器原始碼", "text-link")
    + doc("docs/connector-updates.md", "Connector automatic updates", "Connector 自動更新", "text-link")
    + "</div></div></div></section>"
)
parts.append(
    '<section class="section" id="platforms">'
    + heading(
        "Entry points", "使用入口", "One Dashboard across your screens.", "不同螢幕，同一份 Dashboard。"
    )
    + '<div class="table-wrap" role="region" aria-label="Platform support / 平台支援"><table role="table"><thead role="rowgroup"><tr role="row">'
    + bi("Entry", "入口", "th")
    + bi("Local facilities", "本機能力", "th")
    + bi("Current boundary", "目前界線", "th")
    + '</tr></thead><tbody role="rowgroup">'
)
for en, zh, d, dz, bound, bz in [
    (
        "Browser",
        "瀏覽器",
        "Shared UI and authenticated central access",
        "共用 UI 與中央認證存取",
        "No native Fleet or credential vault.",
        "沒有原生 Fleet 或 credential vault。",
    ),
    (
        "Windows x64",
        "Windows x64",
        "NSIS, bundled central, tray, Fleet and Credential Manager",
        "NSIS、打包中央、系統匣、Fleet 與 Credential Manager",
        "Unsigned validation package.",
        "未正式簽署的驗證包。",
    ),
    (
        "Mac Apple Silicon / Intel",
        "Mac Apple Silicon／Intel",
        "DMG, bundled central, menu bar and Keychain",
        "DMG、打包中央、選單列與 Keychain",
        "Ad-hoc signing; Windows Fleet is not ported.",
        "ad-hoc 簽署；Windows Fleet 未移植。",
    ),
    (
        "Linux",
        "Linux",
        "Debian package and bundled central",
        "Debian 套件及打包中央",
        "External-central native credentials are memory-only.",
        "外部中央的原生憑證只留記憶體。",
    ),
]:
    parts.append(
        '<tr role="row"><th scope="row" role="rowheader">' + bi(en, zh) + '</th><td role="cell">'
        + '<span class="table-label" aria-hidden="true">' + bi("Local facilities", "本機能力") + "</span>"
        + bi(d, dz) + '</td><td role="cell">'
        + '<span class="table-label" aria-hidden="true">' + bi("Current boundary", "目前界線") + "</span>"
        + bi(bound, bz) + "</td></tr>"
    )
parts.append(
    "</tbody></table></div>"
    + bi(
        "Connector automatic updates are in scope for explicitly enrolled per-user Linux installations; eligible published releases apply to future launches. Formal signing, Mac notarization, signed desktop updates and Linux persistent native credential storage remain outside the agreed delivery.",
        "Connector 自動更新已納入範圍，適用於明確登記的 Linux 使用者安裝；符合條件的已發布版本供下次啟動使用。正式簽章、Mac 公證、桌面版簽章更新與 Linux 原生憑證持久儲存仍排除於交付範圍。",
        "p",
        "section-note",
    )
    + doc("docs/design/connector-updates.md", "Connector update contract →", "Connector 更新契約 →")
    + "</section>"
)

parts.append(
    '<section class="section" id="decisions">'
    + heading(
        "Ownership and recovery",
        "歸屬與恢復",
        "Keep control and history intact.",
        "保留控制邊界，也保留歷史。",
    )
    + '<div class="two-column">'
)
for en, zh, d, dz in [
    (
        "Your workspace stays yours",
        "人的工作區保持原樣",
        "Human sessions and worktrees remain read-only through Connector. Reading a source does not grant control of it.",
        "Connector 對人工 session 與 worktree 保持唯讀；能讀來源，不代表能控制來源。",
    ),
    (
        "Each action has authority",
        "每個動作都有權限依據",
        "The current principal, host policy, source access and original resource identity are checked before effects.",
        "效果執行前核對目前身分、主機政策、來源存取及原資源身分。",
    ),
    (
        "A lost reply is still traceable",
        "遺失回覆仍可追查",
        "Keep the original operation and key while a result is uncertain. Query its evidence instead of dispatching another copy.",
        "結果未知時保留原 operation 與 key；查證據，避免再派一份工作。",
    ),
    (
        "Host isolation still matters",
        "主機隔離仍有自己的責任",
        "Connector policy works with host accounts and confinement. It is not an operating-system sandbox.",
        "Connector 政策與主機帳號、隔離機制一起運作，本身不是作業系統 sandbox。",
    ),
]:
    parts.append("<article>" + bi(en, zh, "h3") + bi(d, dz, "p") + "</article>")
parts.append("</div>" + doc("SECURITY.md", "Security model →", "安全模型 →") + "</section>")
parts.append(
    '<section class="section" id="status">'
    + heading(
        "Current scope",
        "目前範圍",
        "A working foundation. A lifecycle still in integration.",
        "底座已能使用，完整流程仍在整合。",
        "Read merged code, candidate checks and real-environment acceptance as separate records.",
        "分開看已合併程式、候選驗證與實際環境驗收。",
    )
    + '<dl class="readiness">'
)
for label, lz, en, zh in [
    (
        "Merged main",
        "已合併主線",
        "Desktop and central foundation; PM plans, quality/dependency evidence, bounded dispatch, policy-bounded repairs, BAT proposals/Follow-up and AHP read-only observation. #132 adds central Overview, discussion context and renames, register, Schedule, Dependencies and daily resource buckets; #134 adds adopted goals, recent decisions and the guided close of a resolved issue.",
        "桌面與中央底座、PM 計畫、品質／依賴證據、有界派工、政策內修復、BAT 提案／Follow-up 及 AHP 唯讀觀察。#132 補上中央總覽、討論來源與改名、登記表、時程、技術相依及每日資源桶；#134 補上採用後目標、近期決策及問題解決回報引導。",
    ),
    (
        "Merged workspace",
        "已合併工作台",
        "Published preview source 437ee6a7 (through #395, October 22, 2026) connects reviewed plans to Work, queued follow-ups, fixed Results, verification and PR delivery. Workbench provides mobile navigation and foreground recovery; profiles show route limits before start. PM can discuss two fixed assessments, save continuation briefs and explicitly assess the Overview. Resources reads separately authorized local Codex/Claude histories. Lists load summaries first; unsent instructions can return to Idea. Core UI actions share HTTP/MCP contracts. Classic remains the default. Integration fixtures use real Connector and disk Git with mock BAT/providers; live BAT/model and physical-device acceptance remain separate.",
        "已發布預覽版來源 437ee6a7（截至 #395，2026 年 10 月 22 日）接通計畫採用、建立工作、排隊中的追加指令、固定成果、驗證與 PR 交付。Workbench 提供手機導航與前景恢復，profile 在開始前顯示路線限制。PM 可討論兩份固定評估、保存接續摘要，並依明確要求判讀總覽；資源頁可讀取另行授權的本機 Codex／Claude 對話。清單先載入摘要，未送指令可留成 Idea；核心 UI 操作共用 HTTP／MCP 契約。Classic 仍為預設。整合測試使用真正 Connector、磁碟 Git 與 mock BAT／providers；真 BAT／模型及實體裝置仍需另行驗收。",
    ),
    (
        "Still ahead",
        "仍待完成",
        "General native coding, AHP create-own/write, remaining document views/slides, broader CLI support, remote usage collection and delivery-efficiency attribution. The full adopted PM v1.2 plan remains the scope.",
        "一般原生 coding、AHP 自建／寫入、其餘文件視圖／簡報、更多 CLI、遠端用量收集及交付效率歸因；範圍仍依完整 PM v1.2 計畫。",
    ),
    (
        "Evidence limits",
        "驗證界線",
        "Package checks validate their exact source and fixtures. Screenshots use demonstration data. Published packages and installation readback do not establish owner live acceptance.",
        "套件檢查驗證精確來源與 fixture；截圖使用展示資料。已發布套件與安裝讀回不代表使用者已通過實際環境驗收。",
    ),
]:
    parts.append("<div>" + bi(label, lz, "dt") + bi(en, zh, "dd") + "</div>")
parts.append(
    '</dl><div class="evidence-links">'
    + doc("docs/product/implementation-status.md", "Progress and evidence ↗", "進度與證據 ↗", "")
    + doc("docs/product/pm-v1.2-plan.md", "Adopted plan ↗", "已採用計畫 ↗", "")
    + link(repo + "/actions?query=branch%3Amain", "Current main checks ↗", "目前 main 檢查 ↗")
    + "</div></section>"
)

parts.append(
    '<section class="section" id="docs">'
    + heading("Documentation", "文件", "Find the next useful page.", "找到下一份用得上的說明。")
    + '<div class="doc-list">'
)
for i, (en, zh, d, dz, path, zpath) in enumerate(
    [
        (
            "Installation and PM first use",
            "安裝與 PM 首次使用",
            "Packages, runtime prerequisites and one small project.",
            "套件、runtime 前提及一個小專案。",
            "docs/getting-started.md",
            "docs/getting-started.zh-TW.md",
        ),
        (
            "Windows Fleet and resources",
            "Windows Fleet 與資源",
            "Connections, profiles, startup and native facilities.",
            "連線、profiles、啟動及原生能力。",
            "docs/windows.md",
            "docs/windows.zh-TW.md",
        ),
        (
            "Progress report",
            "進度報告",
            "Merged, candidate, tested and remaining scope.",
            "已合併、候選、已驗證及剩餘範圍。",
            "docs/product/implementation-status.md",
            None,
        ),
        (
            "Development handoff",
            "開發交接",
            "Fixed versions, evidence, blockers and the next steps.",
            "固定版本、證據、阻擋與接續步驟。",
            "docs/handoff/2026-10-14-docs-ux-v2.md",
            None,
        ),
        (
            "Agent integration",
            "Agent 整合",
            "Canonical skills, MCP/CLI and central operations.",
            "共用 skills、MCP／CLI 及中央操作。",
            "docs/agent-skills.md",
            None,
        ),
        (
            "Source and contribution",
            "原始碼與參與開發",
            "Build the shared frontend and run the relevant checks.",
            "建置共用前端並執行相應檢查。",
            "README.md",
            "README.zh-TW.md",
        ),
    ],
    1,
):
    parts.append(
        f'<div><span class="index">0{i}</span><div>'
        + (
            doc(path, en + " ↗", zh + " ↗", "lang-en") + doc(zpath, en + " ↗", zh + " ↗", "lang-zh")
            if zpath
            else doc(path, en + " ↗", zh + " ↗", "")
        )
        + bi(d, dz, "p")
        + "</div></div>"
    )
parts.append(
    '</div></section><section class="section faq" id="faq">'
    + heading("Practical questions", "使用前的問題", "Know what to expect.", "先了解可以期待什麼。")
)
for q, qz, a, az in [
    (
        "Does BAT have to run on this computer?",
        "BAT 一定要在這台電腦執行嗎？",
        "No. The Dashboard can connect to a configured central and BAT host. Coding work stays on the selected host; the browser's operating system does not determine runtime support.",
        "不必。Dashboard 可連已配置的中央與 BAT 主機；開發工作留在選定主機，瀏覽器的作業系統不決定 runtime 支援。",
    ),
    (
        "Will PM act on everything I say?",
        "跟 PM 說的每句話都會直接執行嗎？",
        "Discussion creates records and proposals. Adoption, dispatch and maintenance policy are explicit product actions with their own authority and fixed scope.",
        "討論形成紀錄與提案；採用、派工及維護政策是明確的產品操作，各有權限與固定範圍。",
    ),
    (
        "Does it scan every CLI's tokens?",
        "已經會掃每個 CLI 的 token 嗎？",
        "Local Codex and Claude collectors are implemented. Agents shows sourced observations, daily buckets where available and reported quota windows. Remote collection and delivery-efficiency attribution remain planned; token traffic is not a subscription quota or bill.",
        "已有 Codex 與 Claude 本機 collector。Agent 資源顯示有來源的觀測、可用的每日桶及回報配額窗；遠端收集與交付效率歸因仍待做，token 流量也不能直接當成訂閱配額或帳單。",
    ),
    (
        "Will optimization change my model automatically?",
        "優化會自動換掉我的模型嗎？",
        "No usage-driven model policy is active. Future recommendations need measured delivery quality; adopted policy should apply to new owned work while preserving existing and human sessions.",
        "尚未啟用依用量自動改模型的政策。未來建議需要交付品質的實測，採用後應作用於新的自有工作，保留既有及人工 session。",
    ),
    (
        "Can I leave it unattended all day?",
        "可以整天放著無人值守嗎？",
        "Only within verified capabilities and adopted bounds. Controlled fixtures do not establish readiness of your accounts, hosts or deployment targets; real-environment acceptance remains with the owner.",
        "須在已驗證能力及採用界線內使用；受控 fixture 不代表你的帳號、主機或部署目標已就緒，實際環境驗收仍由使用者處理。",
    ),
    (
        "Is this an official BAT product?",
        "這是 BAT 官方產品嗎？",
        "It is an unofficial MIT-licensed companion. BAT is by TonyQ and contributors. Project Hub informs selected interaction patterns without a runtime or importer dependency.",
        "這是 MIT 授權的非官方輔助工具。BAT 由 TonyQ 與貢獻者開發；部分互動參考 Project Hub，沒有 runtime 或 importer 依賴。",
    ),
]:
    parts.append("<details><summary>" + bi(q, qz) + "</summary>" + bi(a, az, "p") + "</details>")
parts.append(
    '</section></main><footer class="site-footer"><div class="section footer-inner"><div><a class="brand" href="#top"><img src="logo.svg" width="32" height="32" alt=""><span>Better Agent Dashboard</span></a>'
    + bi(
        "Project management on bat-agent-connector. An unofficial BAT companion.",
        "以 bat-agent-connector 為中央的專案管理工具，BAT 的非官方輔助工具。",
        "p",
    )
    + '</div><div class="footer-links">'
    + link(repo, "Source", "原始碼")
    + link("https://github.com/tony1223/better-agent-terminal", "BAT by TonyQ", "BAT／TonyQ")
    + doc("LICENSE", "MIT license", "MIT 授權", "")
    + doc("THIRD_PARTY_NOTICES.md", "Credits", "第三方聲明", "")
    + "</div></div></footer>"
)

title = "Better Agent Dashboard · Project state, PM discussions and decisions"
zt = "Better Agent Dashboard · 專案狀況、PM 討論與決策"
desc = "Understand project state, find a problem, discuss it with the PM and adopt a reviewed update in one shared Web and Tauri workspace. Python Connector remains the authority."
zd = "在同一個 Web／Tauri 專案脈絡裡看懂狀況、找到問題、與 PM 討論並採用審閱後的更新；Python Connector 保持唯一中央權威。"
head = f'''<!doctype html>
<!-- Generated by scripts/build_product_site.py. Edit the source; see site/README.md. -->
<html lang="en" data-lang="en" data-title-en="{title}" data-title-zh="{zt}" data-desc-en="{desc}" data-desc-zh="{zd}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="theme-color" content="#eff2f6">
<title>{title}</title><meta name="description" content="{desc}">
<link rel="canonical" href="https://ted-h-com.github.io/bat-agent-connector/">
<link rel="alternate" hreflang="en" href="https://ted-h-com.github.io/bat-agent-connector/">
<link rel="alternate" hreflang="zh-Hant" href="https://ted-h-com.github.io/bat-agent-connector/?lang=zh-TW">
<link rel="alternate" hreflang="x-default" href="https://ted-h-com.github.io/bat-agent-connector/">
<meta property="og:type" content="website"><meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}"><meta property="og:url" content="https://ted-h-com.github.io/bat-agent-connector/">
<meta property="og:image" content="https://ted-h-com.github.io/bat-agent-connector/images/overview-en.png">
<meta property="og:image:alt" content="Actual shared Dashboard with synthetic demonstration data">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="styles.css">
<script src="lang.js"></script><script src="app.js" defer></script>
</head><body>
'''
output = head + "\n".join(parts) + "\n</body>\n</html>\n"
parser = argparse.ArgumentParser(description="Build the bilingual static product website")
parser.add_argument("--check", action="store_true", help="refuse generated index drift")
args = parser.parse_args()
if args.check:
    if (root / "index.html").read_text() != output:
        raise SystemExit("site/index.html differs: run python3 scripts/build_product_site.py")
    print("Product site generated content is current.")
else:
    (root / "index.html").write_text(output)
