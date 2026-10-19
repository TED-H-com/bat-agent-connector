import {test, expect} from "@playwright/test";
import {readFileSync} from "node:fs";
import {fleetDiagnosticsTests} from './fleet-diagnostics';
const fixture = JSON.parse(readFileSync(new URL("./fixtures/fleet-status.json", import.meta.url), "utf8")).result;
fleetDiagnosticsTests(setup, false);

async function setup(page: any, options: any = {}) {
  if (options.clock) await page.clock.install();
  await page.addInitScript(({doc, options}: any) => {
    const env = {doc, calls: [] as any[], lose: false, failReads: false, statusCalls: 0, fleetReads: 0, availabilityReads: 0, releaseStatus: null as any, hold: null as string | null, release: null as null | (() => void), ...options};
    const caps = {actor: "fleet-fixture", scopes: ["observe"], api_version: 1, contract_version: "2026-10-08", features: {}, hosts: [], actions: []};
    const checkpoint = {cursor: 0, token: "fixture"};
    Object.assign(window, {__fleet: env, isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
      if (command === "native_status") {
        env.statusCalls++;
        if (options.delayStatus && env.statusCalls > 1) await new Promise(resolve => {env.releaseStatus = resolve;});
        return options.connected ? {endpoint: "https://fixture.example", error: null, credential_available: true}
          : {endpoint: null, error: "Fixture: central is offline", credential_available: false};
      }
      if (command === "connector_connect") {if (options.connected) return caps; throw new Error("Fixture: central is offline");}
      if (command === "connector_request") return {status: 200, data: args.input.path.startsWith("/bootstrap")
        ? {capabilities: caps, sync: {version: 1, server_id: "fixture", principal_id: "fixture", checkpoint}}
        : args.input.path.startsWith("/events") ? {events: [], head_cursor: 0, next_cursor: 0, sync: {checkpoint}}
        : {sessions: [], hosts: [], work_items: [], operations: []}};
      if (command === "fleet_availability") {env.availabilityReads++; return {configured: true, platform_supported: options.supported !== false};}
      if (command !== "fleet_request") throw new Error("Unexpected command " + command);
      const input = structuredClone(args.input);
      if (input.action === "status") env.fleetReads++;
      else env.calls.push(input);
      if (env.hold === input.action) await new Promise<void>(resolve => {env.release = resolve;});
      if (input.action === "status") {
        if (env.failReads) throw new Error("Fixture unavailable");
        return structuredClone(env.doc);
      }
      if (input.action === "set_connections") {
        env.doc.selection.connections = input.connections;
        env.doc.selection.revision = "e".repeat(64);
      }
      if (env.lose) {env.failReads = true; throw new Error("Fixture lost reply");}
      return structuredClone(env.doc);
    }}});
  }, {doc: fixture, options});
  await page.goto("/dashboard/#/settings");
  if (!options.delayStatus) await page.getByRole("link", {name: /^(Fleet|機群)$/}).click();
}

for (const locale of ["en-US", "zh-TW"]) for (const width of [390, 768, 1440]) {
  test(`Fleet selection and readiness ${locale} ${width}`, async ({browser}) => {
    const context = await browser.newContext({locale, viewport: {width, height: 900}});
    const page = await context.newPage();
    const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
    await setup(page);
    await expect(page.locator("[data-fleet-name]")).toHaveCount(6);
    await page.locator('[data-fleet-name="node-2"]').uncheck();
    await page.getByRole("button", {name: locale === "en-US" ? "Save connections" : "儲存連線選擇", exact: true}).click();
    await expect.poll(() => page.evaluate(() => (window as any).__fleet.calls.length)).toBe(1);
    const input = await page.evaluate(() => (window as any).__fleet.calls[0]);
    expect(input).toEqual({action: "set_connections", connections: ["node-1", "node-3", "node-4", "node-5", "connector"],
      expected_configuration_binding: "c".repeat(64), expected_selection_revision: "d".repeat(64), expected_monitor_epoch: "a".repeat(32)});
    await expect(page.getByText(locale === "en-US" ? "Waiting for the monitor to read the current selection." : "等待監控讀取目前選擇。")).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({path: `test-results/fleet-${locale}-${width}.png`, fullPage: true});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(errors).toEqual([]);
    await context.close();
  });
}

