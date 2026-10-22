import {test} from 'node:test';
import assert from 'node:assert/strict';
import {boardGitHubLink} from '../src/board-github.js';
import {locale, setLocale} from '../src/i18n.js';
const external = {source: 'github', repository: 'example/board', repository_id: 1, number: 1,
  node_id: 'PR_1', key: '1#1:PR_1', url: 'https://github.com/example/board/pull/1', kind: 'pull_request',
  state: 'closed', state_reason: null, draft: true, availability: 'current', applied_lane: 'doing',
  board_lane: 'github_closed', manual: false};
for (const lang of ['en', 'zh-TW']) test(`closed draft PR retains both source markers ${lang}`, () => {
  const previous = locale;
  try {
    setLocale(lang);
    const link = boardGitHubLink(external);
    assert.ok(link);
    assert.ok(link.label.includes(lang === 'en' ? 'Closed (not completed)' : '已關閉（未完成）'), link.label);
    assert.ok(link.label.includes(lang === 'en' ? 'Draft' : '草稿'), link.label);
    assert.equal(link.url, external.url);
  } finally {setLocale(previous);}
});
