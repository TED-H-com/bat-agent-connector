// Only client-created messages may become a summary without a known error code.
// Server diagnostics and programming errors remain behind Details.
export class ApiError extends Error {
  constructor(status, code, message, {localized = false} = {}) {
    super(message || code); this.status = status; this.code = code; this.localized = localized;
  }
}
export function errorPresentation(error, t, {summary} = {}) {
  const code = error?.code || '';
  const message = error?.message || String(error);
  const key = `error_code_${code}`;
  const known = code && t(key) !== key;
  const client = error?.localized === true || (!code && error?.constructor === Error);
  const text = summary ?? (known ? t(key) : client ? message : t('error_generic'));
  return {summary: text, code: code === text ? '' : code, message: message === text ? '' : message};
}
