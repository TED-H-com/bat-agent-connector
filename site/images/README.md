# Product screenshots

These sixteen unedited PNGs show the real shared browser frontend at candidate
`944a854fc381f61c6281edb7fcbcf887321a0c68`, using the single membership demo project
in [`desktop/tests/demo/index.ts`](../../desktop/tests/demo/index.ts).
This is a development preview, not merged-main, native installation or live acceptance evidence.
The integrator must re-pin the capture to merged main and regenerate before publication.
The [progress report](../../docs/product/progress-2026-10-14.md) separates those states.

| Images (English / zh-TW) | Scene and state |
| --- | --- |
| `overview-en.png`, `overview-zh.png` | Live Overview: eight adopted requirements, three accepted, one blocker, two pending decisions and a recorded forecast three days late. Saved reports are separate. |
| `discussions-en.png`, `discussions-zh.png` | Discussion opened from the Overview blocker through its Discuss link. Automatic title, original context chip and a reviewable proposal; the proposal is not adopted and does not authorize work. |
| `requirements-en.png`, `requirements-zh.png` | RTM with email sign-in expanded and its bound work inspector open. Requirement acceptance, PR merge and deployment remain separate evidence. |
| `issues-en.png`, `issues-zh.png` | Both register pages loaded, showing risks, issues, an action, a proposed change and a decision. Open records remain open. |
| `schedule-en.png`, `schedule-zh.png` | Recorded baseline, forecast and actual dates, with Design review collapsed through its normal control. Recovery review is forecast three days late; undated session attempts remain unscheduled. |
| `dependencies-en.png`, `dependencies-zh.png` | Pinned inventory graph with its Partial label and evidence legend. The graph is bounded; missing versions, ownership and freshness remain unknown. |
| `board-en.png`, `board-zh.png` | Twelve synthetic work items across five workflow lanes. Waiting and review states remain distinct from accepted content and delivery. |
| `session-en.png`, `session-zh.png` | Manual BAT session on `demo-host`, with its read-only transcript. There are no send, answer or interrupt controls. |

All product data is synthetic, including acceptance and delivery receipts.
The discussion scene performs only three mock operations: create a bound thread,
save a note and prepare a proposal. It stops before adoption; all other scenes are reads.
No customer data, real host, provider or credential is used.

Public images are 1440 × 960, light theme, UTC, with actual browser locales
`en-US` and `zh-TW`. The fixture and browser clock are fixed at
`2026-10-13T09:00:00.000Z`. The capture uses ordinary navigation, composer and
inspector controls. It does not hide, replace or rewrite DOM content.
The app also supports the user's system dark theme.

[`manifest.json`](manifest.json) records the candidate source commit and frontend
tree, capture time, fixture clock, browser/Node versions, actual locale and viewport.
SHA-256 hashes cover the script, demo dependencies, lockfile, served browser assets
and every PNG. Each scene has fresh-context overflow checks at 390, 768 and 1440px.
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

4. Inspect all sixteen public images and the 48 responsive previews in `CAPTURE_REVIEW_DIR`.
   Review the regenerated manifest before committing.

The script owns its ephemeral loopback server and Chromium, and closes both on exit.
A source-byte check refuses frontend or demo changes against the pinned commit.
To refresh, deliberately update the pin, review fixture compatibility and regenerate
all scenes. Rendering may vary with browser and system fonts; the manifest
identifies the files actually produced.
