export interface ConnectorEvent { seq: number; kind: string; resource_type?: string; resource_id?: string }
export interface EventPage { events: ConnectorEvent[]; head_cursor: number; next_cursor?: number;
  has_more: boolean; reset_required?: boolean; reset?: boolean }

// Commit only handled events, never head_cursor: a bounded page may have more events after it.
export function consumePage(page: EventPage, cursor: number, emit: (event: ConnectorEvent) => void): number {
  if (page.reset_required || page.reset || page.head_cursor < cursor) {
    emit({ seq: 0, kind: "reset", resource_type: "reset" });
    cursor = 0;
  }
  for (const event of page.events) {
    if (event.seq <= cursor) continue;
    emit(event);
    cursor = event.seq;
  }
  if (Number.isSafeInteger(page.next_cursor) && page.next_cursor! >= cursor && page.next_cursor! <= page.head_cursor)
    cursor = page.next_cursor!;
  return cursor;
}

// A page is one acknowledgment unit. Coalesce view invalidations while every returned
// refresh promise remains outstanding; a rejected refresh leaves the caller's cursor unchanged.
export async function consumePageAsync(page: EventPage, cursor: number,
  emit: (event: ConnectorEvent) => void | Promise<unknown>): Promise<number> {
  if (page.reset_required || page.reset || page.head_cursor < cursor) {
    await emit({seq: 0, kind: "reset", resource_type: "reset"});
    cursor = 0;
  }
  const pending: Promise<unknown>[] = [];
  const next = consumePage({...page, reset: false, reset_required: false}, cursor,
    event => { pending.push(Promise.resolve().then(() => emit(event))); });
  await settleRefreshes(pending);
  return next;
}

export async function settleRefreshes(pending: Promise<unknown>[]): Promise<void> {
  // Finish sibling reads even when one fails, so a late renderer cannot overwrite
  // evidence after a later retry has already acknowledged the page.
  const results = await Promise.allSettled(pending);
  for (const result of results) if (result.status === "rejected") throw result.reason;
}

// Browser/external binding and legacy format. Native managed storage uses the
// verified journal identity in storage-identity.ts, independent of runtime port.
export function storageScope(endpoint: string, actor: string, server = "legacy", principal = actor): string {
  return [endpoint, server, principal, actor].map(encodeURIComponent).join(":");
}
