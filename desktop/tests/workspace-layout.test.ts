import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  calcSidebarBounds,
  DEFAULT_SIDEBAR_WIDTH,
  MIN_SIDEBAR_WIDTH,
  MAX_SIDEBAR_WIDTH,
  MIN_MAIN_WIDTH,
  KEY_SIDEBAR_WIDTH,
  KEY_MOBILE_INFO_COLLAPSED,
  KEY_MOBILE_COMPOSER_COLLAPSED
} from '../src/workspace-layout.js';

test('sidebar layout bounds respect min sidebar, max sidebar, and desktop main minimum width', () => {
  // Desktop wide display (1440px)
  const wide = calcSidebarBounds(1440);
  assert.equal(wide.minAllowed, MIN_SIDEBAR_WIDTH);
  assert.equal(wide.maxAllowed, MAX_SIDEBAR_WIDTH); // 1440 - 360 = 1080, bounded to 500

  // Standard 1024px display
  const standard = calcSidebarBounds(1024);
  assert.equal(standard.minAllowed, MIN_SIDEBAR_WIDTH);
  assert.equal(standard.maxAllowed, MAX_SIDEBAR_WIDTH); // 1024 - 360 = 664, bounded to 500

  // Constrained 700px display
  const constrained = calcSidebarBounds(700);
  assert.equal(constrained.minAllowed, MIN_SIDEBAR_WIDTH);
  assert.equal(constrained.maxAllowed, 700 - MIN_MAIN_WIDTH); // 700 - 360 = 340

  // Very narrow 500px display
  const narrow = calcSidebarBounds(500);
  assert.equal(narrow.minAllowed, MIN_SIDEBAR_WIDTH);
  assert.equal(narrow.maxAllowed, MIN_SIDEBAR_WIDTH); // 500 - 360 = 140, but bounded to min 200
});

test('sidebar layout constants and storage keys are stable', () => {
  assert.equal(DEFAULT_SIDEBAR_WIDTH, 248);
  assert.equal(MIN_SIDEBAR_WIDTH, 200);
  assert.equal(MAX_SIDEBAR_WIDTH, 500);
  assert.equal(MIN_MAIN_WIDTH, 360);
  assert.equal(KEY_SIDEBAR_WIDTH, 'batc.sidebar.width');
  assert.equal(KEY_MOBILE_INFO_COLLAPSED, 'batc.mobile.info_collapsed');
  assert.equal(KEY_MOBILE_COMPOSER_COLLAPSED, 'batc.mobile.composer_collapsed');
});
