"""Generate the bilingual public introduction. Product data stays on the central."""

import argparse
from html import escape
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "site"
repo = "https://github.com/teddashh/bat-agent-connector"


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
    bi("Keep the plan.<br>Follow the result.", "記得原計畫，<br>追到真成果。", "h1"),
    bi(
        "Discuss the goal, review the plan and connect agent work to delivery evidence. A shared Web and desktop workspace keeps decisions, progress and maintenance in view.",
        "討論目標、審閱計畫，把 Agent 工作接回交付證據。在共用的 Web 與桌面工作空間，掌握決策、進度與後續維護。",
        "p",
        "hero-description",
    ),
    '<div class="actions">'
    + link("#product", "Explore the workspace", "看看工作空間", "button primary")
    + link("#start", "Install and start", "安裝與開始使用", "text-link")
    + "</div>",
    '</div></div></section>',
]
release_note = (
    '<div class="release-note">'
    + bi("Development preview", "開發預覽", "strong")
    + bi(
        "PM v1.2 is in integration. Check the current scope and validated packages before installing.",
        "PM v1.2 仍在整合。安裝前請查看目前範圍與已通過驗證的套件。",
    )
    + link("#status", "Current scope", "目前範圍")
    + "</div>"
)
parts.append('<section class="section product-tour" id="product" aria-label="Product tour / 介面導覽">')

shots = [
    (
        "workspace",
        "Project workspace",
        "專案工作空間",
        "Select work to read its conversation, reply and inspect linked results.",
        "選取工作，一起閱讀對話、回覆並查看關聯成果。",
    ),
    (
        "pm-planning",
        "PM planning",
        "PM 計畫",
        "Inspect a fixed plan draft and its sources before adoption.",
        "先檢視固定計畫草稿與來源，再決定是否採用。",
    ),
    (
        "pm-kanban",
        "PM Kanban",
        "PM 看板",
        "Scan work by stage; open a card for execution and evidence.",
        "按階段掃視工作，需要時再展開執行與證據。",
    ),
    (
        "dispatch",
        "Project dispatch",
        "專案派工",
        "Choose the destination and fixed published revision for the agent's own work.",
        "選擇目的地與固定已發布版本，開始 Agent 自有工作。",
    ),
]
parts.append(
    '<div class="tour-controls" role="group" aria-label="Screenshot view / 介面展示">'
    + bi("View:", "檢視：", "span", "tour-label")
)
for key, en, zh, _, _ in shots:
    parts.append(
        f'<button type="button" data-view="{key}" aria-pressed="{str(key == "workspace").lower()}" aria-controls="view-{key}">'
        + bi(en, zh)
        + "</button>"
    )
parts.append("</div>")
for key, _, _, en, zh in shots:
    parts.append(f'<figure class="product-shot" id="view-{key}">')
    for lang, alt in [("en", en), ("zh", zh)]:
        parts.append(
            f'<a class="lang-{lang} image-link" href="images/{key}-{lang}.png" aria-label="{escape(alt)}"><img src="images/{key}-{lang}.png" width="1440" height="960" loading="lazy" alt="{escape(alt)}"></a>'
        )
    parts.append(
        "<figcaption>"
        + bi(en, zh)
        + bi(
            "Actual UI · demonstration data · open image for full size",
            "實際介面 · 展示資料 · 點圖看原尺寸",
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
        "A workflow you can review at every step.",
        "每一步，都知道該看什麼。",
        "Try login and password reset as a first scope. Each decision and result retains its original project context.",
        "以登入與忘記密碼作為第一段範圍，讓每次決策與成果都保留原專案脈絡。",
    )
    + '<ol class="workflow">'
)
for i, (en, zh, d, dz) in enumerate(
    [
        (
            "Discuss the idea",
            "討論構想",
            "Save the goal, constraints, sources and open questions in an Idea thread.",
            "在 Idea 討論保存目標、限制、來源與未決問題。",
        ),
        (
            "Adopt a plan",
            "採用計畫",
            "Prepare the PM's own worktree. Review a proposal and explicitly adopt its fixed requirements.",
            "準備 PM 自有 worktree，審閱提案後明確採用固定需求。",
        ),
        (
            "Dispatch bounded work",
            "派出有界工作",
            "Select engineering packages and destinations. Agents work in their own managed resources.",
            "選工程包與目的地，讓 Agent 在自己的 managed 資源工作。",
        ),
        (
            "Evaluate the result",
            "評估成果",
            "Use original verification evidence and a responsible quality review against acceptance criteria and DoD.",
            "使用原驗證證據，由責任審閱者對照接受條件與 DoD 評估品質。",
        ),
        (
            "Confirm delivery",
            "確認交付",
            "Review the integration and PR. Keep merge, deployment and release-baseline evidence distinct.",
            "審閱整合及 PR，分開記錄 merge、部署與發布基準證據。",
        ),
        (
            "Maintain and review",
            "維護與回顧",
            "Adopt repair bounds and a schedule. Review findings and results, then bring the next decision into Idea.",
            "採用修復界線與排程，檢視發現及結果，再將下一個決策帶回 Idea。",
        ),
    ],
    1,
):
    parts.append(
        f'<li><span class="step-number">0{i}</span><div>' + bi(en, zh, "h3") + bi(d, dz, "p") + "</div></li>"
    )
