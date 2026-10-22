import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {parse} from 'acorn';
import {STRINGS} from '../src/i18n.js';

const swift = readFileSync(new URL('./macos-window.swift', import.meta.url), 'utf8');
const csharp = readFileSync(new URL('./windows-managed-view.cs', import.meta.url), 'utf8');
const csharpTests = readFileSync(new URL('./windows-managed-view-test.cs', import.meta.url), 'utf8');
const setup = readFileSync(new URL('../src/managed-setup.js', import.meta.url), 'utf8');
const locales = ['en', 'zh-TW'] as const;
const readinessKeys = {
  authenticated: 'setup_waiting', setup: 'setup_title',
  configurationLoaded: 'setup_waiting', saveEnabled: 'setup_review_connection',
} as const;
const literals = (source: string) => [...source.matchAll(/"(?:\\.|[^"\\])*"/g)].map(match => JSON.parse(match[0]));

function swiftLabels() {
  const result: Record<string, string[]> = {};
  for (const match of swift.matchAll(/\bif\s+([^{}]+)\{\s*result\["(authenticated|setup|configurationLoaded|saveEnabled)"\]\s*=\s*true\s*\}/g)) {
    const list = match[1].match(/(\[[^\]\n]+\])\.contains\(label\)/);
    assert.ok(list, `Cannot trace Swift labels for ${match[2]}`);
    assert.equal(result[match[2]], undefined, `Duplicate Swift readiness predicate: ${match[2]}`);
    result[match[2]] = JSON.parse(list[1]);
  }
  return result;
}

function csharpLabels() {
  const result: Record<string, string[]> = {};
  for (const match of csharp.matchAll(/\bif\s*\(([^;{}]+?)\)\s*(Authenticated|Setup|ConfigurationLoaded|SaveEnabled)\s*=\s*true;/g)) {
    const key = match[2][0].toLowerCase() + match[2].slice(1);
    assert.equal(result[key], undefined, `Duplicate C# readiness predicate: ${key}`);
    result[key] = [...match[1].matchAll(/\blabel\s*==\s*("(?:\\.|[^"\\])*")/g)].map(label => JSON.parse(label[1]));
  }
  return result;
}

for (const [platform, read] of [['macOS', swiftLabels], ['Windows', csharpLabels]] as const) {
  for (const [key, translation] of Object.entries(readinessKeys)) test(`${platform} ${key} matches the current English and zh-TW UI`, () => {
    const labels = read();
    assert.deepEqual(Object.keys(labels).sort(), Object.keys(readinessKeys).sort(), 'Readiness predicates must all be traced');
    assert.deepEqual(labels[key], locales.map(locale => STRINGS[locale][translation]), `${key} must use ${translation}`);
  });
}

test('the actual first setup action uses setup_review_connection', () => {
  const tree = parse(setup, {ecmaVersion: 'latest', sourceType: 'module'});
  const buttons: any[] = [];
  function visit(node: any) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'VariableDeclarator' && node.id?.name === 'automaticButton') buttons.push(node.init);
    for (const child of Object.values(node)) if (Array.isArray(child)) child.forEach(visit); else visit(child);
  }
  visit(tree);
  assert.equal(buttons.length, 1, 'Trace the first setup button without guessing another action');
  assert.equal(buttons[0].callee?.name, 'h');
  assert.equal(buttons[0].arguments[2]?.callee?.name, 't');
  assert.equal(buttons[0].arguments[2].arguments[0]?.value, 'setup_review_connection');
});

const readinessLoop = csharpTests.match(/foreach\s*\(string\[\]\s+labels\s+in\s+new\s+string\[\]\[\]\s*\{([\s\S]*?)\}\s*\)/);
for (const [index, locale] of locales.entries()) test(`Windows readiness test labels agree with the ${locale} producer`, () => {
  assert.ok(readinessLoop, 'The localized readiness loop must be traced');
  const vectors = [...readinessLoop[1].matchAll(/new string\[\]\s*\{([^{}]+)\}/g)].map(match => literals(match[1]));
  assert.equal(vectors.length, locales.length, 'Both localized readiness vectors must be traced');
  assert.deepEqual(vectors[index], Object.values(readinessKeys).map(key => STRINGS[locale][key]));
  if (locale === 'en') {
    const absent = csharpTests.match(/Observe\(state, ControlType\.Button, ("(?:\\.|[^"\\])*"), absent\)/);
    assert.ok(absent, 'The missing-enabled-property regression must exercise the current action');
    assert.equal(JSON.parse(absent[1]), STRINGS.en.setup_review_connection);
  }
});

for (const locale of locales) {
  test(`${locale} saved-connection guidance names the available first action`, () => {
    assert.ok(STRINGS[locale].setup_choose_connection.includes(STRINGS[locale].setup_review_connection));
  });
  test(`${locale} retires the unused automatic setup label`, () => {
    assert.equal(Object.hasOwn(STRINGS[locale], 'setup_automatic'), false);
  });
}
