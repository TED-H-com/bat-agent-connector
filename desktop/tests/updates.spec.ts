import {test, expect} from "@playwright/test";

async function setup(page: any, options: any = {}) {
  await page.addInitScript(({options}: any) => {
    const candidate = {candidate_id: "opaque-native-candidate", version: "0.2.0", source_sha: "a".repeat(40), workflow_version: "2026-10-08.9"};
    const env = {calls: [] as any[], failReads: false, ...options,
      status: {current_version: "0.1.0", available: options.signed !== false, phase: "idle", candidate: null as any,
        code: options.signed === false ? "UPDATE_SIGNING_NOT_CONFIGURED" : null, installation: null as any}};
    Object.assign(window, {isTauri: true, __updates: env, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === "native_status") return {updates: true, endpoint: null, credential_available: false};
      if (command === "fleet_availability") return {configured: false, platform_supported: true};
      if (command !== "desktop_update") throw new Error("unexpected command");
      const input = structuredClone(args.input);
      env.calls.push(input);
      if (env.holdAction === input.action) {
        env.held = (env.held || 0) + 1;
        if (env.held === 1) await new Promise<void>(resolve => {env.release = resolve;});
      }
      if (env.failAction === input.action) throw new Error("fixture failed");
      if (input.action === "status" && env.failReads) throw new Error("fixture unavailable");
      if (input.action === "check") Object.assign(env.status, {phase: "available", candidate});
      if (input.action === "download") {
        if (input.candidate_id !== candidate.candidate_id) throw new Error("wrong candidate");
        Object.assign(env.status, {phase: options.badSignature ? "download_failed" : "verified",
          code: options.badSignature ? "UPDATE_SIGNATURE_OR_DOWNLOAD_FAILED" : null});
      }
      if (input.action === "install") {
        if (env.status.phase !== "verified" || input.candidate_id !== candidate.candidate_id) throw new Error("unverified candidate");
        Object.assign(env.status, {phase: "installation_unknown", installation: {to_version: candidate.version}});
        if (options.lostInstall) {env.failReads = true; throw new Error("lost response");}
      }
      return structuredClone(env.status);
    }}});
  }, {options});
  await page.goto("/dashboard/#/settings");
  await page.getByRole("link", {name: /^(Updates|更新)$/}).click();
}

test("unsigned package exposes its version without checking a feed", async ({page}) => {
  await setup(page, {signed: false});
  const panel = page.getByRole("region", {name: "Desktop updates"});
  await expect(panel.getByText("Current version: 0.1.0")).toBeVisible();
  await expect(panel.getByText("Signed updates are not enabled for this test package.")).toBeVisible();
  await expect(panel.getByRole("button", {name: "Check for updates"})).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__updates.calls)).toEqual([{action: "status"}]);
});

test("invalid signature never enables install", async ({page}) => {
  await setup(page, {badSignature: true});
  const panel = page.getByRole("region", {name: "Desktop updates"});
  await panel.getByRole("button", {name: "Check for updates"}).click();
  await expect(panel.getByRole("button", {name: "Install and restart"})).toBeDisabled();
  await panel.getByRole("button", {name: "Download and verify"}).click();
  await expect(panel.getByText("Download or signature verification did not succeed.")).toBeVisible();
  await expect(panel.getByRole("button", {name: "Install and restart"})).toBeDisabled();
  expect(await page.evaluate(() => (window as any).__updates.calls.some((x: any) => x.action === "install"))).toBe(false);
});

test("lost installation response only reads the original attempt", async ({page}) => {
  await setup(page, {lostInstall: true});
  const panel = page.getByRole("region", {name: "Desktop updates"});
  await panel.getByRole("button", {name: "Check for updates"}).click();
  await panel.getByRole("button", {name: "Download and verify"}).click();
  await panel.getByRole("button", {name: "Install and restart"}).click();
  await expect(panel.getByText("The installation outcome is unknown.", {exact: false})).toBeVisible();
  await expect(panel.getByRole("button", {name: "Install and restart"})).toBeDisabled();
  await page.evaluate(() => {(window as any).__updates.failReads = false;});
  await panel.getByRole("button", {name: "Read update status"}).click();
  await expect(panel.getByText("The original installation request is saved; its outcome is not confirmed.")).toBeVisible();
  await expect(panel.getByRole("button", {name: "Install and restart"})).toBeDisabled();
  expect(await page.evaluate(() => (window as any).__updates.calls.filter((x: any) => x.action === "install"))).toEqual([{action: "install", candidate_id: "opaque-native-candidate"}]);
});

