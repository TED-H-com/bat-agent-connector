// The serialized files.rs Receipt shared by picker and clipboard IPC fixtures.
export const selectedReceipt = (draftId: string, overrides: Record<string, unknown> = {}) => ({
  transfer_id: 'file_' + '1'.repeat(32), direction: 'upload', draft_id: draftId,
  display_name: 'pasted-image-20261022T120000Z.png', size_bytes: 1024, digest: 'c'.repeat(64),
  media_type: 'image/png', stage: 'selected', transferred_bytes: 0, error: null,
  operation_id: null, operation_status: null, artifact: null,
  intent_key: '11111111-1111-4111-8111-111111111111', ...overrides,
});