parts.append(
    "</ol>"
    + bi(
        "Passing tests does not automatically accept a requirement. A plan does not authorize execution, and a finding does not authorize repair. M4 is validating the joined lifecycle; follow the progress report for its current limits.",
        "測試通過不會自動接受需求，計畫不直接授權執行，finding 也不直接授權修復。M4 正在驗證整條生命週期，現況與限制請看進度報告。",
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
    (
        "Plans and sources",
        "計畫與來源",
        "Versioned documents distinguish a suggestion, an adopted scope and later changes.",
        "版本化文件分清建議、已採用範圍與後續變更。",
        "pm-plans.md",
    ),
    (
        "Roadmap, Sprint and Kanban",
        "Roadmap、Sprint 與 Kanban",
        "Organize goals, commitments and work. Selected assessments preserve the result they evaluated.",
        "整理目標、承諾與工作；選定的評估保留它當時審閱的成果。",
        "pm-sprints.md",
    ),
    (
        "Follow-up and decisions",
        "Follow-up 與決策",
        "Sourced analysis prepares candidate changes and focused questions for review.",
        "有來源的分析整理候選變更與關鍵問題，供你審閱。",
        "pm-follow-up.md",
    ),
    (
        "Evidence and delivery",
        "證據與交付",
        "Connect fixed commits, original checks, quality reviews, PRs and deployment receipts.",
        "串起固定 commit、原始 checks、品質審閱、PR 與部署回執。",
        "delivery.md",
    ),
    (
        "Maintenance Review",
        "維護 Review",
        "Adopted policies bound observations and repairs; reports retain findings and original results.",
        "已採用政策限制觀察與修復，報告保留發現與原始結果。",
        "pm-maintenance-review.md",
    ),
    (
        "Project Brief exports",
        "Project Brief 輸出",
        "Read or export one fixed Brief as Markdown, HTML or SVG. Further document views and slides remain planned.",
        "讀取或輸出同一份固定 Brief 的 Markdown、HTML 或 SVG；其他文件視圖與簡報仍待完成。",
        "pm-brief-exports.md",
    ),
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
    + bi("Selected BAT host and workspace", "選定的 BAT 主機與 workspace", "h3", "bat-node")
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
        "明確的連線 owner",
        "Use public Rust configuration or explicitly migrate an existing PowerShell setup after proving the old owner stopped.",
        "使用公開 Rust 設定，或證明舊 owner 已停止後明確遷移既有 PowerShell 配置。",
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
        "Choose a successful main platform build and match the artifact to its source commit and validation records.",
        "選成功的 main 平台建置，核對 artifact 的來源 commit 與驗證紀錄。",
    ),
    (
        "Connect your environment",
        "連接環境",
        "Complete BAT trust, repository binding and verification setup. Advanced users can join an existing central.",
        "完成 BAT 信任、repository 綁定與驗證設定；進階使用者可加入既有中央。",
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
        "GitHub Actions artifacts can expire and may require sign-in. These packages are development validation builds, not a stable signed update channel. Closing the UI leaves central work running.",
        "GitHub Actions artifacts 會到期，也可能要求登入。這些是開發驗證包，尚非穩定的簽章更新通道；關閉 UI 仍保留中央工作。",
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
        repo + "/actions/workflows/desktop.yml?query=branch%3Amain",
        "Find matching packages →",
        "查找相符套件 →",
        "text-link",
    )
    + "</div></div></div></section>"
)
parts.append(
    '<section class="section" id="platforms">'
    + heading(
        "Entry points", "使用入口", "One Dashboard across your screens.", "不同螢幕，同一份 Dashboard。"
    )
    + '<div class="table-wrap" role="region" aria-label="Platform support / 平台支援" tabindex="0"><table><thead><tr>'
    + bi("Entry", "入口", "th")
    + bi("Local facilities", "本機能力", "th")
    + bi("Current boundary", "目前界線", "th")
    + "</tr></thead><tbody>"
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
    parts.append("<tr>" + bi(en, zh, "th") + bi(d, dz, "td") + bi(bound, bz, "td") + "</tr>")
parts.append(
    "</tbody></table></div>"
    + bi(
        "Formal signing, Mac notarization, production updates and Linux persistent native credential storage are outside the agreed delivery.",
        "正式簽章、Mac 公證、正式更新通道與 Linux 原生憑證持久儲存，已排除於本次交付。",
        "p",
        "section-note",
    )
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
        "Desktop and central foundation; PM plans, quality/dependency evidence, bounded dispatch, policy-bounded repairs, BAT proposals/Follow-up and AHP read-only observation.",
        "桌面與中央底座、PM 計畫、品質／依賴證據、有界派工、政策內修復、BAT 提案／Follow-up 及 AHP 唯讀觀察。",
    ),
    (
        "Candidate work",
        "候選工作",
        "M4 joins the lifecycle and extends runtime completion, Roadmap quality and Brief deviations. M5 adds workspace analysis choice and serial manual-activity observation. Their current checks belong in the dated progress report.",
        "M4 串接生命週期，補原執行完成、Roadmap 品質與 Brief 偏差；M5 補 workspace 分析選擇及人工循序活動觀察。最新檢查記錄於附日期的進度報告。",
    ),
    (
        "Still ahead",
        "仍待完成",
        "General native coding, AHP create-own/write, remaining document views/slides, broader CLI support and usage/efficiency reporting. The full adopted PM v1.2 plan remains the scope.",
        "一般原生 coding、AHP 自建／寫入、其餘文件視圖／簡報、更多 CLI 及用量／效率報告；範圍仍依完整 PM v1.2 計畫。",
    ),
    (
        "Evidence limits",
        "驗證界線",
        "Package checks validate their exact source and fixtures. Screenshots use demonstration data. Owner installation and live acceptance are not claimed as passed.",
        "套件檢查驗證精確來源與 fixture；截圖使用展示資料。未宣稱使用者已安裝或已通過實際環境驗收。",
    ),
]:
    parts.append("<div>" + bi(label, lz, "dt") + bi(en, zh, "dd") + "</div>")