test("busy focus: Check keeps the keyboard while the check is held", async ({page}) => {
  await setup(page);
  const panel = page.getByRole("region", {name: "Desktop updates"});
  const check = panel.getByRole("button", {name: "Check for updates", exact: true});
  const message = panel.getByRole("status");
  await expect(check).toBeEnabled();
  const before = await page.evaluate(() => (window as any).__updates.calls.filter((call: any) => call.action === "check").length);
  await page.evaluate(() => {(window as any).__updates.holdAction = "check"; (window as any).__updates.held = 0;});
  await check.focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => typeof (window as any).__updates.release === "function");
  await expect(check).toBeFocused();
  await expect(check).toHaveAttribute("aria-disabled", "true");
  await expect(message).toHaveAttribute("aria-busy", "true");
  const held = await page.evaluate(() => (window as any).__updates.calls.filter((call: any) => call.action === "check").length);
  expect(held).toBe(before + 1);
  await page.keyboard.press("Enter");
  await page.evaluate(() => {const env = (window as any).__updates; env.holdAction = ""; env.release();});
  await expect(check).toBeEnabled();
  await expect(check).toBeFocused();
  expect(await page.evaluate(() => (window as any).__updates.calls.filter((call: any) => call.action === "check").length)).toBe(held);
});

test("busy focus: Check stays disabled after a failed update read", async ({page}) => {
  await setup(page);
  const panel = page.getByRole("region", {name: "Desktop updates"});
  const check = panel.getByRole("button", {name: "Check for updates", exact: true});
  await expect(check).toBeEnabled();
  await page.evaluate(() => {(window as any).__updates.failAction = "check";});
  await check.click();
  await expect(panel.getByText("Update status is unavailable. Read the status again.")).toBeVisible();
  await expect(check).toBeDisabled();
  await page.evaluate(() => {const env = (window as any).__updates; env.failAction = ""; env.holdAction = "status"; env.held = 0;});
  const read = panel.getByRole("button", {name: "Read update status", exact: true});
  await read.focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => typeof (window as any).__updates.release === "function");
  await expect(check).toBeDisabled();
  await expect(check).not.toHaveAttribute("aria-disabled");
});

test("busy focus: Download moves to Install", async ({page}) => {
  await setup(page);
  const panel = page.getByRole("region", {name: "Desktop updates"});
  await panel.getByRole("button", {name: "Check for updates", exact: true}).click();
  const download = panel.getByRole("button", {name: "Download and verify", exact: true});
  await expect(download).toBeEnabled();
  await download.focus();
  await page.keyboard.press("Enter");
  const install = panel.getByRole("button", {name: "Install and restart", exact: true});
  await expect(install).toBeEnabled();
  await expect(install).toBeFocused();
  expect(await page.evaluate(() => (window as any).__updates.calls.filter((call: any) => call.action === "download"))).toEqual([{action: "download", candidate_id: "opaque-native-candidate"}]);
});

for (const locale of ["en-US", "zh-TW"]) for (const width of [390, 768, 1440]) {
  test(`verified candidate ${locale} ${width}`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}});
    const page = await context.newPage();
    const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
    await setup(page);
    const panel = page.getByRole("region", {name: locale === "en-US" ? "Desktop updates" : "桌面更新"});
    await panel.getByRole("button", {name: locale === "en-US" ? "Check for updates" : "檢查更新"}).click();
    await panel.getByRole("button", {name: locale === "en-US" ? "Download and verify" : "下載並驗證"}).click();
    await expect(panel.getByRole("button", {name: locale === "en-US" ? "Install and restart" : "安裝並重新啟動"})).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({path: `test-results/updates-${locale}-${width}.png`, fullPage: true});
    expect(errors).toEqual([]);
    await context.close();
  });
}
