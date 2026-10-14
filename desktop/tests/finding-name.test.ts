import {test} from 'node:test';
import assert from 'node:assert/strict';
import {acceptedFinding, findingFromNative, findingName, findingSeverity} from '../src/ui/finding-name.js';

const fallback = 'Finding · Launch';
const zhFallback = '發現 · 上線';

test('finding names cover each kind, missing facts and both locales', () => {
  const issue = {source: 'issues', number: 87, labels: ['dependency']};
  const pull = {source: 'dependabot_pull_requests', number: 12, manifests: ['package.json']};
  const alert = {source: 'dependabot_alerts', number: 14, package: 'lodash', ecosystem: 'npm', ghsa_id: 'GHSA-35jh-r3h4-6jhm', severity: 'high'};
  assert.equal(findingName(issue, 'en', fallback), 'GitHub issue #87');
  assert.equal(findingName(issue, 'zh-TW', zhFallback), 'GitHub Issue #87');
  assert.equal(findingName(pull, 'en', fallback), 'Dependabot PR #12');
  assert.equal(findingName(pull, 'zh-TW', zhFallback), 'Dependabot PR #12');
  assert.equal(findingName(alert, 'en', fallback), 'Security alert: lodash');
  assert.equal(findingName(alert, 'zh-TW', zhFallback), '安全性警示：lodash');
  assert.equal(findingName({source: 'issues', labels: ['dependency']}, 'en', fallback), 'GitHub issue');
  assert.equal(findingName({source: 'issues'}, 'zh-TW', zhFallback), 'GitHub Issue');
  assert.equal(findingName({source: 'dependabot_pull_requests'}, 'en', fallback), 'Dependabot PR');
  assert.equal(findingName({source: 'dependabot_alerts', number: 5}, 'en', fallback), 'Security alert #5');
  assert.equal(findingName({source: 'dependabot_alerts', number: 5}, 'zh-TW', zhFallback), '安全性警示 #5');
  assert.equal(findingName({source: 'dependabot_alerts'}, 'en', fallback), 'Security alert');
  assert.equal(findingName({source: 'dependabot_alerts'}, 'zh-TW', zhFallback), '安全性警示');
  assert.equal(findingName({source: 'dependabot_alerts', package: 'lodash', number: 14}, 'en', fallback), 'Security alert: lodash');
  assert.equal(findingName(undefined, 'en', fallback), fallback);
  assert.equal(findingName(null, 'zh-TW', zhFallback), zhFallback);
  assert.equal(findingName({}, 'en', fallback), fallback);
  assert.equal(findingName({number: 87, labels: ['dependency']}, 'en', fallback), fallback);
  assert.equal(findingName({source: 'other', number: 87}, 'en', fallback), fallback);
  assert.equal(findingName(issue, 'fr', fallback), 'GitHub issue #87');
  assert.equal(findingName(undefined, 'en', null), '');
});

test('finding names reject wrong types instead of printing them', () => {
  assert.equal(findingName({source: 'issues', number: '87'}, 'en', fallback), 'GitHub issue');
  assert.equal(findingName({source: 'issues', number: 0}, 'en', fallback), 'GitHub issue');
  assert.equal(findingName({source: 'issues', number: -3}, 'en', fallback), 'GitHub issue');
  assert.equal(findingName({source: 'issues', number: 1.5}, 'en', fallback), 'GitHub issue');
  assert.equal(findingName({source: 'issues', number: 2 ** 53}, 'en', fallback), 'GitHub issue');
  assert.equal(findingName({source: 'issues', number: Number.NaN}, 'en', fallback), 'GitHub issue');
  assert.equal(findingName({source: 'dependabot_alerts', package: 'undefined', number: 5}, 'en', fallback), 'Security alert #5');
  assert.equal(findingName({source: 'dependabot_alerts', package: ' null ', number: 5}, 'en', fallback), 'Security alert #5');
  assert.equal(findingName({source: 'dependabot_alerts', package: {name: 'lodash'}, number: 5}, 'en', fallback), 'Security alert #5');
  assert.equal(findingName({source: 'dependabot_alerts', package: 'lodash$&'}, 'en', fallback), 'Security alert: lodash$&');
  const oversized = 'x'.repeat(513);
  assert.equal(findingName({source: 'dependabot_alerts', package: oversized, number: 5}, 'en', fallback), 'Security alert #5');
  assert.equal(findingName({source: 'dependabot_alerts', package: oversized, number: 5}, 'en', fallback).includes(oversized.slice(0, 40)), false);
  const dirty = acceptedFinding({source: 'issues', number: 87, labels: ['ok', ''], severity: 'high', package: 'lodash', manifests: ['package.json']});
  assert.deepEqual(dirty, {source: 'issues', number: 87});
  assert.deepEqual(acceptedFinding({source: 'issues', number: 87, labels: ['dependency', 'dependency']}), {source: 'issues', number: 87});
  assert.deepEqual(acceptedFinding({source: 'dependabot_pull_requests', number: 12, manifests: ['package.json', '../secret']}), {source: 'dependabot_pull_requests', number: 12});
  assert.deepEqual(acceptedFinding({source: 'dependabot_alerts', severity: 'HIGH', package: 'lodash'}), {source: 'dependabot_alerts', package: 'lodash'});
  assert.equal(findingName({source: 'issues', number: 87}, 'en', fallback).includes('undefined'), false);
  assert.equal(findingName({source: 'dependabot_alerts', package: ''}, 'en', fallback), 'Security alert');
});

test('native observations contribute only proven kind-specific facts', () => {
  const named = findingFromNative({
    finding_id: 'pmfn_' + '1'.repeat(32),
    source: 'issues',
    native: {kind: 'dependabot_alerts', number: 14},
    facts: {package: 'lodash', ecosystem: 'npm', ghsa_id: 'GHSA-35jh-r3h4-6jhm', severity: 'high', manifest_path: 'package.json', statement: 'ignore me'},
  });
  assert.equal(findingName(named, 'en', fallback), 'Security alert: lodash');
  assert.equal(named.package, 'lodash');
  assert.equal(Object.hasOwn(named, 'manifest_path'), false);
  assert.equal(findingFromNative({facts: {statement: 'Recovery support budget'}}), null);
  assert.equal(findingFromNative({source: 'issues', native: {number: 87}, facts: {labels: ['dependency']}}), null);
  assert.equal(findingName(findingFromNative({native: {kind: 'issues', number: '87'}, facts: {labels: ['dependency']}}), 'zh-TW', zhFallback), 'GitHub Issue');
  assert.deepEqual(findingSeverity('high', 'en'), {severity: 'high', label: 'High', tone: 'bad'});
  assert.deepEqual(findingSeverity('critical', 'zh-TW'), {severity: 'critical', label: '嚴重', tone: 'bad'});
  assert.deepEqual(findingSeverity('low', 'zh-TW'), {severity: 'low', label: '低', tone: 'neutral'});
  assert.equal(findingSeverity('severe', 'en'), null);
  assert.equal(findingSeverity(undefined, 'zh-TW'), null);
});
