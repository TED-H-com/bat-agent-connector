// Native codes are diagnostic details, never user-facing prose.
export function managedRecoveryKey(code) {
  if (!code) return null;
  if (code === 'MANAGED_RECOVERING') return 'managed_service_recovering';
  if (code === 'MANAGED_RECOVERY_IDENTITY_CHANGED') return 'managed_service_identity';
  return 'managed_service_retry';
}
