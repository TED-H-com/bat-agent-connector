# Product screenshots

These sixteen unedited PNGs show the real shared browser frontend from merged main
`437ee6a7143a82e32eae1d6a008203b2df072aaf`, using the single membership demo project
in [`desktop/tests/demo/index.ts`](../../desktop/tests/demo/index.ts).
This is the published Connector 0.2.13 / desktop 0.1.9 prerelease source through #395 (2026-10-22). The compact gallery shows existing PM views and a read-only manual session with synthetic data. It does not open Start work or the Work Result inspector. It does not show later changes #399–#403. Native coding/AHP routes, full PM lifecycle work and real BAT/model/device acceptance remain separate open work.

這十六張圖固定於已發布的 Connector 0.2.13／desktop 0.1.9 預覽版來源 `437ee6a7`（2026-10-22，#395），使用合成資料展示既有 PM 檢視與唯讀人工工作階段。圖庫沒有開啟「開始工作」或 Work 成果檢視器，也不含後續 #399–#403。原生 coding／AHP 路線、完整 PM 生命週期及真 BAT／模型／裝置驗收仍分別待完成。
The development preview is shared-browser evidence; this refresh provides no installation or live acceptance evidence.
本次預覽只提供共用瀏覽器畫面證據，不提供安裝或實機驗收證據。
The [current implementation status](../../docs/product/implementation-status.md) separates published source, historical installation receipts and pending acceptance.

| Images (English / zh-TW) | Scene and state |
| --- | --- |
| `overview-en.png`, `overview-zh.png` | Live Overview in Development: eight adopted requirements, three accepted, one blocker, two pending decisions, and Recovery review forecast three days late. Needs attention lists six rows; each shows its kind, context and age, with Discuss as the one action. Recently decided shows two synthetic plan-adoption receipts, actor `mei.lin`, separate from the pending decisions. The maintenance alert names lodash. The live charter shows four sections: purpose, scope, resources and risks. Milestones and recent deliveries stay in their own cards. Saved reports are separate. |
| `discussions-en.png`, `discussions-zh.png` | Discussion opened from the Overview blocker through its Discuss link. Automatic title, original context chip, and a reviewable proposal in the conversation. The proposal is Proposed and Unresolved (建議、未決); it is not adopted and does not authorize work. Discussion info is not open over it. The composer offers Save note only and Make a plan. This synthetic session says PM availability is unknown, and its status line says PM finished. |
| `requirements-en.png`, `requirements-zh.png` | RTM with email sign-in expanded and its bound work inspector open: eight requirements, three accepted, one recorded blocker. The row says Acceptance Accepted and Deploy Partial. The inspector keeps PR #87 and #91 Merged, with one deployed receipt and one unknown deploy. The latest result says the definition of done is not assessed. Owner is Not recorded. There is no Start work panel. |
| `issues-en.png`, `issues-zh.png` | Both register pages loaded: twelve records (one risk, six issues, one action, three changes, one adopted decision), including named maintenance findings. The header counts match those rows. One open record has no known age. Open records remain open. The adopted decision is Member sign-in plan, separate from the open changes. |
| `schedule-en.png`, `schedule-zh.png` | Recorded baseline, forecast and actual dates on a labelled axis. Design review stays collapsed. Unscheduled (6) stays collapsed. Recovery review is forecast three days late, and the header says 27 items. Waiting and Open stay distinct from Accepted. The phone width uses one fitted range, Sep 25 – Nov 7. |
| `dependencies-en.png`, `dependencies-zh.png` | Pinned inventory graph labelled Partial, with the evidence legend (solid verified, outline declared, dashed inferred) and the note that capture needs Manage access. The graph is bounded; missing versions, ownership and freshness remain unknown. |
| `board-en.png`, `board-zh.png` | Twelve work items and one recorded blocker. Lanes: Not started 2, In progress 3, Waiting 1, In review 2, Content approved 4. Waiting stays distinct from Content approved. The inspector is closed, so Start work and Result are not in this image. |
| `session-en.png`, `session-zh.png` | Manual BAT session on `demo-host`, with its read-only transcript. View only says to reply in BAT, continue from a checkpoint in a new managed session, or dispatch from an agent (MCP). That panel and the checkpoints block say this host is not set up for managed work. There are no send, answer or interrupt controls. |

