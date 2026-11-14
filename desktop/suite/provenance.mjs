import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {execFileSync} from '../../scripts/hidden-child-process.mjs';

export function suiteProvenance(root, env = process.env, args = process.argv.slice(2)) {
  if (args.some(arg => arg !== '--harness') || args.length > 1)
    throw new Error('Only --harness is supported');
  const project = readFileSync(resolve(root, 'pyproject.toml'), 'utf8').match(/^\[project\]\s*\n([\s\S]*?)(?=^\[|$(?![\s\S]))/m);
  const connectorVersion = project?.[1].match(/^version\s*=\s*["']([^"']+)["']\s*$/m)?.[1];
  if (!connectorVersion) throw new Error('Cannot read project version');
  const git = gitArgs => execFileSync('git', gitArgs, {cwd: root, env, encoding: 'utf8'}).trim();
  const gitCommit = git(['status', '--porcelain', '--untracked-files=normal']) ? null : git(['rev-parse', 'HEAD']);
  return {connectorVersion, gitCommit, harness: args.includes('--harness')};
}
