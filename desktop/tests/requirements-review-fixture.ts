import {type Page} from '@playwright/test';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';
export function reviewFixture(locale = 'en') {
  const fixture = demoFixture({locale, managedSession: true, discussions: true});
  const overrides: ((url: URL, result: any) => any)[] = [];
  const reads: string[] = [], writes: string[] = [], events: any[] = [];
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture');
    (input.method === 'GET' ? reads : writes).push(input.path);
    if (url.pathname === '/events') {
      const after = Number(url.searchParams.get('after') || 0), fresh = events.filter(row => row.seq > after), cursor = fresh.at(-1)?.seq ?? after;
      return {status: 200, data: {events: fresh, next_cursor: cursor, head_cursor: cursor, has_more: false, sync: {checkpoint: {cursor, token: `demo-${cursor}`}}}};
    }
    let result: any;
    try {result = await fixture.dispatch(input);} catch {result = {status: 404, data: {error: {code: 'NOT_FOUND', message: 'Not found'}}};}
    for (const override of overrides) result = await override(url, result) ?? result;
    return result;
  };
  return {...fixture, reads, writes, overrides, dispatch,
    emit() {events.push({seq: events.length + 1, resource_type: 'session', resource_id: `${fixture.ids.host}/${fixture.ids.sessions.manual}`, kind: 'session.updated'});},
    async mount(page: Page, view: string, select = '') {
      await mountConversation(page, false, dispatch);
      await page.goto(`/dashboard/#/pm/${fixture.ids.workspaceId}/${view}${select ? '?select=' + encodeURIComponent(select) : ''}`);
    },
  };
}
