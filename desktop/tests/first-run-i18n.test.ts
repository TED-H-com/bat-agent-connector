import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readdirSync, readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parse} from 'acorn';
import {STRINGS, detectLocale, initialLocale} from '../src/i18n.js';

for (const language of ['zh-TW', 'zh-Hant', 'zh-Hant-TW', 'zh-HK', 'zh-MO', 'ZH-tw'])
  test(`first run selects Traditional Chinese for ${language}`, () => assert.equal(detectLocale(language), 'zh-TW'));
for (const language of ['en', 'en-US', 'ja-JP', 'zh', 'zh-CN', 'zh-Hans', 'zh-Hans-CN', 'fr-FR'])
  test(`first run selects English for ${language}`, () => assert.equal(detectLocale(language), 'en'));
test('explicit language survives a different browser locale; invalid/blocked storage falls back safely', () => {
  assert.equal(initialLocale({getItem: () => 'en'}, 'zh-TW'), 'en');
  assert.equal(initialLocale({getItem: () => 'zh-TW'}, 'en-US'), 'zh-TW');
  assert.equal(initialLocale({getItem: () => 'invalid'}, 'zh-HK'), 'zh-TW');
  assert.equal(initialLocale({getItem: () => {throw Error('blocked');}}, 'zh-Hant'), 'zh-TW');
});

