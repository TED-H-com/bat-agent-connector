import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ApiError, errorPresentation} from '../src/error-presentation.js';

for (const locale of ['en', 'zh-TW']) {
  const descriptor = Object.getOwnPropertyDescriptor(navigator, 'language');
  Object.defineProperty(navigator, 'language', {value: locale, configurable: true});
  const {t} = await import(`../src/i18n.js?error-locale=${locale}`);
  if (descriptor) Object.defineProperty(navigator, 'language', descriptor);
  else delete (navigator as any).language;
  test(`${locale}: plain localized client Error retains its sentence without Details`, () => {
    const message = t('attachment_unsupported_image');
    assert.deepEqual(errorPresentation(new Error(message), t), {summary: message, code: '', message: ''});
  });
  test(`${locale}: known code prefers its sentence with diagnostic code`, () => {
    assert.deepEqual(errorPresentation(new ApiError(404, 'NOT_FOUND', 'Fixture server diagnostic'), t), {
      summary: t('error_code_NOT_FOUND'), code: 'NOT_FOUND', message: 'Fixture server diagnostic'});
  });
  test(`${locale}: unknown server code remains generic even when its message resembles client text`, () => {
    assert.deepEqual(errorPresentation(new ApiError(422, 'UNKNOWN_FIXTURE_CODE', t('offline_actions_paused')), t), {
      summary: t('error_generic'), code: 'UNKNOWN_FIXTURE_CODE', message: t('offline_actions_paused')});
  });
  test(`${locale}: programming TypeError stays generic, rather than network`, () => {
    assert.deepEqual(errorPresentation(new TypeError('Fixture programming failure'), t), {
      summary: t('error_generic'), code: '', message: 'Fixture programming failure'});
  });
  for (const code of ['NETWORK_ERROR', 'TIMEOUT']) test(`${locale}: ${code} uses its explicit transport code`, () => {
    assert.deepEqual(errorPresentation(new ApiError(0, code, 'Fixture transport diagnostic'), t), {
      summary: t(`error_code_${code}`), code, message: 'Fixture transport diagnostic'});
  });
  test(`${locale}: caller summary wins over known code and client sentence`, () => {
    const summary = t('setup_refused');
    assert.deepEqual(errorPresentation(new ApiError(422, 'INVALID_TARGET', t('setup_required'), {localized: true}), t, {summary}), {
      summary, code: 'INVALID_TARGET', message: t('setup_required')});
  });
  test(`${locale}: explicit client localization supports unknown API codes without guessing server messages`, () => {
    const message = t('parent_archived');
    assert.deepEqual(errorPresentation(new ApiError(409, 'ITEM_ARCHIVED', message, {localized: true}), t), {
      summary: message, code: 'ITEM_ARCHIVED', message: ''});
    assert.equal(errorPresentation(new ApiError(0, 'CENTRAL_OFFLINE', t('offline_actions_paused'), {localized: true}), t).summary,
      t('error_code_CENTRAL_OFFLINE'));
    assert.equal(errorPresentation(new ApiError(401, 'UNAUTHORIZED', t('need_token'), {localized: true}), t).summary,
      t('error_code_UNAUTHORIZED'));
  });
  test(`${locale}: duplicate summary diagnostics are omitted`, () => {
    const summary = t('error_code_NOT_FOUND');
    assert.deepEqual(errorPresentation(new ApiError(404, 'NOT_FOUND', summary), t), {summary, code: 'NOT_FOUND', message: ''});
    assert.deepEqual(errorPresentation(new Error(summary), t, {summary}), {summary, code: '', message: ''});
  });
}
