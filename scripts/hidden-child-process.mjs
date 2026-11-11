// Keep Node overloads and callbacks while enforcing Windows console suppression.
import {promisify} from 'node:util';
import * as childProcess from 'node:child_process';
function hiddenCall(name, values) {
  const args = [...values];
  const callback = typeof args.at(-1) === 'function' ? args.pop() : undefined;
  const optionsIndex = name === 'exec' || name === 'execSync' ? 1 : 2;
  if (optionsIndex === 2 && args[1] == null) args[1] = [];
  else if (optionsIndex === 2 && !Array.isArray(args[1])) args.splice(1, 0, []);
  args[optionsIndex] = {...args[optionsIndex], windowsHide: true};
  if (callback) args.push(callback);
  return childProcess[name](...args);
}
export const spawn = (...args) => hiddenCall('spawn', args);
export const spawnSync = (...args) => hiddenCall('spawnSync', args);
export const exec = (...args) => hiddenCall('exec', args);
export const execSync = (...args) => hiddenCall('execSync', args);
export const execFile = (...args) => hiddenCall('execFile', args);
export const execFileSync = (...args) => hiddenCall('execFileSync', args);
export const fork = (...args) => hiddenCall('fork', args);

// Node's native custom promisifier returns both streams, not just stdout.
for (const [name, fn] of [['exec', exec], ['execFile', execFile]]) {
  fn[promisify.custom] = (...args) => {
    let child;
    const promise = new Promise((resolve, reject) => {
      child = fn(...args, (error, stdout, stderr) => {
        if (error) { error.stdout = stdout; error.stderr = stderr; reject(error); }
        else resolve({stdout, stderr});
      });
    });
    promise.child = child;
    return promise;
  };
}
