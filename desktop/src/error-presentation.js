import {clientActionCode, clientActionReason, clientActionMessageKey} from './central-service-status.js';

// Only client-created messages may become a summary without a known error code.
// Server diagnostics and programming errors remain behind Details.
export class ApiError extends Error {
  constructor(status, code, message, {localized = false} = {}) {
    super(message || code); this.status = status; this.code = code; this.localized = localized;
  }
}
export const clientActionError = (reason, t, status = 0) => new ApiError(status, clientActionCode(reason),
  t(clientActionMessageKey(reason)), {localized: true});

const SESSION_ERROR_LINES = {SESSION_BUSY: 'session_err_busy', PENDING_CHANGED: 'session_err_changed', NOT_FOUND: 'session_err_gone',
  PAYLOAD_TOO_LARGE: 'session_err_too_large', INTERNAL: 'session_err_server', FORBIDDEN: 'session_err_forbidden'};
export function sessionErrorLine(error, t) {
  const code = typeof error?.code === 'string' ? error.code : '';
  const clientReason = clientActionReason(code);
  if (clientReason) return t(clientActionMessageKey(clientReason));
  if (Object.hasOwn(SESSION_ERROR_LINES, code)) return t(SESSION_ERROR_LINES[code]);
  if (error?.status === 403) return t('session_err_forbidden');
  if (error?.status === 404 || error?.status === 410) return t('session_err_gone');
  if (error?.status === 413) return t('session_err_too_large');
  if (error?.status >= 500) return t('session_err_server');
  if (error?.status === 0 && error?.code === 'CENTRAL_OFFLINE') return t('offline_actions_paused');
  return t('session_err_unknown');
}
export function errorPresentation(error, t, {summary, fallback} = {}) {
  const code = error?.code || '';
  const message = error?.message || String(error);
  const key = `error_code_${code}`;
  const known = code && t(key) !== key;
  const client = error?.localized === true || (!code && error?.constructor === Error);
  const text = summary ?? (known ? t(key) : client ? message : t(fallback || 'error_generic'));
  return {summary: text, code: code === text ? '' : code, message: message === text ? '' : message};
}
