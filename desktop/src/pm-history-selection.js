// Textareas normalize CRLF and CR to LF and expose UTF-16 offsets. The central
// reference uses offsets in the original, unnormalized Unicode codepoints.
export function historyTextSelection(text, partIndex, start, end) {
  if (typeof text !== 'string' || /[\uD800-\uDFFF]/u.test(text)
    || ![partIndex, start, end].every(Number.isSafeInteger)
    || partIndex < 0 || partIndex >= 128 || start < 0 || start >= end) return null;
  const points = [...text], boundaries = new Map([[0, 0]]);
  if (points.length > 16000) return null;
  let offset = 0;
  for (let index = 0; index < points.length; index++) {
    const point = points[index];
    if (point === '\r' && points[index + 1] === '\n') index++;
    offset += point.length;
    boundaries.set(offset, index + 1);
  }
  if (!boundaries.has(start) || !boundaries.has(end)) return null;
  return {kind:'text', part_index:partIndex, start_codepoint:boundaries.get(start), end_codepoint:boundaries.get(end)};
}

// Chromium's readonly textarea allows Select All but does not move its caret
// with arrow keys. Keep the native readonly field and supply range navigation.
export function navigateHistoryText(input, event) {
  if (input.disabled || event.isComposing || event.altKey
    || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
  const {value, selectionStart:start, selectionEnd:end, selectionDirection:direction} = input;
  const anchor = direction === 'backward' ? end : start, focus = direction === 'backward' ? start : end;
  const modified = event.ctrlKey || event.metaKey;
  let next;
  if (event.key === 'Home') next = modified || focus === 0 ? 0 : value.lastIndexOf('\n', focus - 1) + 1;
  else if (event.key === 'End') {const newline = value.indexOf('\n', focus); next = modified || newline < 0 ? value.length : newline;}
  else {
    if (modified) return; // Retain the platform's word-navigation shortcuts.
    const right = event.key === 'ArrowRight';
    const segments = typeof Intl.Segmenter === 'function'
      ? [...new Intl.Segmenter(undefined, {granularity:'grapheme'}).segment(value)].map(row=>row.segment) : [...value];
    let offset = 0;
    const boundaries = [0, ...segments.map(segment => (offset += segment.length))];
    next = !event.shiftKey && start !== end ? (right ? end : start)
      : right ? (boundaries.find(value=>value > focus) ?? value.length) : (boundaries.filter(value=>value < focus).at(-1) ?? 0);
  }
  event.preventDefault();
  if (!event.shiftKey) input.setSelectionRange(next, next);
  else input.setSelectionRange(Math.min(anchor, next), Math.max(anchor, next), next < anchor ? 'backward' : 'forward');
}

const keys = (value, names) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join(',') === [...names].sort().join(',');
export function historySelectionKey(selection) {
  if (keys(selection, ['kind']) && selection.kind === 'entry') return 'entry';
  if (!keys(selection, ['kind','part_index','start_codepoint','end_codepoint']) || selection.kind !== 'text'
    || ![selection.part_index, selection.start_codepoint, selection.end_codepoint].every(Number.isSafeInteger)
    || selection.part_index < 0 || selection.part_index >= 128 || selection.start_codepoint < 0
    || selection.start_codepoint >= selection.end_codepoint || selection.end_codepoint > 16000) return null;
  return JSON.stringify([selection.part_index, selection.start_codepoint, selection.end_codepoint]);
}

export function historyReferenceKey(ref) {
  const fields = ['schema','server_id','workspace_id','source_id','entry_id','source_digest','content_digest','selection','excerpt_digest'];
  const selection = historySelectionKey(ref?.selection);
  if (!keys(ref, fields) || ref.schema !== 'external_history_ref/v1' || !selection
    || !fields.filter(key => key !== 'selection').every(key => typeof ref[key] === 'string')
    || !['source_digest','content_digest','excerpt_digest'].every(key => /^[0-9a-f]{64}$/.test(ref[key]))) return null;
  return JSON.stringify([...fields.filter(key => key !== 'selection').map(key => ref[key]), selection]);
}

// Compare the server preview with the exact retained entry/range, without
// computing or replacing any of the server's canonical digests.
export function matchesHistoryContent(content, entry, selection) {
  if (!historySelectionKey(selection) || !content || content.role !== entry.role
    || content.clipped !== entry.clipped || content.omitted_parts !== entry.omitted_parts
    || !['user','assistant','tool'].includes(content.role) || typeof content.clipped !== 'boolean'
    || !Number.isSafeInteger(content.omitted_parts) || content.omitted_parts < 0
    || !Array.isArray(entry.parts) || entry.parts.length > 128 || !Array.isArray(content.parts)) return false;
  let length = 0;
  for (const part of entry.parts) {
    if (part?.kind === 'text' && typeof part.text === 'string' && !/[\uD800-\uDFFF]/u.test(part.text)) length += [...part.text].length;
    else if (!['tool_request','tool_result'].includes(part?.kind) || part.name !== null && typeof part.name !== 'string') return false;
  }
  if (length > 16000) return false;
  let parts = entry.parts;
  if (selection.kind === 'text') {
    const part = parts[selection.part_index];
    if (part?.kind !== 'text' || selection.end_codepoint > [...part.text].length) return false;
    parts = [{kind:'text', text:[...part.text].slice(selection.start_codepoint, selection.end_codepoint).join('')}];
  }
  return content.parts.length === parts.length && parts.every((part, index) => {
    const other = content.parts[index];
    return keys(other, Object.keys(part)) && Object.keys(part).every(key => part[key] === other[key]);
  });
}
