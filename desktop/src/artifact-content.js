import {nativeDesktop, nativeFileSupport, nativeFilesPreview, nativeFilesSave,
  nativeFilesStatus, nativeFilesControl} from "./transport/index.ts";
import {errorPresentation} from "./error-presentation.js";

const reference = row => ({artifact_id: row.artifact_id, revision: row.revision, digest: row.digest});
const same = (a, b) => a && b && a.artifact_id === b.artifact_id && a.revision === b.revision && a.digest === b.digest;
const pending = new Set(['downloading', 'saving']);
const textLimit = 256 * 1024, pngLimit = 2 * 1024 * 1024, contentLimit = 16 * 1024 * 1024;

async function browserPreview(bytes) {
  if (bytes.slice(0, 8).every((byte, i) => byte === [137,80,78,71,13,10,26,10][i]) && bytes.length >= 33) {
    if (bytes.length > pngLimit) throw new Error('PREVIEW_UNSUPPORTED');
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const width = view.getUint32(16), height = view.getUint32(20);
    if (view.getUint32(8) !== 13 || String.fromCharCode(...bytes.slice(12, 16)) !== 'IHDR' ||
        !width || !height || width * height > 1048576) throw new Error('PREVIEW_UNSUPPORTED');
    // Strip metadata before decoding; bound pixels first and refuse animation.
    const chunks = [bytes.slice(0, 8)];
    let header = false, palette = false, transparency = false, data = false, dataEnded = false, ended = false;
    for (let offset = 8; offset < bytes.length;) {
      if (offset + 12 > bytes.length) throw new Error('PREVIEW_UNSUPPORTED');
      const length = view.getUint32(offset), end = offset + length + 12;
      const kind = String.fromCharCode(...bytes.slice(offset + 4, offset + 8));
      if (end > bytes.length || !/^[A-Za-z]{2}[A-Z][A-Za-z]$/.test(kind) || kind === 'acTL') throw new Error('PREVIEW_UNSUPPORTED');
      if (kind === 'IHDR') {
        if (header || offset !== 8 || length !== 13) throw new Error('PREVIEW_UNSUPPORTED');
        header = true;
      } else if (!header) throw new Error('PREVIEW_UNSUPPORTED');
      if (kind === 'PLTE') {
        if (palette || transparency || data || !length || length > 768 || length % 3) throw new Error('PREVIEW_UNSUPPORTED');
        palette = true;
      }
      if (kind === 'tRNS') {
        if (transparency || data || bytes[25] === 3 && !palette) throw new Error('PREVIEW_UNSUPPORTED');
        transparency = true;
      }
      if (kind === 'IDAT') {
        if (dataEnded || bytes[25] === 3 && !palette) throw new Error('PREVIEW_UNSUPPORTED');
        data = true;
      } else if (data) dataEnded = true;
      if (kind === 'IEND') {
        if (!data || ended || length !== 0 || end !== bytes.length) throw new Error('PREVIEW_UNSUPPORTED');
        ended = true;
      }
      if (['IHDR', 'PLTE', 'IDAT', 'IEND', 'tRNS'].includes(kind)) chunks.push(bytes.slice(offset, end));
      else if (/^[A-Z]/.test(kind)) throw new Error('PREVIEW_UNSUPPORTED');
      offset = end;
    }
    if (!ended) throw new Error('PREVIEW_UNSUPPORTED');
    const bitmap = await createImageBitmap(new Blob(chunks, {type: 'image/png'}));
    try {
      if (bitmap.width !== width || bitmap.height !== height) throw new Error('PREVIEW_UNSUPPORTED');
      const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
      canvas.getContext('2d').drawImage(bitmap, 0, 0);
      return {canvas}; // Only decoded pixels enter the DOM; never source markup or metadata.
    } finally {bitmap.close();}
  }
  if (bytes.length > textLimit) throw new Error('PREVIEW_UNSUPPORTED');
  let text;
  try {text = new TextDecoder('utf-8', {fatal: true}).decode(bytes);} catch {throw new Error('PREVIEW_UNSUPPORTED');}
  if (text.includes('\0')) throw new Error('PREVIEW_UNSUPPORTED');
  return {text};
}

