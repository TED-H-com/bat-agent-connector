import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

const PROTO_URL = "/prototype/v1.3/";
const TMP_ROOT = process.env.TMPDIR || os.tmpdir();
const SHOTS_DIR = path.join(TMP_ROOT, "shots");
const VIDEO_DIR = path.join(TMP_ROOT, "video");

const FORBIDDEN_ENGLISH_UI_IN_ZH =
  /\b(Send to PM|Save note only|Update plan|Start work|Send next step|Short Plan Summary Card|Adopt, do later|Adopt and start|Self-reported|Tests passed|PR open|Accepted|Merged|Deployed|Model calls:|Mutations sent to manual session:|Read-Only Manual Session|Reconnect & reconcile|Synthetic fixture only|Unified Intent Composer)\b/;

async function assertPageHealth(page: Page, lang: "en" | "zh-TW") {
  // 1. Strict CSP / zero inline style attributes
  const styledEls = await page.locator("[style]").evaluateAll((els) =>
    els.map((el) => `${el.tagName}.${el.className}[style="${el.getAttribute("style")}"]`),
  );
  expect(styledEls).toEqual([]);

  // 2. Zero horizontal overflow
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth - doc.clientWidth;
  });
  expect(overflow).toBeLessThanOrEqual(1);

  // 3. No raw keys, undefined, null, or untranslated English UI strings in zh-TW
  const bodyText = (await page.locator("body").innerText()) || "";
  expect(bodyText).not.toMatch(/\bundefined\b|\bnull\b/);
  expect(bodyText).not.toMatch(/\bproto\.[a-z0-9_.]+\b/i);
  expect(bodyText).not.toMatch(/\bui_[a-z0-9_]+\b/i);
  if (lang === "zh-TW") {
    expect(bodyText).not.toMatch(FORBIDDEN_ENGLISH_UI_IN_ZH);
  }
}

async function saveShot(page: Page, name: string) {
  fs.mkdirSync(SHOTS_DIR, { recursive: true });
  await page.screenshot({
    path: path.join(SHOTS_DIR, name),
    fullPage: true,
  });
}

