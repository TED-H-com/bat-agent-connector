// Native paths, tokens, source bytes and credential bindings never enter this module.
import {nativeFilesPick, nativeFilesStatus, nativeFilesUpload, nativeFilesControl,
  nativeFilesSave, nativeFilesPreview, nativeFilesDropTarget} from "./transport/index.ts";
import {clientActionError} from './error-presentation.js';

const pending = new Set(["checking", "uploading", "verifying", "downloading", "saving"]);
const fixedRef = ref => ({artifact_id: ref.artifact_id, revision: ref.revision, digest: ref.digest});
const sameRef = (a, b) => a && b && a.artifact_id === b.artifact_id && a.revision === b.revision && a.digest === b.digest;
export function nativeAttachments({h, t, guard, canWrite, draftId, onReceipt, onDiscard, onVisibility, attached}) {
  const rows = h("div", {class: "native-transfers"}), notice = h("p", {class: "muted", role: "status"});
  const preview = h("div", {class: "file-preview", hidden: true});
  const box = h("div", {class: "native-files"}, rows, notice, preview);
  const known = new Map(), downloads = new Set();
  let armed = false, timer, refreshing, picking = false, objectUrl = null, stopped = false;
  const live = () => {guard(); if (!box.isConnected) throw new Error("Attachment form changed");};
  const failure = error => {try {live(); notice.textContent = String(error?.message || error);} catch { /* old view */ }};
  const writable = () => {live(); const gate = canWrite(); if (!gate.allowed) throw clientActionError(gate.reason, t);};
  const act = async (receipt, action) => {
    try {
      live(); if (["retry", "cancel_upload"].includes(action) && receipt.direction === "upload") writable();
      await nativeFilesControl(receipt.transfer_id, action); live();
      if (action === "discard_local") onDiscard(receipt.transfer_id);
      await refresh();
    } catch (error) {failure(error);}
  };
  const render = () => {
    rows.replaceChildren(...[...known.values()].filter(r => r.direction === "download" || !attached(r.transfer_id)).map(r => {
      const active = pending.has(r.stage), percent = r.size_bytes ? Math.min(100, Math.floor(r.transferred_bytes / r.size_bytes * 100)) : 0;
      return h("div", {class: "file-transfer"}, h("div", {class: "row"}, h("div", {class: "grow"}, r.display_name,
        h("div", {class: "muted"}, `${t(`files_${r.stage}`)} · ${r.transferred_bytes} / ${r.size_bytes} B`)),
        r.operation_id ? h("a", {href: `#/op/${r.operation_id}`}, t("dep_operation_details")) : null),
        active ? h("progress", {max: 100, value: percent, "aria-label": t("files_progress")}) : null,
        r.error ? h("p", {class: "muted"}, r.error) : null,
        h("div", {class: "actions"},
          active ? h("button", {class: "secondary", onclick: () => act(r, "stop")}, t("files_stop")) : null,
          !active && !["ready", "saved", "failed", "cancelled"].includes(r.stage) ? h("button", {class: "secondary", disabled: r.direction === "upload" && !canWrite().allowed, onclick: () => act(r, "retry")}, t(r.stage === "selected" ? "files_upload" : "files_retry")) : null,
          !active && r.direction === "upload" && r.stage !== "selected" && !["ready", "failed", "cancelled"].includes(r.stage) ? h("button", {class: "secondary", onclick: () => act(r, "check")}, t("files_check")) : null,
          !active && r.operation_id && !["succeeded", "failed", "cancelled"].includes(r.operation_status) ? h("button", {class: "secondary", disabled: !canWrite().allowed, onclick: () => act(r, "cancel_upload")}, t("files_cancel")) : null,
          !active && (r.direction === "download" || r.stage === "selected" || ["ready", "failed", "cancelled"].includes(r.stage)) ? h("button", {class: "secondary", onclick: async () => {
            await act(r, "discard_local");
          }}, t("files_discard")) : null));
    }));
  };
  const adopt = r => {
    if (!/^file_[0-9a-f]{32}$/.test(r?.transfer_id) || !/^[0-9a-f]{64}$/.test(r.digest) ||
      !Number.isSafeInteger(r.size_bytes) || r.size_bytes < 0 || r.size_bytes > 16 * 1024 * 1024 ||
      typeof r.display_name !== "string") throw new Error(t("files_receipt_mismatch"));
    const previous = known.get(r.transfer_id);
    if (previous && ["intent_key", "direction", "draft_id", "display_name", "size_bytes", "digest"].some(k => previous[k] !== r[k]))
      throw new Error(t("files_receipt_mismatch"));
    if (r.direction === "upload" && r.draft_id !== draftId) return;
    if (r.stage === "ready" && (r.operation_status !== "succeeded" || !r.operation_id ||
      r.artifact?.digest !== r.digest || !/^art_[0-9a-f]{32}$/.test(r.artifact?.artifact_id) || !Number.isSafeInteger(r.artifact?.revision)))
      throw new Error(t("files_receipt_mismatch"));
    known.set(r.transfer_id, r);
    if (r.direction === "upload" && JSON.stringify(previous) !== JSON.stringify(r)) onReceipt(r);
  };
  const refresh = async () => {
    if (refreshing) return refreshing;
    const task = (async () => {
      live(); const status = await nativeFilesStatus(); live();
      const previousKeys = [...known.keys()].join(","), current = new Set();
      for (const receipt of status.transfers) {
        if (receipt.direction === "upload" && receipt.draft_id === draftId || receipt.direction === "download" && (receipt.stage !== "saved" || downloads.has(receipt.transfer_id))) {
          current.add(receipt.transfer_id); adopt(receipt);
        }
      }
      for (const id of known.keys()) if (!current.has(id)) known.delete(id);
      if ([...known.keys()].join(",") !== previousKeys) onVisibility();
      if (status.drop_error) notice.textContent = status.drop_error;
      render();
    })();
    refreshing = task;
    try {await task;} finally {if (refreshing === task) refreshing = null;}
  };
  const pick = async () => {
    if (picking) return;
    try {
      writable(); picking = true;
      const receipts = await nativeFilesPick(draftId); live();
      for (const receipt of receipts) {adopt(receipt); writable(); await nativeFilesUpload(receipt.transfer_id); live();}
      await refresh();
    } catch (error) {failure(error); await refresh().catch(failure);}
    finally {picking = false;}
  };
  const drop = h("button", {class: "secondary", "aria-pressed": false, onclick: async () => {
    try {
      writable(); armed = !armed; await nativeFilesDropTarget(draftId, armed); live();
      drop.setAttribute("aria-pressed", String(armed)); notice.textContent = armed ? t("files_drop_help") : "";
    } catch (error) {armed = false; failure(error);}
  }}, t("files_drop"));
  const closePreview = () => {
    preview.replaceChildren(); preview.hidden = true;
    if (objectUrl) URL.revokeObjectURL(objectUrl); objectUrl = null;
  };
  const actions = ref => h("span", {class: "actions file-actions"},
    h("button", {class: "secondary", onclick: async () => {
      try {
        live(); const expected = fixedRef(ref), receipt = await nativeFilesSave(expected); live();
        if (!receipt) return;
        if (receipt.direction !== "download" || !sameRef(receipt.artifact, expected)) throw new Error(t("files_receipt_mismatch"));
        downloads.add(receipt.transfer_id); adopt(receipt); render();
      } catch (error) {failure(error);}
    }}, t("files_save")),
    h("button", {class: "secondary", onclick: async () => {
      try {
        live(); const content = await nativeFilesPreview(fixedRef(ref)); live(); closePreview();
        let view;
        if (content.media_type === "text/plain" && typeof content.text === "string" && content.text.length <= 256 * 1024) view = h("pre", {}, content.text);
        else if (content.media_type === "image/png" && typeof content.base64 === "string" && content.base64.length <= 2800000) {
          const bytes = Uint8Array.from(atob(content.base64), c => c.charCodeAt(0));
          objectUrl = URL.createObjectURL(new Blob([bytes], {type: "image/png"}));
          view = h("img", {src: objectUrl, alt: t("files_preview")});
        } else throw new Error(t("files_receipt_mismatch"));
        preview.hidden = false; preview.append(h("div", {class: "actions"}, h("strong", {}, t("files_preview")),
          h("button", {class: "secondary", onclick: closePreview}, t("close"))), view);
      } catch (error) {failure(error);}
    }}, t("files_preview")));
  const tick = async () => {
    try {
      live(); await refresh();
      if (armed) {writable(); await nativeFilesDropTarget(draftId, box.getClientRects().length > 0 && document.visibilityState === "visible");}
    } catch (error) {
      try {live(); failure(error);} catch {stopped = true; closePreview(); await nativeFilesDropTarget(draftId, false).catch(() => {});}
    }
    if (!stopped) timer = setTimeout(tick, 1000);
  };
  queueMicrotask(tick);
  return {box, pick, drop, actions, refresh, has: id => known.has(id)};
}
