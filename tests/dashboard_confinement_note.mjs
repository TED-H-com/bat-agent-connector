// A10 / 計畫 §06, §10: render the real start note and translations without booting the Dashboard.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

// Browser assets are generated bundles; exercise the shared canonical source directly.
const root = new URL("../desktop/src/", import.meta.url);
const app = readFileSync(new URL("app.js", root), "utf8")
  .replace(/^import .*;\n/gm, "").replace(/^startTimeTicker\(\);\n/gm, "")
  .replace(/\nstart\(\);\s*$/, "\n");
const dom = readFileSync(new URL("ui/dom.js", root), "utf8")
  // Evaluate all shared helpers in the VM; app.js imports h as dom.
  .replace(/^export /gm, "").replace("function h(", "function dom(");
const i18nSource = readFileSync(new URL("i18n.js", root), "utf8");
// Load the actual named translation imports, including newly added PM panels.
// The fixture still executes the canonical translator and start-note behavior.
const translations = {};
for (const [, names, path] of i18nSource.matchAll(/^import\s+\{([^}]+)\}\s+from\s+["']([^"']+)["'];?$/gm)) {
  const source = await import(new URL(path, root));
  for (const binding of names.split(",")) {
    const [original, local = original] = binding.trim().split(/\s+as\s+/);
    assert(Object.hasOwn(source, original), `Missing translation export ${original}`);
    translations[local] = source[original];
  }
}
const i18n = i18nSource
  .replace(/^import .*;\n/gm, "").replace(/^export (?=const locale\b)/gm, "")
  .replace("export function t", "function t");

class FixtureNode {
  constructor() { this.textContent = ""; this.events = {}; }
  setAttribute() {}
  matches() { return false; } // The start note is a paragraph, never a chip.
  append() {}
  addEventListener(name, callback) { this.events[name] = callback; }
}

function render(language, effect, agent, declared = true) {
  const context = vm.createContext({
    navigator: { language }, Node: FixtureNode, nativeDesktop: false, ...translations,
    document: { createElement: () => new FixtureNode(), createTextNode: () => new FixtureNode(), addEventListener() {} },
  });
  vm.runInContext(dom + "\n" + i18n + "\n" + app + "\nglobalThis.fixture = { state, confinementNote, t };", context);
  const { state, confinementNote, t } = context.fixture;
  const reason = "fixture_check_reason"; // no JS copy of the server's supported-reason list
  // Contradict the status to prove that routing uses only the server's start_effect.
  state.caps = { hosts: [{ host: "h1", confinement: { host_account: {
    declared, status: effect === "verified" ? "unknown" : "verified", reason, start_effect: effect,
  } } }] };
  const select = new FixtureNode();
  select.value = agent;
  return { note: confinementNote("h1", select), select, state, t, reason };
}

for (const language of ["en", "zh-TW"]) {
  for (const effect of ["verified", "recheck", "fallback_default", "refused"]) {
    for (const agent of ["claude", "codex"]) {
      test(`confinementNote ${effect} ${agent} ${language}`, () => {
        const { note, t, reason } = render(language, effect, agent);
        const blocked = t("confinement_account_blocked", { reason });
        const codex = t("confinement_codex_note");
        if (agent === "codex") {
          assert.equal(note.textContent, effect === "refused" ? blocked + " " + codex : codex);
          assert.ok(!note.textContent.includes(language === "en" ? "acceptEdits" : "自動接受編輯"));
        } else {
          const expected = {
            verified: t("confinement_account_note"),
            recheck: t("confinement_account_recheck"),
            fallback_default: t("confinement_account_fallback", { reason }) + " " + t("confinement_claude_note"),
            refused: blocked,
          };
          assert.equal(note.textContent, expected[effect]);
          if (effect === "recheck") {
            assert.ok(note.textContent.includes(language === "en" ? "acceptEdits" : "自動接受編輯")
              && note.textContent.includes(language === "en" ? "default" : "一般權限模式"));
            assert.ok(note.textContent.includes(language === "en" ? "checked again" : "重新查核"));
            assert.ok(note.textContent.includes(language === "en" ? "refuse the start" : "拒絕啟動"));
          }
          if (effect === "fallback_default") {
            assert.ok(note.textContent.includes(reason));
            assert.ok(note.textContent.includes(language === "en" ? "without acceptEdits" : "不啟用自動接受編輯"));
            assert.ok(note.textContent.includes(language === "en" ? "no OS write isolation" : "沒有作業系統的寫入隔離"));
          }
        }
        assert.ok(!/null|undefined|\[object/.test(note.textContent));
      });
    }
  }
  for (const agent of ["claude", "codex"]) {
    test(`confinementNote undeclared ${agent} ${language}`, () => {
      const { note, t } = render(language, "fallback_default", agent, false);
      assert.equal(note.textContent, t(agent === "codex" ? "confinement_codex_note" : "confinement_claude_note"));
      assert.ok(!note.textContent.includes("fixture_check_reason"));
    });
  }
  test(`confinementNote refreshes on agent and host changes ${language}`, () => {
    const { note, select, state, t, reason } = render(language, "recheck", "claude");
    select.value = "codex";
    select.events.change();
    assert.equal(note.textContent, t("confinement_codex_note"));
    state.caps.hosts.push({ host: "h2", confinement: { host_account: { start_effect: "refused", reason } } });
    note.setHost("h2");
    assert.equal(note.textContent, t("confinement_account_blocked", { reason }) + " " + t("confinement_codex_note"));
  });
}