test('every first-run translation and error has an explicit zh-TW entry with matching variables', () => {
  const keys = new Set(Object.keys(STRINGS.en).filter(key => /^(setup_|managed_|desktop_|error_code_|error_generic|global_first_)/.test(key)));
  const sources = ['managed-setup.js', 'global-screens.js'];
  const app = readFileSync(new URL('../src/app.js', import.meta.url), 'utf8');
  const fragments = [app.slice(app.indexOf('function languageSwitch()'), app.indexOf('// Credential dialogs')),
    app.slice(app.indexOf('async function viewNativeSettings('), app.indexOf('async function viewNativeSettings(') + app.slice(app.indexOf('async function viewNativeSettings(')).indexOf('\nfunction ', 1))];
  for (const source of [...sources.map(file => readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8')), ...fragments]) {
    const tree = parse(source, {ecmaVersion: 'latest', sourceType: 'module'});
    function visit(node: any) {
      if (!node || typeof node !== 'object') return;
      if (node.type === 'CallExpression') {
        if (node.callee.name === 't' && node.arguments[0]?.type === 'Literal') keys.add(node.arguments[0].value);
        if (['input', 'checkbox', 'action'].includes(node.callee.name) && node.arguments[1]?.type === 'Literal') keys.add(node.arguments[1].value);
        if (node.callee.name === 'h') for (const child of node.arguments.slice(2)) {
          if (child.type === 'Literal' && typeof child.value === 'string')
            assert.doesNotMatch(child.value, /[A-Za-z\u4e00-\u9fff]{2}/, `Untranslated setup text: ${child.value}`);
        }
      }
      for (const child of Object.values(node)) if (Array.isArray(child)) child.forEach(visit); else visit(child);
    }
    visit(tree);
  }
  for (const key of keys) {
    assert.ok(Object.hasOwn(STRINGS.en, key), `Missing English first-run key: ${key}`);
    assert.ok(Object.hasOwn(STRINGS['zh-TW'], key) && STRINGS['zh-TW'][key].trim(), `Missing zh-TW first-run key: ${key}`);
    const variables = (value: string) => [...value.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort();
    // Chinese count labels do not need the English singular/plural nouns.
    if (key !== 'setup_counts') assert.deepEqual(variables(STRINGS['zh-TW'][key]), variables(STRINGS.en[key]), key);
  }
});

test('every literal t(...) key in desktop/src exists in en and zh-TW, and shared keys have matching placeholders', () => {
  const srcRoot = fileURLToPath(new URL('../src', import.meta.url));
  const walk = (dir: string): string[] => readdirSync(dir, {withFileTypes: true}).flatMap(entry => {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) return walk(full);
    return (entry.name.endsWith('.js') || (entry.name.endsWith('.ts') && !entry.name.endsWith('.d.ts'))) ? [full] : [];
  });
  // reported
  const reportedMissingKeys = new Set<string>([
    // reported
  ]);
  // reported
  const reportedPlaceholderMismatches = new Set<string>([
    'setup_counts', // reported
    'wres_changes_summary_one', // reported
    'wres_changes_summary_one_file', // reported
    'wres_changes_summary_one_commit', // reported
  ]);
  const literalKeys = new Set<string>();
  for (const file of walk(srcRoot)) {
    const raw = readFileSync(file, 'utf8');
    const source = file.endsWith('.ts') ? stripTypeScriptTypes(raw) : raw;
    const tree = parse(source, {ecmaVersion: 'latest', sourceType: 'module'});
    function visit(node: any) {
      if (!node || typeof node !== 'object') return;
      if (node.type === 'CallExpression' && node.callee?.type === 'Identifier' && node.callee.name === 't'
          && node.arguments[0]?.type === 'Literal' && typeof node.arguments[0].value === 'string') {
        literalKeys.add(node.arguments[0].value);
      }
      for (const child of Object.values(node)) {
        if (Array.isArray(child)) child.forEach(visit);
        else visit(child);
      }
    }
    visit(tree);
  }
  assert.ok(literalKeys.has('reason_unavailable'));
  assert.ok(literalKeys.has('pm_meeting_more'));
  for (const key of literalKeys) {
    if (reportedMissingKeys.has(key)) {
      assert.ok(!Object.hasOwn(STRINGS.en, key) && !Object.hasOwn(STRINGS['zh-TW'], key), `Allowlisted missing key is now defined: ${key}`);
      continue;
    }
    assert.ok(Object.hasOwn(STRINGS.en, key) && STRINGS.en[key].trim(), `Missing English key for t('${key}')`);
    assert.ok(Object.hasOwn(STRINGS['zh-TW'], key) && STRINGS['zh-TW'][key].trim(), `Missing zh-TW key for t('${key}')`);
  }
  assert.deepEqual(new Set(Object.keys(STRINGS['zh-TW'])), new Set(Object.keys(STRINGS.en)));
  const placeholders = (value: string) => [...new Set([...value.matchAll(/\{(\w+)\}/g)].map(match => match[1]))].sort();
  for (const key of Object.keys(STRINGS.en)) {
    if (!Object.hasOwn(STRINGS['zh-TW'], key)) continue;
    if (reportedPlaceholderMismatches.has(key)) {
      assert.notDeepEqual(placeholders(STRINGS['zh-TW'][key]), placeholders(STRINGS.en[key]), `Allowlisted mismatch is now equal: ${key}`);
      continue;
    }
    assert.deepEqual(placeholders(STRINGS['zh-TW'][key]), placeholders(STRINGS.en[key]), `Placeholder mismatch for ${key}`);
  }
});

test('project rename input, checkpoint continuation textarea, and placeholder fields in app.js have accessible names in en and zh-TW', () => {
  const appSource = readFileSync(fileURLToPath(new URL('../src/app.js', import.meta.url)), 'utf8');
  assert.ok(appSource.includes('const rename = h("input", { value: p.name, maxlength: 80, "aria-label": t("rename") });'));
  assert.ok(appSource.includes('const instr = h("textarea", { "aria-label": t("start_from_checkpoint") }, text);'));
  for (const key of [
    'rename',
    'start_from_checkpoint',
    'send_placeholder',
    'continue_placeholder',
    'checkpoint_note_placeholder',
    'branch_on_github',
    'new_sub_project',
    'new_child_item',
    'new_branch_item',
    'new_step',
    'link_ref_hint',
  ]) {
    assert.ok(STRINGS.en[key]?.trim(), `Missing English accessible name for ${key}`);
    assert.ok(STRINGS['zh-TW'][key]?.trim(), `Missing zh-TW accessible name for ${key}`);
  }
});

test('redacted BAT connection URL guidance names --print-connect-link and full-connect: in en and zh-TW', () => {
  for (const locale of ['en', 'zh-TW'] as const) {
    assert.ok(Object.hasOwn(STRINGS[locale], 'setup_redacted_connection_url'));
    for (const key of ['setup_redacted_connection_url', 'setup_connection_url_hint']) {
      assert.ok(STRINGS[locale][key].includes('--print-connect-link'));
      assert.ok(STRINGS[locale][key].includes('full-connect:'));
    }
    assert.doesNotMatch(STRINGS[locale].setup_connection_url_hint, /banner.*connect: line|banner’s connect:|啟動訊息的 connect:/);
  }
});
