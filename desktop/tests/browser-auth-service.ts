import {expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {once} from 'node:events';

export async function browserAuthService() {
  const child = spawn('../.venv/bin/python', ['tests/browser-auth-fixture.py'], {stdio: ['pipe', 'pipe', 'pipe']});
  const exited = once(child, 'exit');
  const lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
  const next = async () => {
    const line = await lines.next();
    if (line.done) throw new Error('Auth fixture stopped before replying');
    return JSON.parse(line.value);
  };
  const control = async (command: unknown) => {
    child.stdin.write(JSON.stringify(command) + '\n');
    return next();
  };
  const identity = await next();
  return {...identity, control, async status(status: number) {
    expect((await control({status})).updated).toBe(true);
  }, async close() {
    child.stdin.end();
    const [code] = await exited; expect(code).toBe(0);
  }};
}
