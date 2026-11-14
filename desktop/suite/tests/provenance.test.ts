// @ts-nocheck
import assert from 'node:assert/strict';
import {mkdirSync, mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {test} from 'node:test';
import {execFileSync} from '../../../scripts/hidden-child-process.mjs';
import {suiteProvenance} from '../provenance.mjs';

function withRepo(run, {pyproject = '[project]\nname = "bac"\nversion = "1.2.3"\n', globalConfig = ''} = {}) {
  const tempRoot = mkdtempSync(join(process.env.TMPDIR || tmpdir(), 'suite-provenance-'));
  try {
    const globalConfigPath = join(tempRoot, 'gitconfig');
    writeFileSync(globalConfigPath, globalConfig);
    const repo = join(tempRoot, 'repo');
    mkdirSync(repo);
    const env = {
      ...process.env,
      GIT_CONFIG_GLOBAL: globalConfigPath,
      GIT_CONFIG_NOSYSTEM: '1',
      GIT_AUTHOR_NAME: 'Test Author',
      GIT_AUTHOR_EMAIL: 'author@example.com',
      GIT_COMMITTER_NAME: 'Test Committer',
      GIT_COMMITTER_EMAIL: 'committer@example.com',
    };
    const git = args => execFileSync('git', args, {cwd: repo, env, encoding: 'utf8'}).trim();
    git(['init', '-q']);
    writeFileSync(join(repo, '.gitignore'), 'ignored.txt\n');
    writeFileSync(join(repo, 'pyproject.toml'), pyproject);
    git(['add', '.gitignore', 'pyproject.toml']);
    git(['commit', '-q', '-m', 'Initial commit']);
    const head = git(['rev-parse', 'HEAD']);
    return run({repo, env, head, git});
  } finally {
    rmSync(tempRoot, {recursive: true, force: true});
  }
}

test('provenance records HEAD for clean or ignored-only trees and null for dirty trees', () => {
  withRepo(({repo, env, head}) => {
    assert.deepEqual(suiteProvenance(repo, env, []), {connectorVersion: '1.2.3', gitCommit: head, harness: false});
    assert.deepEqual(suiteProvenance(repo, env, ['--harness']), {connectorVersion: '1.2.3', gitCommit: head, harness: true});
    writeFileSync(join(repo, 'ignored.txt'), 'ignored\n');
    assert.equal(suiteProvenance(repo, env, []).gitCommit, head);
  });
  withRepo(({repo, env}) => {
    writeFileSync(join(repo, 'untracked.txt'), 'untracked\n');
    assert.equal(suiteProvenance(repo, env, []).gitCommit, null);
  });
  withRepo(({repo, env}) => {
    writeFileSync(join(repo, 'pyproject.toml'), '[project]\nname = "bac"\nversion = "1.2.4"\n');
    assert.deepEqual(suiteProvenance(repo, env, []), {connectorVersion: '1.2.4', gitCommit: null, harness: false});
  });
});

test('provenance detects untracked files even when status.showUntrackedFiles is no', () => {
  withRepo(({repo, env, git}) => {
    writeFileSync(join(repo, 'untracked.txt'), 'untracked\n');
    assert.equal(git(['status', '--porcelain']), '');
    assert.equal(suiteProvenance(repo, env, []).gitCommit, null);
  }, {globalConfig: '[status]\n\tshowUntrackedFiles = no\n'});
});

test('provenance reads [project].version and refuses missing version or unknown arguments', () => {
  withRepo(({repo, env, head}) => {
    assert.deepEqual(suiteProvenance(repo, env, []), {connectorVersion: '2.3.4', gitCommit: head, harness: false});
  }, {pyproject: '[tool.other]\nversion = "0.0.1"\n\n[project]\nname = "bac"\nversion = "2.3.4"\n\n[tool.after]\nversion = "9.9.9"\n'});
  for (const pyproject of [
    '[tool.other]\nversion = "0.0.1"\n\n[project]\nname = "bac"\n',
    '[tool.other]\nversion = "0.0.1"\n',
  ]) {
    withRepo(({repo, env}) => {
      assert.throws(() => suiteProvenance(repo, env, []), /Cannot read project version/);
    }, {pyproject});
  }
  withRepo(({repo, env}) => {
    assert.throws(() => suiteProvenance(repo, env, ['--unknown']), /Only --harness is supported/);
    assert.throws(() => suiteProvenance(repo, env, ['--harness', '--extra']), /Only --harness is supported/);
  });
});
