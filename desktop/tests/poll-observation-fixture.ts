export const failedPolls = [
  [0, 'NETWORK_ERROR', 'transport', 'ui_reconnecting', 'reconnecting'],
  [0, 'TIMEOUT', 'transport', 'ui_reconnecting', 'reconnecting'],
  [401, 'UNAUTHORIZED', 'refused', 'ui_connection_refused', 'refused'],
  [403, 'FORBIDDEN', 'refused', 'ui_connection_refused', 'refused'],
  [503, 'UNAVAILABLE', 'service_error', 'ui_service_error', 'service_error'],
  [500, 'INTERNAL', 'service_error', 'ui_service_error', 'service_error'],
  [200, 'INVALID_EVENT_PAGE', 'invalid_response', 'ui_invalid_response', 'invalid_response'],
  [400, 'BAD_REQUEST', 'invalid_response', 'ui_invalid_response', 'invalid_response'],
  [409, 'EVENT_CURSOR_RESET', 'continuity', 'ui_connecting', 'connecting'],
] as const;