test("lost Fleet reply reads back without resending and keeps central independent", async ({page}) => {
  await setup(page, {lose: true});
  await page.locator('[data-fleet-name="node-2"]').uncheck();
  await page.getByRole("button", {name: "Save connections", exact: true}).click();
  await expect(page.getByText("The outcome is unknown;", {exact: false})).toBeVisible();
  await expect(page.getByRole("button", {name: "Save connections", exact: true})).toBeDisabled();
  expect(await page.evaluate(() => sessionStorage.getItem("batc.desktop.fleet.selection." + "c".repeat(64)))).toContain("node-1");
  await page.evaluate(() => {(window as any).__fleet.failReads = false;});
  await page.getByRole("button", {name: "Read status", exact: true}).click();
  await expect(page.locator('[data-fleet-name="node-2"]')).not.toBeChecked();
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("batc.desktop.fleet.selection." + "c".repeat(64)))).toBe(null);
  expect(await page.evaluate(() => (window as any).__fleet.calls.length)).toBe(1);
  await expect(page.getByRole("button", {name: "Connect", exact: true})).toBeDisabled();
});

test("changed monitor or selection blocks a stale draft and other-login control", async ({page}) => {
  await setup(page);
  await page.locator('[data-fleet-name="node-2"]').uncheck();
  await page.evaluate(() => {(window as any).__fleet.doc.selection.revision = "f".repeat(64);});
  await page.getByRole("button", {name: "Read status", exact: true}).click();
  await expect(page.getByText("The selection or monitor changed.", {exact: false})).toBeVisible();
  await expect(page.getByRole("button", {name: "Save connections", exact: true})).toBeDisabled();
  await expect(page.locator('[data-fleet-name="node-2"]')).not.toBeChecked();
  await page.getByRole("button", {name: "Use current selection", exact: true}).click();
  await expect(page.locator('[data-fleet-name="node-2"]')).toBeChecked();
  await page.evaluate(() => {(window as any).__fleet.doc.monitor.controllable = false;});
  await page.getByRole("button", {name: "Read status", exact: true}).click();
  await expect(page.getByText("This monitor is outside", {exact: false})).toBeVisible();
  await expect(page.locator('[data-fleet-name="node-2"]')).toBeDisabled();
  await expect(page.getByRole("button", {name: "Stop local connection monitor", exact: true})).toBeDisabled();
  expect(await page.evaluate(() => (window as any).__fleet.calls.length)).toBe(0);
});

test("Fleet control unavailable on other platforms", async ({page}) => {
  await setup(page, {supported: false});
  await expect(page.getByText("Fleet connection management requires Windows.")).toBeVisible();
  await expect(page.locator("[data-fleet-name]")).toHaveCount(0);
});

const fleetReads = (page: any) => page.evaluate(() => (window as any).__fleet.fleetReads as number);
const selectionKey = "batc.desktop.fleet.selection." + "c".repeat(64);
async function releaseHold(page: any) {
  await page.evaluate(() => {
    const env = (window as any).__fleet;
    env.hold = null;
    env.release?.();
  });
}

test("a status poll keeps button focus", async ({page}) => {
  await setup(page, {clock: true});
  const region = page.getByRole("region", {name: "Local Fleet connections"});
  const refresh = region.getByRole("button", {name: "Read status", exact: true});
  const quit = region.getByRole("button", {name: "Stop local connection monitor", exact: true});
  await expect(refresh).toBeEnabled();
  await expect(quit).toBeEnabled();
  await refresh.focus();
  const before = await fleetReads(page);
  await page.clock.fastForward(5000);
  await expect.poll(() => fleetReads(page)).toBe(before + 1);
  await expect(refresh).toBeFocused();
  await quit.focus();
  await page.clock.fastForward(5000);
  await expect.poll(() => fleetReads(page)).toBe(before + 2);
  await expect(quit).toBeFocused();
});

test("a status poll keeps connection checkbox focus", async ({page}) => {
  await setup(page, {clock: true});
  const box = page.locator('[data-fleet-name="node-1"]');
  await box.focus();
  await expect(box).toBeChecked();
  await page.evaluate(() => {(window as any).__fleet.hold = "status";});
  const before = await fleetReads(page);
  await page.clock.fastForward(5000);
  await expect.poll(() => fleetReads(page)).toBe(before + 1);
  await expect(box).toBeFocused();
  await expect(box).toHaveAttribute("aria-disabled", "true");
  expect(await box.evaluate(node => (node as HTMLInputElement).disabled)).toBe(false);
  await page.keyboard.press("Space");
  await expect(box).toBeChecked();
  expect(await page.evaluate(key => sessionStorage.getItem(key), selectionKey)).toBe(null);
  await releaseHold(page);
  await expect(box).not.toHaveAttribute("aria-disabled");
  await expect(box).toBeFocused();
  await expect(box).toBeChecked();
  expect(await page.evaluate(key => sessionStorage.getItem(key), selectionKey)).toBe(null);
});