// Tab or a click elsewhere is the person's focus. Hiding the pressed control
// moves focus to the body; only that case is handed to the next control.
export function retainFocus(button) {
  if (!(button instanceof HTMLElement) || document.activeElement !== button) return () => {};
  let moved = false;
  const onKeyDown = event => {
    if (['Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) moved = true;
  };
  const onPointerDown = event => {
    const target = event.target;
    if (target !== button && !(target instanceof Node && button.contains(target))) moved = true;
  };
  document.addEventListener('keydown', onKeyDown, true);
  document.addEventListener('pointerdown', onPointerDown, true);
  return (successor, fallback, alive = true) => {
    document.removeEventListener('keydown', onKeyDown, true);
    document.removeEventListener('pointerdown', onPointerDown, true);
    if (!alive || moved) return;
    const usable = node => Boolean(node && node.isConnected && !node.closest('[hidden]') && !node.disabled);
    const target = usable(button) ? button : usable(successor) ? successor : fallback;
    if (!target || !target.isConnected) return;
    if (target === fallback) fallback.tabIndex = -1;
    if (document.activeElement !== target) target.focus();
  };
}

export function artifactContent({h, t, api, guard, getArtifact, canRead, validArtifact, readBrowser}) {
  const status = h('p', {class: 'muted', role: 'status'}), transfers = h('div'), preview = h('div', {class: 'file-preview', hidden: true});
  const box = h('div', {class: 'artifact-content'}), receipts = new Map(), controller = new AbortController();
  let current = null, busy = false, disposed = false, timer, readingStatus = null;
  const live = expected => {
    guard();
    if (disposed || !canRead() || expected && !same(expected, getArtifact())) throw new Error(t('ar_content_changed'));
  };
  const fail = error => {try {guard(); if (!disposed) status.textContent = error.message === 'PREVIEW_UNSUPPORTED' ? t('ar_content_preview_limit') : errorPresentation(error, t, {fallback: 'error_generic_short'}).summary;} catch { /* old view */ }};
  const close = () => {preview.replaceChildren(); preview.hidden = true; };
  const receiptValid = value => /^file_[0-9a-f]{32}$/.test(value?.transfer_id) && value.direction === 'download' &&
    same(value.artifact, current) && value.digest === current.digest && value.size_bytes === current.size_bytes &&
    ['downloading','saving','saved','stopped','failed','cancelled'].includes(value.stage) &&
    Number.isSafeInteger(value.transferred_bytes) && value.transferred_bytes >= 0 && value.transferred_bytes <= value.size_bytes;
  const adopt = value => {
    if (!receiptValid(value)) throw new Error(t('ar_content_changed'));
    const previous = receipts.get(value.transfer_id);
    if (previous && ['display_name','digest','size_bytes','direction'].some(key => previous[key] !== value[key]))
      throw new Error(t('ar_content_changed'));
    receipts.set(value.transfer_id, value);
  };
  async function control(receipt, action) {
    try {live(receipt.artifact); await nativeFilesControl(receipt.transfer_id, action); live(receipt.artifact); await refresh();}
    catch (error) {fail(error);}
  }
  function renderTransfers() {
    transfers.replaceChildren(...[...receipts.values()].map(row => h('div', {class: 'file-transfer'},
      h('div', {class: 'muted'}, `${t(`files_${row.stage}`)} · ${row.transferred_bytes} / ${row.size_bytes} B`),
      row.error ? h('p', {class: 'muted'}, row.error) : null,
      h('div', {class: 'actions'}, pending.has(row.stage)
        ? h('button', {class: 'secondary', onclick: () => control(row, 'stop')}, t('files_stop'))
        : [row.stage === 'stopped' || row.stage === 'failed' ? h('button', {class: 'secondary', onclick: () => control(row, 'retry')}, t('files_retry')) : null,
          h('button', {class: 'secondary', onclick: () => control(row, 'discard_local')}, t('files_discard'))]))));
  }
  async function refresh() {
    if (readingStatus) return readingStatus;
    const expected = current;
    readingStatus = (async () => {
      live(expected); const doc = await nativeFilesStatus(); live(expected);
      const found = new Set();
      for (const row of doc.transfers || []) if (row.direction === 'download' && same(row.artifact, expected)) {
        adopt(row); found.add(row.transfer_id);
      }
      for (const id of receipts.keys()) if (!found.has(id)) receipts.delete(id);
      renderTransfers();
      clearTimeout(timer);
      if ([...receipts.values()].some(row => pending.has(row.stage))) timer = setTimeout(() => refresh().catch(fail), 1000);
    })();
    try {await readingStatus;} finally {readingStatus = null;}
  }
  async function fresh() {
    live(); const expected = getArtifact();
    if (!expected || !Number.isSafeInteger(expected.size_bytes) || expected.size_bytes < 0 || expected.size_bytes > contentLimit)
      throw new Error(t('ar_content_limit'));
    const {artifact: row} = await api('GET', `/artifacts/${expected.artifact_id}/revisions/${expected.revision}`); live(expected);
    if (!validArtifact(row, reference(expected)) || row.size_bytes !== expected.size_bytes ||
        row.operation_id !== expected.operation_id || row.source?.fingerprint !== expected.source?.fingerprint)
      throw new Error(t('ar_content_changed'));
    return row;
  }
  async function perform(action) {
    const button = action === 'preview' ? peek : save;
    if (busy || button.getAttribute('aria-disabled') === 'true') return;
    const settle = retainFocus(button);
    busy = true; update(); status.textContent = t('ar_content_reading');
    try {
      const row = await fresh(), ref = reference(row);
      if (action === 'preview') {
        if (row.size_bytes > pngLimit) throw new Error('PREVIEW_UNSUPPORTED');
        let content;
        if (nativeDesktop) {
          const result = await nativeFilesPreview(ref); live(ref);
          if (result.media_type === 'text/plain' && typeof result.text === 'string' &&
              new TextEncoder().encode(result.text).length <= textLimit && !result.text.includes('\0')) content = {text: result.text};
          else if (result.media_type === 'image/png' && typeof result.base64 === 'string' && result.base64.length <= 2800000)
            content = await browserPreview(Uint8Array.from(atob(result.base64), c => c.charCodeAt(0)));
          else throw new Error(t('ar_content_changed'));
        } else content = await browserPreview(await readBrowser(ref, row.size_bytes, controller.signal));
        live(ref); close();
        let body;
        if (content.canvas) {body = content.canvas; body.setAttribute('role', 'img'); body.setAttribute('aria-label', t('files_preview'));}
        else body = h('pre', {}, content.text);
        preview.hidden = false; preview.append(h('div', {class: 'actions'}, h('strong', {}, t('files_preview')),
          h('button', {class: 'secondary', onclick: close}, t('close'))), body);
        status.textContent = t('ar_content_verified');
      } else if (nativeDesktop) {
        const receipt = await nativeFilesSave(ref); live(ref);
        status.textContent = '';
        if (receipt) {adopt(receipt); renderTransfers(); await refresh();}
      } else {
        const bytes = await readBrowser(ref, row.size_bytes, controller.signal); live(ref);
        const url = URL.createObjectURL(new Blob([bytes], {type: 'application/octet-stream'}));
        const link = document.createElement('a'); link.href = url;
        link.download = String(row.display_name || row.artifact_id).replace(/[\\/\x00-\x1f\x7f]/g, '_').slice(0, 200) || row.artifact_id;
        link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        status.textContent = t('ar_content_downloaded');
      }
    } catch (error) {fail(error);} finally {busy = false; update(); settle(null, status, !disposed);}
  }
  const peek = h('button', {class: 'secondary', onclick: () => perform('preview')}, t('files_preview'));
  const save = h('button', {class: 'secondary', onclick: () => perform('save')}, t(nativeDesktop ? 'files_save' : 'ar_content_download'));
  box.append(h('div', {class: 'actions'}, peek, save), h('p', {class: 'muted'}, t('ar_content_help')), status, preview, transfers);
  function update() {
    if (disposed) return;
    const next = getArtifact(), changed = !same(current, next);
    if (changed) {current = next; close(); receipts.clear(); renderTransfers(); clearTimeout(timer); status.textContent = '';}
    box.hidden = !next;
    const blocked = !next || !canRead() || nativeDesktop && !nativeFileSupport;
    for (const button of [peek, save]) {
      if (busy) {button.disabled = false; button.setAttribute('aria-disabled', 'true');}
      else {button.removeAttribute('aria-disabled'); button.disabled = blocked;}
    }
    if (busy) status.setAttribute('aria-busy', 'true'); else status.removeAttribute('aria-busy');
    if (next && nativeDesktop && !nativeFileSupport) status.textContent = t('ar_content_native_unavailable');
    if (changed && next && nativeDesktop && nativeFileSupport) queueMicrotask(() => refresh().catch(fail));
  }
  return {box, update, dispose() {disposed = true; controller.abort(); clearTimeout(timer); close();}};
}
