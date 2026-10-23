# Maintaining the Better Agent Dashboard website

This repository owns the public GitHub Pages introduction. The operational Web
Dashboard is served by Python and shares `desktop/src` with Tauri; this static
site does not contact a central, BAT host, model provider or analytics service.

The introduction links to `/demo/`, a build of the actual shared Dashboard with
synthetic read-only data. Its transport is replaced only in this build and its CSP
blocks network connections. `/prototype/v1.3/` remains a separate simulated workflow
preview; neither is evidence of live execution. Preview links to existing product
views open the demo in a new tab, preserving the preview's draft and work.

## What the page explains

The product introduction follows one project journey: understand its state, find a
problem, discuss it with the PM and review a decision in the same context.
It retains Windows Fleet, central authority, platform differences and installation guidance.

The redesigned views and sixteen screenshots are a **development preview from merged main**
pinned to `437ee6a7143a82e32eae1d6a008203b2df072aaf`.
This is the published Connector 0.2.13 / desktop 0.1.9 prerelease source through #395 (2026-10-22). The compact gallery shows existing PM views and a read-only manual session with synthetic data. It does not open Start work or the Work Result inspector. It does not show later changes #399–#403. Native coding/AHP routes, full PM lifecycle work and real BAT/model/device acceptance remain separate open work.

這十六張圖固定於已發布的 Connector 0.2.13／desktop 0.1.9 預覽版來源 `437ee6a7`（2026-10-22，#395），使用合成資料展示既有 PM 檢視與唯讀人工工作階段。圖庫沒有開啟「開始工作」或 Work 成果檢視器，也不含後續 #399–#403。原生 coding／AHP 路線、完整 PM 生命週期及真 BAT／模型／裝置驗收仍分別待完成。
This refresh provides no installation or live evidence.
本次圖庫更新不提供安裝或實機驗收證據。
CI and browser screenshots do not establish native or live acceptance.
Keep validation outcomes in the [implementation status](../docs/product/implementation-status.md);
link it from the site. The [October 12 report](../docs/product/progress-2026-10-12.md) retains historical evidence.
The [handoff](../docs/handoff/2026-10-14-docs-ux-v2.md) has development continuation
steps, while [PM status](../docs/product/pm-v1.2-status.md) retains the full backlog.

## Edit the source

| File | Responsibility |
| --- | --- |
| `scripts/build_product_site.py` | English/Traditional Chinese copy, semantic sections, metadata and gallery entries |
| `site/index.html` | Generated output; regenerate after source edits |
| `site/styles.css` | Existing paper/ink/action tokens and responsive components; see [DESIGN.md](DESIGN.md) |
| `site/lang.js`, `site/app.js` | Language preference, mobile navigation and screenshot selection; no external requests |
| `site/404.html` | Bilingual missing-page recovery at the production Pages base path |
| `scripts/capture_site.mjs` | Actual shared-frontend capture with local synthetic fixtures |
| `site/images/manifest.json` | Pinned frontend source, fixture/browser details and image/source hashes |
| `desktop/demo/` | Build-time transport, locale and banner for the shared Dashboard demo |
| `desktop/prototype/v1.3/` | Separate fixture preview of the proposed work flow |

Keep the English and [Traditional Chinese README](../README.zh-TW.md) aligned with
the same source. Preserve the [first-use guides](../docs/getting-started.md) as the
place for setup details. Do not recreate this site with the old external page kit:
its former MCP/CLI-only description does not describe today's product.

The site's original palette, mark and language bootstrap came from the repository's
previous [project-page kit](https://github.com/teddashh/teddashh.github.io/tree/main/kit).
Continue the existing design instead of introducing another visual system.

## Refresh every public screenshot

The gallery has eight scenes in two languages: Overview, Discussions, Requirements,
Issues & changes, Schedule, Dependencies, Board and a read-only manual Session. See [screenshot provenance and reproduction](images/README.md).
Full-page images retain text below the viewport, including the Overview charter.
The Requirements scene uses a taller viewport to retain the work inspector.
The generated gallery uses the manifest’s actual PNG dimensions.
All are unedited captures of the real shared frontend using one synthetic demo project.
The discussion proposal is unadopted; unassessed quality and incomplete coverage remain visible.
Recently decided uses the existing synthetic plan adoption receipts. Named findings and
the Schedule axis come from the merged UI. Agents and Overview empty-state variants
remain review captures, keeping the public tour at eight scenes.

When frontend source changes, update the deliberate source pin, check fixture
compatibility, regenerate all sixteen images and inspect the 390/768/1440 previews.
Commit the resulting manifest with the images. Do not substitute invented product
screens, private conversations or later candidate UI under a main-version caption.

## Verify locally

From the repository root, with `desktop/` dependencies and Playwright Chromium installed:

```bash
cd desktop
npm run build:demo
npm run build:prototype
cd ..
mkdir -p site/demo site/prototype/v1.3
cp -R desktop/dist-demo/. site/demo/
cp -R desktop/dist-prototype/v1.3/. site/prototype/v1.3/
python3 scripts/build_product_site.py
python3 scripts/build_product_site.py --check
python3 scripts/check_product_site.py
python3 scripts/check_public_content.py
SITE_SCREENSHOT_DIR="$TMPDIR/review/site" \
  node scripts/verify_product_site.mjs
```

The browser verifier owns an ephemeral loopback server at the production
`/bat-agent-connector/` path and closes it on exit. It checks both languages at
390/768/1440, all eight gallery choices, keyboard/mobile navigation, preferences,
blocked storage, no-JavaScript fallback, image decoding, overflow and 404 recovery.
It also follows the introduction → Dashboard demo → workflow preview → Requirements
demo journey at the production base path, checking that the preview keeps its draft.
It rejects page/console errors and external requests. Inspect the resulting images;
a green script does not prove the prose describes the product accurately.

For a manual preview:

```bash
python3 -m http.server 18745 --bind 127.0.0.1 --directory site
```

Open `http://127.0.0.1:18745/` or `?lang=zh-TW`; stop the server afterward.

## Review accuracy before publishing

- Match each feature to merged source, a labelled candidate or planned work.
- Keep desktop client, Python central and execution host separate. A Mac/Windows
  desktop package does not prove native PM or general coding support on that OS.
- Keep Fleet connections/profiles/startup discoverable; PM does not replace them.
- Describe acceptance, runtime completion, PR merge, deployment and maintenance
  policy as separate facts with their original evidence.
- Local Codex/Claude collection exists. Remote collection and delivery-efficiency
  attribution remain planned. Keep observed tokens, source-reported quota and bills
  separate; do not imply automatic model changes.
- Keep source checks, platform fixtures, owner installation and live acceptance
  separate. Validation artifacts are not a stable signed release.
- Preserve BAT/Project Hub attribution, manual-resource protection, original-key
  recovery and the explicit distribution exclusions.

## Publish through the repository

The existing Pages workflow validates PRs. A main push with site changes validates
again and deploys `site/` through GitHub Pages. Merge only the reviewed source with
its required checks; then verify the deployed page and screenshots. A local preview,
PR validation or successful build alone does not establish a published website.
