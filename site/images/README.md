# Product screenshots

These eight unedited PNGs show the real shared browser frontend at merged main
`662460311345db7dd0a9fe65ea8010dc3e5e4897`, captured on 2026-10-12 with synthetic API
fixtures. They do not show customer data or establish native installation, live
provider acceptance, completed delivery, or features from later branches.

| Images | Scene |
| --- | --- |
| `workspace-en.png`, `workspace-zh.png` | Project tree, original conversation, reply composer and result inspector. The example is still running and its result is unverified. |
| `dispatch-en.png`, `dispatch-zh.png` | An unsubmitted project dispatch form with an explicit repository/host/workspace selection. No version preview or execution is requested. |
| `pm-planning-en.png`, `pm-planning-zh.png` | A fixed Planning revision with scope, proposed acceptance criteria and open decisions. It has not been adopted; acceptance and budget still need an owner review. |
| `pm-kanban-en.png`, `pm-kanban-zh.png` | Seven synthetic work items in the existing five workflow columns, unassessed DoD, and explicit incomplete source coverage. Content approval is not delivery acceptance. |

Public images are 1440 × 960. English uses browser locale `en-US`; Traditional
Chinese uses `zh-TW`. All scenes use the existing light theme for a consistent
tour; the application also supports the system dark theme. The capture opens normal disclosures, fills an
unsubmitted form and scrolls the page. It does not replace, hide or rewrite DOM
content. Fixture setup and the immutable draft are synthetic server responses.

[`manifest.json`](manifest.json) records the pinned frontend tree, hashes of the
capture script, fixture dependency files, lockfile, served browser assets and
each PNG. It also records browser/Node versions, a fixed fixture clock, UTC time
zone, actual locale and viewport, and overflow checks at 390, 768 and 1440 pixels
for every scene. These checks concern layout, not native or live acceptance.

To repeat this capture:

1. Use Node 24 and install the locked dependencies in `desktop/` with `npm ci`.
   Install the Playwright Chromium browser if it is not already available.
2. Run `npm run check:browser` from `desktop/` to check the committed shared assets.
3. From the repository root, run the following. Keep temporary data on disk; the
   browser capture does not require a backend test run.

   ```sh
   mkdir -p "$HOME/agent-work/tmp/native-pm"
   env TMPDIR="$HOME/agent-work/tmp/native-pm" \
     CAPTURE_REVIEW_DIR="$HOME/agent-work/tmp/public-screenshot-review" \
     node --experimental-strip-types scripts/capture_site.mjs
   ```

4. Inspect all eight public images and the 24 responsive previews in
   `CAPTURE_REVIEW_DIR`; review the regenerated manifest before committing.

The script owns a temporary loopback HTTP server and closes it and Chromium on
exit. API calls use the committed synthetic fixtures; external requests fail the
capture. No live Connector or credentials are needed. A source-byte check refuses
to capture frontend changes against the old pinned commit. To refresh for a new
frontend revision, deliberately update the script's source commit, review fixture
compatibility and regenerate the complete set. Rendering may vary between browser
or system-font versions; the manifest identifies the files actually produced.
