import assert from 'node:assert/strict';
import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import {stripTypeScriptTypes} from 'node:module';
import {parse} from 'acorn';

const desktop = fileURLToPath(new URL('..', import.meta.url));
const read = (path: string) => readFileSync(join(desktop, path), 'utf8');

function invokedCommands(directory: string, commands = new Set<string>()) {
  for (const entry of readdirSync(directory, {withFileTypes: true})) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) invokedCommands(path, commands);
    else if (/\.[cm]?[jt]sx?$/.test(entry.name)) {
      const text = readFileSync(path, 'utf8');
      const source = parse(/\.tsx?$/.test(entry.name) ? stripTypeScriptTypes(text) : text,
        {ecmaVersion: 'latest', sourceType: 'module'});
      const invokeNames = new Set(['invoke']);
      for (const statement of source.body as any[]) {
        if (statement.type !== 'ImportDeclaration') continue;
        for (const binding of statement.specifiers) {
          if (binding.imported?.name === 'invoke') invokeNames.add(binding.local.name);
        }
      }
      function visit(node: any) {
        if (!node || typeof node !== 'object') return;
        if (node.type === 'CallExpression' && (
          node.callee.type === 'Identifier' && invokeNames.has(node.callee.name) ||
          node.callee.type === 'MemberExpression' && node.callee.property.name === 'invoke')) {
          const command = node.arguments[0];
          assert.equal(typeof command?.value, 'string', `Nonliteral native invoke in ${path}`);
          commands.add(command.value);
        }
        for (const value of Object.values(node)) {
          if (Array.isArray(value)) value.forEach(visit);
          else if (value && typeof value === 'object') visit(value);
        }
      }
      visit(source);
    }
  }
  return commands;
}

function rustList(source: string, pattern: RegExp, quoted = false) {
  const match = source.match(pattern);
  assert.ok(match, 'Native command list must remain readable by the ACL guard');
  return new Set(quoted ? [...match[1].matchAll(/"([a-z_]+)"/g)].map(value => value[1]) :
    match[1].split(',').map(value => value.trim()).filter(Boolean));
}

test('frontend commands agree with the packaged main-window command ACL', t => {
  const invoked = invokedCommands(join(desktop, 'src'));
  const registered = rustList(read('src-tauri/src/main.rs'), /generate_handler!\s*\[([^\]]+)\]/);
  const manifest = rustList(read('src-tauri/build.rs'), /\.commands\s*\(\s*&\[([^\]]+)\]/, true);
  const capability = JSON.parse(read('src-tauri/capabilities/dashboard.json'));
  assert.deepEqual(capability.windows, ['main']);
  const allowed = new Set<string>(capability.permissions.map((permission: string) => {
    assert.match(permission, /^allow-[a-z]+(?:-[a-z]+)*$/, 'No wildcard or default permission set');
    return permission.slice('allow-'.length).replaceAll('-', '_');
  }));
  const sets = {invoked, registered, manifest, allowed};
  t.diagnostic(JSON.stringify(Object.fromEntries(Object.entries(sets).map(([name, commands]) =>
    [name, [...commands].sort()]))));
  const failures: string[] = [];
  for (const command of new Set([...invoked, ...registered, ...manifest, ...allowed])) {
    for (const [name, commands] of Object.entries(sets)) {
      if (!commands.has(command)) failures.push(`${command}: missing from ${name}`);
    }
  }
  assert.deepEqual(failures.sort(), [], failures.join('\n'));
});