All product data is synthetic, including acceptance and delivery receipts.
The capture script projects Recently decided from the demo's existing plan adoption
receipts using the central read shape; it invents no decisions or counts.
The eight-scene public tour stays compact. Agents and Overview's Unknown versus
None recorded states have additional bilingual review captures, outside the public gallery.
The explicit Resolve step is covered by the separate
[real-central walkthrough](../../docs/design/pm-decision-walkthrough.md), with mock external transports.
The discussion scene performs only three mock operations: create a bound thread,
save a note and prepare a proposal. It stops before adoption; all other scenes are reads.
No customer data, real host, provider or credential is used. `mei.lin` is a synthetic
actor ID; repository, host and component identifiers retain their fixture names.

Public images are 1440px wide, in light theme and UTC, with actual browser locales
`en-US` and `zh-TW`. The viewport is 1440 × 960, or 1440 × 1600 for Requirements
so its independently scrolling inspector fits. Captures include the full page;
image height can exceed the viewport. The manifest records both sizes, and the
site uses the PNG dimensions to reserve the correct space. The fixture and browser clock are fixed at
`2026-10-13T09:00:00.000Z`. The capture uses ordinary navigation, composer and
inspector controls, including More project tools for the collapsed management views. It does not hide, replace or rewrite DOM content, or change product styles.
The app also supports the user's system dark theme.

[`manifest.json`](manifest.json) records the merged source commit and frontend
tree, capture time, fixture clock, browser/Node versions, actual locale and viewport.
SHA-256 hashes cover the script, demo dependencies, lockfile (excluding only the
root package version), served browser assets and every PNG. Each scene has
fresh-context overflow checks at 390, 768 and 1440px.
Browser/console errors and external requests fail the capture.
These checks concern shared-browser layout, not native or live acceptance.

To repeat the capture:

1. Use an isolated checkout of `437ee6a7143a82e32eae1d6a008203b2df072aaf` and copy the current `scripts/capture_site.mjs` into it. Keep the pinned frontend, demo, conversation fixture and browser assets unchanged. Use Node 24 and the locked `desktop/` dependencies (`npm ci`), with Playwright Chromium installed.
2. Run `npm run check:browser` from `desktop/` to check the committed shared assets.
3. From the repository root, choose a disk-backed temporary directory and run:

   ```sh
   CAPTURE_REVIEW_DIR="$TMPDIR/review/gallery" \
     node --experimental-strip-types scripts/capture_site.mjs
   ```

4. Inspect all sixteen public images, the 48 responsive previews and 18 additional review captures in `CAPTURE_REVIEW_DIR`.
   Review the regenerated manifest before committing.

重製時請使用 `437ee6a7` 的獨立 checkout，放入目前的擷取腳本，保留該來源的前端、demo、對話 fixture 與瀏覽器資產。
腳本透過「更多專案工具」開啟收合的管理頁，不改 DOM 或樣式。
檢查十六張公開圖片、48 張不同寬度的預覽及 18 張補充畫面，再連同 manifest 更新圖庫。

The script owns its ephemeral loopback server and Chromium, and closes both on exit.
The script's source-byte guard refuses frontend, demo and dashboard asset drift
against the pinned commit before capturing.
The [checker](../../scripts/check_product_site.py) verifies every recorded capture
input against that commit, including the conversation fixture, except
`scripts/capture_site.mjs` and `desktop/package-lock.json`. Those two inputs are
verified against the checkout, with only the lockfile's root version excluded.

腳本的來源位元組檢查會在擷取前拒絕前端、demo 與 Dashboard 資產偏離固定 commit。
[校驗程式](../../scripts/check_product_site.py)會對照固定 commit 驗證所有記錄的擷取輸入，
包括對話 fixture；只有 `scripts/capture_site.mjs` 與 `desktop/package-lock.json`
改以工作樹內容校驗，且僅排除 lockfile 的根套件版本。

To refresh, deliberately update the pin, review fixture compatibility and regenerate
all scenes. Rendering may vary with browser and system fonts; the manifest
identifies the files actually produced.
