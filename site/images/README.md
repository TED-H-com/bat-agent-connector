# Product screenshots

These sixteen unedited PNGs show the real shared browser frontend from merged main
`d66f032d342562322479c5087c6982d662d11a01`, using the single membership demo project
in [`desktop/tests/demo/index.ts`](../../desktop/tests/demo/index.ts).
This pin includes merged updates through #181 on 2026-10-15: quiet session status and explained history (#152), shell focus (#155), single-message failure presentation and meetings guidance (#162, #175), M4 lifecycle evidence (#116), Settings diagnostics export (#171), read-only session work paths and agent setup (#169), on-demand PM views (#174), TED-H-com repository links (#177), Git transport contract (#167), and UI polish across phone headers, single-line owner columns and zh-TW localization (#172, #179, #180).
The development preview is shared-browser evidence; this refresh provides no installation or live acceptance evidence.
The [progress report](../../docs/product/progress-2026-10-14.md) separates those states.

| Images (English / zh-TW) | Scene and state |
| --- | --- |
| `overview-en.png`, `overview-zh.png` | Live Overview: eight adopted requirements, three accepted, one blocker, two pending decisions and a recorded forecast three days late. Recently decided shows the two existing synthetic plan adoption receipts, separately from pending decisions. The maintenance alert names its package. The full-page image also includes the six charter sections. Saved reports are separate. |
| `discussions-en.png`, `discussions-zh.png` | Discussion opened from the Overview blocker through its Discuss link. Automatic title, original context chip, open Discussion info / Plan panel and a reviewable proposal; the proposal is not adopted and does not authorize work. |
| `requirements-en.png`, `requirements-zh.png` | RTM with email sign-in expanded and its bound work inspector open. Requirement acceptance, PR merge and deployment remain separate evidence. |
| `issues-en.png`, `issues-zh.png` | Both register pages loaded: nine records, including named maintenance findings, risks, issues, an action, a proposed change and a decision. Open records remain open. |
| `schedule-en.png`, `schedule-zh.png` | Recorded baseline, forecast and actual dates on a labelled axis, with Design review collapsed through its normal control. Phone tracks use one fitted date range. Recovery review is forecast three days late; undated session attempts remain unscheduled. |
| `dependencies-en.png`, `dependencies-zh.png` | Pinned inventory graph with its Partial label and evidence legend. The graph is bounded; missing versions, ownership and freshness remain unknown. |
| `board-en.png`, `board-zh.png` | Search, requirement and agent filters above twelve synthetic work items across five workflow lanes. Waiting and review states remain distinct from accepted content and delivery. |
| `session-en.png`, `session-zh.png` | Manual BAT session on `demo-host`, with its read-only transcript and reviewed work paths. There are no send, answer or interrupt controls. |

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
inspector controls. It does not hide, replace or rewrite DOM content, or change product styles.
The app also supports the user's system dark theme.

[`manifest.json`](manifest.json) records the merged source commit and frontend
tree, capture time, fixture clock, browser/Node versions, actual locale and viewport.
SHA-256 hashes cover the script, demo dependencies, lockfile (excluding only the
root package version), served browser assets and every PNG. Each scene has
fresh-context overflow checks at 390, 768 and 1440px.
Browser/console errors and external requests fail the capture.
These checks concern shared-browser layout, not native or live acceptance.

To repeat the capture:

1. Use Node 24 and the locked `desktop/` dependencies (`npm ci`), with Playwright Chromium installed.
2. Run `npm run check:browser` from `desktop/` to check the committed shared assets.
3. From the repository root, choose a disk-backed temporary directory and run:

   ```sh
   CAPTURE_REVIEW_DIR="$TMPDIR/review/gallery" \
     node --experimental-strip-types scripts/capture_site.mjs
   ```

4. Inspect all sixteen public images, the 48 responsive previews and 18 additional review captures in `CAPTURE_REVIEW_DIR`.
   Review the regenerated manifest before committing.

The script owns its ephemeral loopback server and Chromium, and closes both on exit.
A source-byte check refuses frontend or demo changes against the pinned commit.
To refresh, deliberately update the pin, review fixture compatibility and regenerate
all scenes. Rendering may vary with browser and system fonts; the manifest
identifies the files actually produced.