for (const lang of ["en", "zh-TW"] as const) {
  for (const width of [1440, 390] as const) {
    test(`v1.3 E00 golden flow & all 8 scenarios (${lang} @ ${width}px)`, async ({ browser }) => {
      test.setTimeout(90_000);
      fs.mkdirSync(SHOTS_DIR, { recursive: true });
      fs.mkdirSync(VIDEO_DIR, { recursive: true });

      const recordVideo =
        width === 1440
          ? {
              dir: VIDEO_DIR,
              size: { width: 1440, height: 900 },
            }
          : undefined;

      const context = await browser.newContext({
        viewport: { width, height: 900 },
        locale: lang,
        ...(recordVideo ? { recordVideo } : {}),
      });
      const page = await context.newPage();

      const consoleErrors: string[] = [];
      page.on("console", (msg) => {
        if (msg.type() === "error") consoleErrors.push(msg.text());
      });
      page.on("pageerror", (err) => {
        consoleErrors.push(String(err));
      });

      const captureAll = (lang === "en" && width === 1440) || (lang === "zh-TW" && width === 390);
      const captureExtra = (lang === "zh-TW" && width === 1440) || (lang === "en" && width === 390);

      await page.goto(`${PROTO_URL}?lang=${lang}`);
      await expect(page.getByTestId("prototype-bar")).toBeVisible();
      await expect(page.getByTestId("fixed-context-strip")).toBeVisible();
      await assertPageHealth(page, lang);

      // Verify <meta name="robots" content="noindex">
      await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex");

      // --- 1. F2: Idea & Save note only (0 model calls) ---
      await expect(page.getByTestId("view-discussions")).toBeVisible();
      await expect(page.getByTestId("counter-model-calls")).toHaveAttribute("data-count", "0");
      if (captureAll) await saveShot(page, `01-idea-${lang}-${width}.png`);

      // Test draft preservation across view switching (§6.1)
      const initialDraft = await page.getByTestId("composer-textarea").inputValue();
      expect(initialDraft.length).toBeGreaterThan(5);
      if (width === 390) {
        await page.getByTestId("nav-drawer-toggle").click();
      }
      await page.getByTestId("nav-item-board").click();
      await expect(page.getByTestId("view-placeholder-board")).toBeVisible();
      await expect(page.getByTestId("preserved-draft-indicator")).toContainText(initialDraft);
      await assertPageHealth(page, lang);
      await page.getByTestId("placeholder-return-discussions").click();
      await expect(page.getByTestId("composer-textarea")).toHaveValue(initialDraft);

      // Click "Save note only" -> 0 model calls
      await page.getByTestId("composer-save-note").click();
      await expect(page.getByTestId("note-saved-banner")).toBeVisible();
      await expect(page.getByTestId("counter-model-calls")).toHaveAttribute("data-count", "0");
      await assertPageHealth(page, lang);
      if (captureAll) await saveShot(page, `02-note-only-${lang}-${width}.png`);

      // Simulate PM unavailable -> draft kept, reason named, fix entry offered (§6.4)
      await page.getByTestId("toggle-pm-unavailable").click();
      await expect(page.getByTestId("pm-unavailable-diagnostic")).toBeVisible();
      await expect(page.getByTestId("composer-send-pm")).toBeDisabled();
      await assertPageHealth(page, lang);
      if (captureAll) await saveShot(page, `03-pm-unavailable-${lang}-${width}.png`);

      // Fix PM and return to thread without pasting again
      await page.getByTestId("fix-pm-available").click();
      await expect(page.getByTestId("composer-send-pm")).toBeEnabled();
      await expect(page.getByTestId("composer-textarea")).toHaveValue(initialDraft);

      // Send to PM -> increments model calls to 1
      await page.getByTestId("composer-send-pm").click();
      await expect(page.getByTestId("counter-model-calls")).toHaveAttribute("data-count", "1");

      // --- 2. F3/F4: Short plan & Adopt steps (§7.3) ---
      await page.getByTestId("suggest-switch-plan").click();
      await expect(page.getByTestId("intent-composer")).toHaveAttribute("data-active-intent", "plan");
      await page.getByTestId("composer-update-plan").click();
      await expect(page.getByTestId("short-plan-card")).toBeVisible();
      await assertPageHealth(page, lang);
      if (captureAll) await saveShot(page, `04-short-plan-${lang}-${width}.png`);

      // Test "Adopt, do later"
      await page.getByTestId("plan-adopt-later").click();
      await expect(page.getByTestId("plan-adopted-later-notice")).toBeVisible();

      // Simulate partial failure: 「計畫已採用，工作尚未開始」
      await page.getByTestId("plan-simulate-partial").click();
      await expect(page.getByTestId("partial-adopt-diagnostic")).toBeVisible();
      await expect(page.getByTestId("adopt-step-s3")).toHaveAttribute("data-step-status", "done");
      await expect(page.getByTestId("adopt-step-s4")).toHaveAttribute("data-step-status", "failed");
      await expect(page.getByTestId("adopt-step-s5")).toHaveAttribute("data-step-status", "pending");
      await assertPageHealth(page, lang);
      if (captureAll || captureExtra) await saveShot(page, `05-adopt-partial-${lang}-${width}.png`);

      // Continue unfinished step -> completes steps 4..8
      await page.getByTestId("continue-unfinished-step").click();
      for (let i = 1; i <= 8; i++) {
        await expect(page.getByTestId(`adopt-step-s${i}`)).toHaveAttribute("data-step-status", "done");
      }
      await expect(page.getByTestId("counter-start-requests")).toHaveAttribute("data-count", "1");
      await assertPageHealth(page, lang);
      if (captureAll || captureExtra) await saveShot(page, `06-adopt-and-start-${lang}-${width}.png`);

      // Open Work Page (F5)
      await page.getByTestId("adopt-open-work-page").click();
      await expect(page.getByTestId("view-work-main")).toBeVisible();
      await expect(page.getByTestId("work-area-header")).toBeVisible();
      await expect(page.getByTestId("work-area-main")).toBeVisible();
      await expect(page.getByTestId("work-area-results")).toBeVisible();
      await expect(page.getByTestId("work-area-advanced")).toBeVisible();
      await assertPageHealth(page, lang);
      if (captureAll || captureExtra) await saveShot(page, `07-work-running-${lang}-${width}.png`);

      // At 390px, verify inspector drawer opens with the same data
      if (width === 390) {
        await page.getByTestId("inspector-drawer-toggle").click();
        await expect(page.getByTestId("context-inspector")).toBeVisible();
        await assertPageHealth(page, lang);
        await page.getByTestId("context-inspector").locator(".ui-inspector-back").click();
      }

      // --- 3. F5: Second sentence queued while busy, NOT steered ---
      await page.getByTestId("btn-queue-second-sentence").click();
      const receipt = page.getByTestId("queued-second-sentence-receipt");
      await expect(receipt).toBeVisible();
      await expect(receipt).toHaveAttribute("data-queue-policy", "after_current");
      await expect(receipt).toHaveAttribute("data-steered", "false");
      await expect(page.getByTestId("work-queue-box")).toHaveAttribute("data-second-steered", "false");
      await assertPageHealth(page, lang);
      if (captureAll || captureExtra) await saveShot(page, `08-work-queued-second-${lang}-${width}.png`);

      // --- 4. F5: Waiting for input ---
      await page.getByTestId("work-state-btn-waiting_input").click();
      await expect(page.getByTestId("work-question-card")).toHaveAttribute("data-question-answered", "false");
      await assertPageHealth(page, lang);
      if (captureAll) await saveShot(page, `09-work-waiting-input-${lang}-${width}.png`);

      // Answer the question -> transitions to Result awaiting verification
      await page.getByTestId("answer-question-keep").click();
      await expect(page.getByTestId("work-question-card")).toHaveAttribute("data-question-answered", "true");
      await assertPageHealth(page, lang);
      if (captureAll || captureExtra) await saveShot(page, `10-work-awaiting-verification-${lang}-${width}.png`);

      // --- 5. Distinct delivery states & PM backfill (§10.2, §12, A32) ---
      const stateLabels: string[] = [];
      for (const st of ["self_reported", "tests_passed", "pr_open", "accepted", "merged", "deployed"]) {
        const el = page.getByTestId(`delivery-state-${st}`);
        await expect(el).toBeVisible();
        const label = await el.locator(".proto-delivery-step__label").innerText();
        stateLabels.push(label.trim());
      }
      expect(new Set(stateLabels).size).toBe(6);

      // Before PR creation: self_reported & tests_passed are true, pr_open is false
      await expect(page.getByTestId("delivery-state-self_reported")).toHaveAttribute("data-delivery-achieved", "true");
      await expect(page.getByTestId("delivery-state-tests_passed")).toHaveAttribute("data-delivery-achieved", "true");
      await expect(page.getByTestId("delivery-state-pr_open")).toHaveAttribute("data-delivery-achieved", "false");

      // Create PR from Results
      await page.getByTestId("btn-create-pr").click();
      await expect(page.getByTestId("delivery-state-pr_open")).toHaveAttribute("data-delivery-achieved", "true");
      await expect(page.getByTestId("delivery-state-accepted")).toHaveAttribute("data-delivery-achieved", "false");
      await assertPageHealth(page, lang);
      if (captureAll) await saveShot(page, `11-work-pr-and-pm-backfill-${lang}-${width}.png`);

      // Owner makes 1 decision and returns to Overview (§12.1)
      await page.getByTestId("decision-accept-overview").click();
      await expect(page.getByTestId("view-overview")).toBeVisible();
      await expect(page.getByTestId("delivery-state-accepted")).toHaveAttribute("data-delivery-achieved", "true");
      await expect(page.getByTestId("pm-owner-decision-banner")).toBeVisible();
      await assertPageHealth(page, lang);
      if (captureAll) await saveShot(page, `12-overview-decision-return-${lang}-${width}.png`);

      // --- 6. F6: External Read-Only Manual BAT Session ---
      await page.getByTestId("scenario-picker").selectOption("external_readonly");
      const manualView = page.getByTestId("view-external-manual");
      await expect(manualView).toBeVisible();
      await expect(manualView).toHaveAttribute("data-session-readonly", "true");
      // Verify zero send/answer/resume/interrupt inputs inside manual session view
      await expect(manualView.locator("textarea, input")).toHaveCount(0);

      await page.getByTestId("manual-view-original").click();
      await expect(page.getByTestId("manual-view-original-notice")).toBeVisible();
      await page.getByTestId("manual-link-existing").click();
      await expect(page.getByTestId("manual-linked-notice")).toBeVisible();
      await page.getByTestId("manual-derive-new").click();
      await expect(page.getByTestId("manual-derived-notice")).toContainText("4b91e0c");
      await expect(page.getByTestId("manual-derived-notice")).toContainText("scratch.txt");
      await expect(page.getByTestId("counter-manual-mutations")).toHaveAttribute("data-count", "0");
      await assertPageHealth(page, lang);
      if (captureAll || captureExtra) await saveShot(page, `13-external-readonly-${lang}-${width}.png`);

      // --- 7. F7: External Agent Work (Hermes via MCP) ---
      await page.getByTestId("proto-trigger-hermes").click();
      const hermesView = page.getByTestId("view-hermes-work");
      await expect(hermesView).toBeVisible();
      await expect(hermesView).toHaveAttribute("data-external-controller", "Hermes");
      await expect(hermesView.locator("textarea, input")).toHaveCount(0);

      await page.getByTestId("hermes-send-inbox").click();
      await expect(page.getByTestId("hermes-inbox-feedback")).toBeVisible();
      await expect(page.getByTestId("counter-hermes-mutations")).toHaveAttribute("data-count", "0");
      await assertPageHealth(page, lang);
      if (captureAll || captureExtra) await saveShot(page, `14-hermes-mcp-work-${lang}-${width}.png`);

      // --- 8. Disconnect Recovery (§13) ---
      await page.getByTestId("scenario-picker").selectOption("disconnect_recovery");
      await expect(page.getByTestId("disconnect-recovery-card")).toBeVisible();
      await expect(page.getByTestId("counter-start-requests")).toHaveAttribute("data-count", "1");
      await expect(page.getByTestId("counter-duplicate-starts")).toHaveAttribute("data-count", "0");
      await assertPageHealth(page, lang);
      if (captureAll) await saveShot(page, `15-disconnect-recovery-${lang}-${width}.png`);

      // Reconnect & reconcile original operation -> 0 duplicate starts, original key visible in Advanced
      await page.getByTestId("btn-reconnect-reconcile").click();
      await expect(page.getByTestId("reconciled-banner")).toBeVisible();
      await expect(page.getByTestId("counter-start-requests")).toHaveAttribute("data-count", "1");
      await expect(page.getByTestId("counter-duplicate-starts")).toHaveAttribute("data-count", "0");
      await expect(page.getByTestId("work-area-advanced")).toContainText("opk_signin_double_submit_start_01");
      await assertPageHealth(page, lang);

      // --- 9. Maintenance (§12.5) ---
      await page.getByTestId("scenario-picker").selectOption("maintenance");
      await expect(page.getByTestId("view-maintenance")).toBeVisible();
      await page.getByTestId("maint-return-review").click();
      await expect(page.getByTestId("maint-review-returned-banner")).toBeVisible();
      await assertPageHealth(page, lang);
      if (captureAll) await saveShot(page, `16-maintenance-fix-${lang}-${width}.png`);

      // --- 10. Dark theme check ---
      await page.getByTestId("scenario-picker").selectOption("awaiting_verification");
      await page.getByTestId("proto-theme-toggle").click();
      await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
      await assertPageHealth(page, lang);
      if (captureAll) await saveShot(page, `17-dark-theme-work-${lang}-${width}.png`);

      expect(consoleErrors).toEqual([]);

      const video = page.video();
      await context.close();
      if (video && width === 1440) {
        const savedVideoPath = await video.path();
        const destVideoPath = path.join(VIDEO_DIR, `golden-flow-${lang}-1440.webm`);
        if (fs.existsSync(savedVideoPath)) {
          fs.renameSync(savedVideoPath, destVideoPath);
        }
      }
    });
  }
}
