import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
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
