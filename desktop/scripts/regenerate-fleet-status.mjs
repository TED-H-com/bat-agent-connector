// Consume only output from the typed Rust producers. Retain existing JSON key
// order for review, but every value (including new keys) comes from that output.
import {readFileSync, writeFileSync} from 'node:fs';
const [kind, log] = process.argv.slice(2);
const outputs = {
  raw: ['FLEET_RAW=', 'fleet-status.json'],
  golden: ['FLEET_GOLDEN=', 'fleet-status.golden.json'],
  stop: ['FLEET_STOP=', 'fleet-stop.json'],
  'stop-golden': ['FLEET_STOP_GOLDEN=', 'fleet-stop.golden.json'],
  requirement: ['FLEET_REQUIREMENT=', 'fleet-requirement.json'],
  'requirement-golden': ['FLEET_REQUIREMENT_GOLDEN=', 'fleet-requirement.golden.json'],
  pending: ['FLEET_PENDING=', 'fleet-pending.json'],
  'pending-golden': ['FLEET_PENDING_GOLDEN=', 'fleet-pending.golden.json'],
};
if (!Object.hasOwn(outputs, kind) || !log) throw new Error('Usage: ' + Object.keys(outputs).join('|') + ' producer.log');
const [marker, filename] = outputs[kind];
const line = readFileSync(log, 'utf8').split('\n').find(line => line.startsWith(marker));
if (!line) throw new Error(`Missing ${marker} producer output`);
const target = new URL('../tests/fixtures/' + filename, import.meta.url);
const actual = JSON.parse(line.slice(marker.length));
function order(value, prior) {
  if (Array.isArray(value)) return value.map((item, i) => order(item, prior?.[i]));
  if (value && typeof value === 'object') {
    const keys = [...new Set([...Object.keys(prior || {}), ...Object.keys(value)])].filter(key => Object.hasOwn(value, key));
    return Object.fromEntries(keys.map(key => [key, order(value[key], prior?.[key])]));
  }
  return value;
}
let prior = {};
try { prior = JSON.parse(readFileSync(target, 'utf8')); } catch (error) { if (error.code !== 'ENOENT') throw error; }
writeFileSync(target, JSON.stringify(order(actual, prior), null, 2) + '\n');
