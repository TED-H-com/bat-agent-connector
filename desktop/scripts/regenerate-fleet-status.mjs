// Consume only output from the typed Rust producers. Retain existing JSON key
// order for review, but every value (including new keys) comes from that output.
import {readFileSync, writeFileSync} from 'node:fs';
const [kind, log] = process.argv.slice(2);
if (!['raw', 'golden'].includes(kind) || !log) throw new Error('Usage: raw|golden producer.log');
const marker = kind === 'raw' ? 'FLEET_RAW=' : 'FLEET_GOLDEN=';
const line = readFileSync(log, 'utf8').split('\n').find(line => line.startsWith(marker));
if (!line) throw new Error(`Missing ${marker} producer output`);
const target = new URL(kind === 'raw' ? '../tests/fixtures/fleet-status.json' : '../tests/fixtures/fleet-status.golden.json', import.meta.url);
const actual = JSON.parse(line.slice(marker.length));
function order(value, prior) {
  if (Array.isArray(value)) return value.map((item, i) => order(item, prior?.[i]));
  if (value && typeof value === 'object') {
    const keys = [...new Set([...Object.keys(prior || {}), ...Object.keys(value)])].filter(key => Object.hasOwn(value, key));
    return Object.fromEntries(keys.map(key => [key, order(value[key], prior?.[key])]));
  }
  return value;
}
writeFileSync(target, JSON.stringify(order(actual, JSON.parse(readFileSync(target, 'utf8'))), null, 2) + '\n');