parts.append(
    '</dl><div class="evidence-links">'
    + doc("docs/product/progress-2026-10-12.md", "Progress and evidence ↗", "進度與證據 ↗", "")
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
            "docs/product/progress-2026-10-12.md",
            None,
        ),
        (
            "Development handoff",
            "開發交接",
            "Fixed versions, evidence, blockers and the next steps.",
            "固定版本、證據、阻擋與接續步驟。",
            "docs/handoff/2026-10-12-pm-v1.2.md",
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
        "No collector is implemented yet. The planned approach uses per-CLI local aggregation and coverage reporting, then relates usage to accepted work. Token traffic cannot be treated as subscription quota or a bill.",
        "目前尚未實作 collector。規劃採各 CLI 的本機彙總與資料涵蓋說明，再把用量接回合格工作；token 流量不能直接當成訂閱配額或帳單。",
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
        "這是 MIT 授權的非官方 companion。BAT 由 TonyQ 與貢獻者開發；部分互動參考 Project Hub，沒有 runtime 或 importer 依賴。",
    ),
]:
    parts.append("<details><summary>" + bi(q, qz) + "</summary>" + bi(a, az, "p") + "</details>")
parts.append(
    '</section></main><footer class="site-footer"><div class="section footer-inner"><div><a class="brand" href="#top"><img src="logo.svg" width="32" height="32" alt=""><span>Better Agent Dashboard</span></a>'
    + bi(
        "Project management on bat-agent-connector. An unofficial BAT companion.",
        "以 bat-agent-connector 為中央的專案管理工具，BAT 的非官方 companion。",
        "p",
    )
    + '</div><div class="footer-links">'
    + link(repo, "Source", "原始碼")
    + link("https://github.com/tony1223/better-agent-terminal", "BAT by TonyQ", "BAT／TonyQ")
    + doc("LICENSE", "MIT license", "MIT 授權", "")
    + doc("THIRD_PARTY_NOTICES.md", "Credits", "第三方聲明", "")
    + "</div></div></footer>"
)

title = "Better Agent Dashboard · Plans, agent work and delivery evidence"
zt = "Better Agent Dashboard · 計畫、Agent 工作與交付證據"
desc = "Discuss and adopt plans, follow agent work, evaluate results and review maintenance in a shared Web and Tauri workspace. Powered by Python Connector, BAT and Windows Fleet."
zd = "在共用 Web 與 Tauri 工作空間討論及採用計畫、跟進 Agent 工作、評估成果與回顧維護；底層由 Python Connector、BAT 與 Windows Fleet 分工。"
head = f'''<!doctype html>
<!-- Generated by scripts/build_product_site.py. Edit the source; see site/README.md. -->
<html lang="en" data-lang="en" data-title-en="{title}" data-title-zh="{zt}" data-desc-en="{desc}" data-desc-zh="{zd}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="theme-color" content="#eff2f6">
<title>{title}</title><meta name="description" content="{desc}">
<link rel="canonical" href="https://teddashh.github.io/bat-agent-connector/">
<link rel="alternate" hreflang="en" href="https://teddashh.github.io/bat-agent-connector/">
<link rel="alternate" hreflang="zh-Hant" href="https://teddashh.github.io/bat-agent-connector/?lang=zh-TW">
<link rel="alternate" hreflang="x-default" href="https://teddashh.github.io/bat-agent-connector/">
<meta property="og:type" content="website"><meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}"><meta property="og:url" content="https://teddashh.github.io/bat-agent-connector/">
<meta property="og:image" content="https://teddashh.github.io/bat-agent-connector/images/workspace-en.png">
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
