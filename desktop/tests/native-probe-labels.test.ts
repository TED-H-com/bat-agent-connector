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
  configurationLoaded: 'setup_waiting', saveEnabled: 'setup_discover',
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

test('macOS provider selection and readiness match desktop_role_provider in both locales', () => {
  const labels = swift.match(/let providerRoleLabels\s*=\s*(\[[^\]\n]+\])/);
  assert.ok(labels, 'Trace the provider labels shared by AXPress and readiness');
  assert.deepEqual(JSON.parse(labels[1]), locales.map(locale => STRINGS[locale].desktop_role_provider));
  assert.match(swift, /labels\.contains\(where:\s*\{\s*providerRoleLabels\.contains\(\$0\)\s*\}\)/);
  assert.match(swift, /providerRoleLabels\.contains\(label\)\s*\{\s*result\["roleChoice"\]\s*=\s*true/);
});

test('Windows provider selection and readiness match desktop_role_provider in both locales', () => {
  const predicate = csharp.match(/internal static bool IsLabel\(string label\)\s*\{([^{}]+)\}/);
  assert.ok(predicate, 'Trace the provider predicate shared by InvokePattern and readiness');
  assert.deepEqual(literals(predicate[1]), locales.map(locale => STRINGS[locale].desktop_role_provider));
  assert.match(csharp, /if\s*\(!IsLabel\(read\(AutomationElement\.NameProperty\) as string\)\)/);
  assert.match(csharp, /ProviderSelection\.IsLabel\(label\)\)\s*\{\s*RoleChoice\s*=\s*true/);
});

test('Windows provider probe test labels match desktop_role_provider in both locales', () => {
  const loop = csharpTests.match(/foreach\s*\(string label in new string\[\]\s*\{([^{}]+)\}\)/);
  assert.ok(loop, 'Trace the localized provider selection test loop');
  assert.deepEqual(literals(loop[1]), locales.map(locale => STRINGS[locale].desktop_role_provider));
});

test('the actual first setup action traces the discovery-mode setup_discover branch', () => {
  const tree = parse(setup, {ecmaVersion: 'latest', sourceType: 'module'});
  const assignments: any[] = [], capability: any[] = [];
  function visit(node: any) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'AssignmentExpression' && node.left?.object?.name === 'automaticButton' &&
        node.left.property?.name === 'textContent') assignments.push(node.right);
    if (node.type === 'VariableDeclarator' && node.id?.name === 'discoverySupported') capability.push(node.init);
    for (const child of Object.values(node)) if (Array.isArray(child)) child.forEach(visit); else visit(child);
  }
  visit(tree);
  assert.equal(assignments.length, 1, 'Trace the current action text, not its static initializer');
  assert.equal(assignments[0].callee?.name, 't');
  const choice = assignments[0].arguments[0];
  assert.equal(choice.type, 'ConditionalExpression');
  assert.equal(choice.consequent.value, 'setup_discover', 'The bundled Connector first action must use discovery');
  assert.equal(choice.alternate.value, 'setup_review_connection');
  assert.equal(choice.test.type, 'LogicalExpression');
  assert.equal(choice.test.operator, '&&');
  assert.equal(choice.test.left.name, 'discoverySupported');
  assert.equal(choice.test.right.operator, '!');
  assert.equal(choice.test.right.argument.expression.object.name, 'discovery');
  assert.equal(choice.test.right.argument.expression.property.name, 'profile_id');
  assert.equal(capability.length, 1);
  assert.equal(capability[0].operator, '===');
  assert.equal(capability[0].left.expression.object.name, 'snapshot');
  assert.equal(capability[0].left.expression.property.name, 'discovery_contract_version');
  assert.equal(capability[0].right.value, 1);
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
    assert.equal(JSON.parse(absent[1]), STRINGS.en.setup_discover);
  }
});

for (const locale of locales) {
  test(`${locale} saved-connection guidance names the available first action`, () => {
    assert.ok(STRINGS[locale].setup_choose_connection.includes(STRINGS[locale].setup_discover));
  });
  test(`${locale} retires the unused automatic setup label`, () => {
    assert.equal(Object.hasOwn(STRINGS[locale], 'setup_automatic'), false);
  });
}
