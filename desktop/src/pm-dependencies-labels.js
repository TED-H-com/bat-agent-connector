// One name per captured node across graph, matrix and list. Unrelated names and
// display filters cannot change a same-name group's stable ID ordering.
export function dependencyNodeNames(nodes) {
  const groups = new Map(), names = new Map(), seen = new Set();
  for (const {id, name} of nodes) {
    if (seen.has(id)) continue;
    seen.add(id);
    if (!groups.has(name)) groups.set(name, new Set());
    groups.get(name).add(id);
  }
  for (const [name, ids] of groups) [...ids].sort().forEach((id, index) => {
    names.set(id, name + (ids.size > 1 ? ` [${index + 1}]` : ''));
  });
  return id => names.get(id) || '';
}

// Keep separators and the original text. CJK may break between characters;
// other segments break internally only if the segment itself exceeds a line.
export function wrapDependencyName(name, width, measure) {
  const segments = String(name).match(/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]|[^\s\-_/\.\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]+[\s\-_/\.]*|[\s\-_/\.]+/gu) || [''];
  const lines = []; let line = '';
  for (const segment of segments) {
    if (line && measure(line + segment) > width) {lines.push(line); line = '';}
    if (measure(segment) > width) {
      for (const char of segment) {
        if (line && measure(line + char) > width) {lines.push(line); line = '';}
        line += char;
      }
    } else line += segment;
  }
  lines.push(line);
  return lines;
}