test("refresh keeps focus and ignores a second press", async ({page}) => {
  await setup(page, {clock: true});
  const region = page.getByRole("region", {name: "Local Fleet connections"});
  const refresh = region.getByRole("button", {name: "Read status", exact: true});
  const status = region.getByRole("status");
  await page.evaluate(() => {(window as any).__fleet.hold = "status";});
  const before = await fleetReads(page);
  await refresh.focus();
  await page.keyboard.press("Enter");
  await expect(refresh).toHaveAttribute("aria-disabled", "true");
  await expect(refresh).toBeFocused();
  await expect(status).toHaveAttribute("aria-busy", "true");
  expect(await refresh.evaluate(node => (node as HTMLButtonElement).disabled)).toBe(false);
  await page.keyboard.press("Enter");
  await refresh.evaluate(node => (node as HTMLButtonElement).click());
  await releaseHold(page);
  await expect(refresh).not.toHaveAttribute("aria-disabled");
  await expect(status).not.toHaveAttribute("aria-busy");
  expect(await fleetReads(page)).toBe(before + 1);
  await expect(refresh).toBeFocused();
});

test("Apply keeps focus while saving, then moves to Read status", async ({page}) => {
  await setup(page, {clock: true});
  const region = page.getByRole("region", {name: "Local Fleet connections"});
  await page.locator('[data-fleet-name="node-2"]').uncheck();
  await page.evaluate(() => {(window as any).__fleet.hold = "set_connections";});
  const apply = region.getByRole("button", {name: "Save connections", exact: true});
  const refresh = region.getByRole("button", {name: "Read status", exact: true});
  await apply.focus();
  await page.keyboard.press("Enter");
  await expect(apply).toHaveAttribute("aria-disabled", "true");
  await expect(apply).toBeFocused();
  expect(await apply.evaluate(node => (node as HTMLButtonElement).disabled)).toBe(false);
  await releaseHold(page);
  await expect(refresh).toBeFocused();
  expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false);
  expect(await page.evaluate(() => (window as any).__fleet.calls.filter((call: {action: string}) => call.action === "set_connections"))).toHaveLength(1);
});

test("delayed settings status cannot mount Fleet into a later route", async ({page}) => {
  await setup(page, {connected: true, delayStatus: true});
  await expect.poll(() => page.evaluate(() => typeof (window as any).__fleet.releaseStatus)).toBe("function");
  await page.getByRole('button', {name: 'Tools', exact: true}).click();
  await page.getByRole("menuitem", {name: "Sessions", exact: true}).click();
  await expect(page.getByRole("heading", {name: "Sessions", exact: true})).toBeVisible();
  await page.evaluate(async () => {(window as any).__fleet.releaseStatus(); await new Promise(resolve => requestAnimationFrame(resolve));});
  await expect(page.getByRole("region", {name: "Local Fleet connections"})).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__fleet.availabilityReads)).toBe(0);
});

test('Fleet preserves unmapped blocking reasons and native diagnostic codes', async ({page}) => {
  await setup(page);
  for (const native of [false, true]) {
    await page.evaluate((native: boolean) => {
      const row = (window as any).__fleet.doc.readiness.hosts[0];
      Object.assign(row, {level: 'down', code: native ? 'OWNER_UNPROVEN' : null, blocking: native ? 'auth' : 'OWNER_UNPROVEN'});
    }, native);
    await page.getByRole('button', {name: 'Read status', exact: true}).click();
    const status = page.locator('[data-fleet-diagnosis="node-1"]');
    await expect(status.getByText('The background connection outcome is unconfirmed', {exact: true})).toBeVisible();
    if (!native) await status.locator('summary').click();
    await expect(status.locator('code').filter({hasText: 'OWNER_UNPROVEN'})).toBeVisible();
    if (native) await expect(status.locator('code').filter({hasText: 'auth'})).toBeVisible();
  }
});
