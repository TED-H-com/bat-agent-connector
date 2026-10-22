import {timeStamp} from './ui/stamps.js';
import {renderMessage} from "./message-format.js";
import {agentMark, disclosure, emptyState, statusPill, errorDetails} from "./ui/components.js";
import {icon} from "./ui/icons.js";
import {errorPresentation} from "./error-presentation.js";

// Durations read as seconds or minutes, never raw milliseconds.
export function messageDuration(t, ms) {
  if (!(typeof ms === "number" && Number.isFinite(ms) && ms >= 0)) return "";
  if (ms < 60_000) return t("message_seconds", {s: String(Number((ms / 1000).toFixed(1)))});
  const total = Math.round(ms / 1000), m = Math.floor(total / 60), s = total % 60;
  return s ? t("message_minutes", {m, s}) : t("message_minutes_even", {m});
}

export function readingSummary(t, reading) {
  if (!reading || reading.unread_count == null) return {line: t("conversation_unknown"), detail: ""};
  if (reading.complete) return {line: t("conversation_unread", {count: reading.unread_count}), detail: ""};
  const reasons = Array.isArray(reading.incomplete_reasons) ? reading.incomplete_reasons : [];
  const lowerBound = t("conversation_unread_lower_bound", {count: reading.unread_count});
  const idDetail = reasons.includes("message_ids") ? t("conversation_message_ids_detail") : "";
  if (reasons.includes("archive_budget")) {
    return {
      line: `${lowerBound} · ${t("conversation_archive_budget")}`,
      detail: idDetail,
    };
  }
  if (reasons.includes("archive_unconfirmed")) {
    return {
      line: `${lowerBound} · ${t("conversation_archive_unconfirmed")}`,
      detail: [t("conversation_archive_unconfirmed_detail"), idDetail].filter(Boolean).join(" "),
    };
  }
  if (reasons.includes("message_ids")) {
    return {
      line: lowerBound,
      detail: idDetail,
    };
  }
  return {line: t("conversation_partial", {count: reading.unread_count}), detail: ""};
}

// Scrolling never marks messages read. Personal receipts require an explicit action.
export function conversationPanel({h, t, when, guard, readingActions = null, memory = null}) {
  const viewport = h("div", {class: "conversation-scroll", tabindex: "0", role: "region", "aria-label": t("messages")});
  const status = h("span", {class: "muted", role: "status"}), fallback = h("div", {class: "conversation-copy"});
  const notice = h("p", {class: "muted", role: "status", hidden: true}, t("message_anchor_missing"));
  const latest = h("button", {class: "mini conversation-jump", type: "button", hidden: true, onclick: () => {
    if (olderWindow && readingActions) {run(() => readingActions.latest(), t("message_window_failed"), false); return;}
    follow(); notice.hidden = true;
    // The button hides itself; keep the keyboard in the transcript instead of dropping to the page.
    viewport.focus({preventScroll: true});
  }}, t("message_latest"));
  const progress = h("span", {class: "muted", role: "status", "data-conversation-reading": ""});
  const mark = h("button", {class: "mini", type: "button", onclick: () => {
    const messages = visible().filter(row => row.reading?.can_mark && row.reading?.unread)
      .map(row => ({message_id: row.id, revision: row.reading.revision}));
    if (messages.length) run(() => readingActions.mark(messages), t("conversation_save_failed"), true);
  }}, t("conversation_mark"));
  const remember = h("button", {class: "mini", type: "button", onclick: () => {
    const row = visible().find(row => row.reading);
    if (row?.reading) run(() => readingActions.remember({message_id: row.id, revision: row.reading.revision,
      offset: Math.round(row.node.getBoundingClientRect().top - viewport.getBoundingClientRect().top)}, reading?.position?.version || 0), t("conversation_save_failed"), true);
  }}, t("conversation_remember"));
  const older = h("button", {class: "conversation-earlier", type: "button", hidden: true,
    onclick: () => run(() => readingActions.older(), t("message_window_failed"), false)}, t("message_load_earlier"));
  const caption = h("p", {class: "muted conversation-window"});
  const captionRow = h("div", {class: "conversation-caption"}, caption);
  const readingBar = readingActions ? h("div", {class: "actions"}, progress, mark, remember) : null;
  const box = h("section", {class: "panel conversation"},
    captionRow, notice, viewport,
    h("div", {class: "conversation-toolbar"}, status, latest),
    readingBar, fallback);
  let rows = new Map(), initialized = false, disposed = false, copyAttempt = 0, reading = null, busy = false, olderWindow = false;
  // Pinned changes only when the reader scrolls; layout changes re-pin instead of unpinning.
  let pinned = true, unseen = 0, userLabel = null, last = null, resizing = false;
  const alive = () => {if (disposed) return false; try {guard(); return true;} catch {return false;}};
  // A queued scroll event may run after controls change the viewport height.
  // Remember our applied position so that event cannot impersonate a reader scroll.
  let scrollPosition = 0;
  const setPosition = value => {viewport.scrollTop = value; scrollPosition = viewport.scrollTop;};
  const atBottom = () => viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <= 48;
  const visible = () => {
    if (document.visibilityState !== "visible") return [];
    const bounds = viewport.getBoundingClientRect(), top = Math.max(bounds.top, 0), bottom = Math.min(bounds.bottom, innerHeight);
    return [...rows.values()].filter(row => {
      const rect = row.node.getBoundingClientRect();
      return bottom > top && rect.bottom > top && rect.top < bottom;
    });
  };
  const indicator = () => {
    if (pinned) unseen = 0;
    latest.textContent = !olderWindow && unseen ? t("message_new") : t("message_latest");
    latest.hidden = !olderWindow && (pinned || !rows.size);
    if (memory && initialized) memory.scrollTop = viewport.scrollTop;
    if (!readingActions) return;
    const availability = (control, reason) => {
      control.disabled = Boolean(reason);
      control.setAttribute("aria-description", reason); control.title = reason;
    };
    availability(mark, busy ? t("conversation_saving") : !visible().some(row => row.reading?.can_mark && row.reading?.unread) ? t("conversation_no_unread") : "");
    availability(remember, busy ? t("conversation_saving") : !visible().some(row => row.reading) ? t("conversation_no_position") : "");
    availability(older, busy ? t("conversation_saving") : "");
  };
  const follow = () => {pinned = true; unseen = 0; setPosition(viewport.scrollHeight); indicator();};
  const run = async (action, summary = t("conversation_save_failed"), isSave = true) => {
    if (!alive() || busy) return;
    busy = true; status.replaceChildren(); indicator();
    try {
      await action();
      if (alive() && isSave) status.textContent = t("conversation_saved");
    } catch (error) {
      if (alive()) {
        const presentation = errorPresentation(error, t, {summary});
        const details = errorDetails(presentation);
        status.replaceChildren(
          h("div", {'data-api-error': ''},
            h("span", {'data-error-summary': '', class: 'error'}, presentation.summary),
            details || ''
          )
        );
      }
    } finally {
      busy = false;
      if (alive()) indicator();
    }
  };
  const observePosition = () => {
    // A hidden perspective has no scroll geometry; zero is not reader movement.
    if (!viewport.clientHeight) return;
    if (initialized && !resizing && viewport.scrollTop !== scrollPosition) pinned = atBottom();
    scrollPosition = viewport.scrollTop;
  };
  const onScroll = () => {
    observePosition();
    indicator();
  };
  viewport.addEventListener("scroll", onScroll, {passive: true});
  viewport.addEventListener("focusin", () => requestAnimationFrame(onScroll));
  window.addEventListener("scroll", indicator, {passive: true});
  const onResize = () => {
    resizing = true;
    requestAnimationFrame(() => {
      if (!alive()) return;
      if (pinned && initialized) setPosition(viewport.scrollHeight);
      requestAnimationFrame(() => {resizing = false; indicator();});
    });
  };
  window.addEventListener("resize", onResize);
  // A growing composer or a narrower window shrinks the transcript without a scroll event.
  const resized = typeof ResizeObserver === "function" ? new ResizeObserver(() => {
    if (alive() && pinned && initialized) setPosition(viewport.scrollHeight);
  }) : null;
  resized?.observe(viewport);
  const copy = async text => {
    if (!alive()) return;
    const attempt = ++copyAttempt;
    status.textContent = ""; fallback.replaceChildren();
    try {await navigator.clipboard.writeText(text); if (alive() && attempt === copyAttempt) status.textContent = t("message_copied");}
    catch {
      if (!alive() || attempt !== copyAttempt) return;
      const input = h("textarea", {readonly: true, "aria-label": t("message_copy_source")}, text);
      // Textarea.value normalizes CRLF. A whole-source manual copy still uses the
      // original string; partial selections keep the browser's normal behavior.
      input.addEventListener("copy", event => {
        if (alive() && event.clipboardData && input.selectionStart === 0 && input.selectionEnd === input.value.length) {
          event.clipboardData.setData("text/plain", text); event.preventDefault();
        }
      });
      fallback.replaceChildren(h("p", {class: "muted"}, t("message_copy_manual")), input,
        h("button", {class: "mini", type: "button", onclick: () => fallback.replaceChildren()}, t("cancel")));
      input.focus(); input.select();
    }
  };
  const countCaption = (count, more) => count === 1 && !more ? t("message_window_one")
    : more ? t("message_window_latest", {count}) : t("message_window_count", {count});
  const update = (messages, state = null) => {
    if (!alive()) return;
    // Scroll events arrive asynchronously. Capture a reader's movement before
    // changing message geometry, even if its scroll event is still queued.
    observePosition();
    last = {messages, state};
    const top = viewport.getBoundingClientRect().top;
    const anchor = [...rows.entries()].find(([, row]) => row.node.getBoundingClientRect().bottom > top);
    const selection = window.getSelection();
    const readingSelection = selection && !selection.isCollapsed && viewport.contains(selection.anchorNode);
    const savedScroll = !initialized && memory && Number.isFinite(memory.scrollTop) && !state?.restore && !state?.latest;
    const followNow = !savedScroll && (!initialized || !state?.preserve && pinned && !readingSelection);
    const offset = anchor ? anchor[1].node.getBoundingClientRect().top - top : 0, scrollTop = viewport.scrollTop;
    const next = new Map(), occurrences = new Map();
    let added = 0;
    for (const message of messages) {
      const text = typeof message.text === "string" ? message.text : "";
      const identity = JSON.stringify(message.id != null ? ["id", message.id] : ["text", message.role, message.ts, text]);
      const occurrence = occurrences.get(identity) || 0; occurrences.set(identity, occurrence + 1);
      const key = JSON.stringify([identity, occurrence]);
      let row = rows.get(key);
      if (!row) {
        added += 1;
        row = {text: null, meta: null, body: h("div", {class: "message-body"}),
          mark: h("span", {class: "ui-agent-mark", "aria-hidden": "true"}),
          name: h("span", {class: "message-name"}), time: h("time", {class: "message-time"}),
          status: h("span", {class: "message-status"}),
          extra: h("span", {class: "message-extra"}), who: h("span", {class: "who"})};
        row.chipText = h("span", {class: "message-chip-text"});
        row.chip = h("span", {class: "message-chip", hidden: true}, row.chipText);
        row.who.append(row.name, row.time, row.status, row.chip, row.extra);
        row.node = h("article", {class: "msg"}, row.mark,
          h("div", {class: "message-meta"}, row.who,
            h("button", {class: "message-copy", type: "button", "aria-label": t("message_copy"), title: t("message_copy"),
              onclick: () => copy(row.text)}, icon("clipboard"))), row.body);
      }
      const extras = [];
      const pillKey = message.role !== "tool" && typeof message.status !== "string" ? ""
        : message.denied ? "blocked" : message.deferred ? "waiting"
        : message.status === "completed" ? "done"
        : message.status === "failed" || message.status === "error" ? "failed"
        : message.status === "running" ? "running" : "unknown";
      const agent = typeof message.agent === "string" && message.agent ? message.agent : "";
      const whoName = message.role === "user" ? userLabel || t("message_you")
        : message.role === "tool" ? (message.tool || t("message_tool_details"))
        : message.role === "system" ? t("message_system")
        : (agent || t("agent"));
      // The name already says which agent; the chip adds only what it does not say.
      const chip = [];
      if (agent && whoName !== agent) chip.push(agent);
      if (typeof message.model === "string" && message.model) chip.push(message.model);
      const duration = messageDuration(t, message.duration_ms);
      if (duration) extras.push(duration);
      const stamp = when(message.ts);
      const meta = JSON.stringify([message.role, whoName, agent, stamp, chip, extras, pillKey]);
      if (row.meta !== meta) {
        if (message.role === "tool") row.mark.replaceChildren(icon("wrench"));
        else if (message.role === "system") row.mark.replaceChildren(icon("settings"));
        else if (message.role === "user") row.mark.textContent = Array.from(whoName)[0]?.toUpperCase() || "";
        else row.mark.textContent = agentMark(agent).textContent;
        row.name.textContent = whoName;
        const stampNode = timeStamp(message.ts, {empty:''});
        row.time.textContent = stamp;
        if (stampNode instanceof Node) {row.time.dateTime = stampNode.dateTime; row.time.title = stampNode.title; row.time.dataset.uiTime = stampNode.dataset.uiTime;}
        else {
          for (const attr of ['datetime', 'title', 'data-ui-time']) row.time.removeAttribute(attr);
        }
        const chipText = chip.join(" · ");
        row.chipText.textContent = chipText;
        row.chip.hidden = !chipText;
        if (chipText) row.chip.title = chipText; else row.chip.removeAttribute("title");
        row.status.replaceChildren(...(pillKey ? [statusPill(pillKey)] : []));
        row.extra.textContent = extras.join(" · ");
        row.meta = meta;
      }
      row.node.className = `msg${message.role === "user" ? " user" : ""}`;
      row.id = message.id; row.reading = message.reading;
      row.node.dataset.messageId = typeof message.id === "string" ? message.id : "";
      const tool = message.role === "tool", system = message.role === "system";
      const completed = tool && message.status === "completed" && !message.denied && !message.deferred;
      const folded = tool || system;
      if (row.tool !== folded) {row.body.replaceChildren(); row.text = null; row.tool = folded; row.toolDetails = null; row.pill = undefined;}
      if (folded && !row.toolDetails) {
        row.toolContent = h("div", {});
        row.toolLabel = h("span", {class: "message-tool-label"});
        row.toolDetails = disclosure({label: row.toolLabel, body: row.toolContent, open: tool ? !completed : false});
        row.toolDetails.classList.add("message-tool");
        row.toolDetails.addEventListener("toggle", indicator); row.body.append(row.toolDetails);
      } else if (tool && !completed && row.pill !== pillKey) row.toolDetails.open = true;
      // Errors, pending and unknown open when they first appear or change state; afterwards the reader's toggle wins.
      row.pill = pillKey;
      if (row.text !== text) {
        if (row.text !== null) added += 1; // a streamed revision is new content too
        (folded ? row.toolContent : row.body).replaceChildren(...renderMessage(h, t, text, copy)); row.text = text;
        if (folded && row.toolDetails) {
          const line = text.split("\n").find(item => item.trim())?.trim() || "";
          const label = line || t(tool ? "message_tool_details" : "message_system");
          row.toolLabel.textContent = label;
          row.toolDetails.querySelector("summary").title = Array.from(label).slice(0, 200).join("");
        }
      }
      next.set(key, row);
    }
    // Keep unchanged nodes in place so refresh does not erase focus or selected text.
    const retained = new Set([...next.values()].map(row => row.node));
    for (const node of [...viewport.childNodes]) if (!retained.has(node)) node.remove();
    let position = viewport.firstChild;
    for (const row of next.values()) {
      if (row.node === position) position = position.nextSibling;
      else viewport.insertBefore(row.node, position);
    }
    while (position) {const old = position; position = old.nextSibling; old.remove();}
    if (!next.size) viewport.replaceChildren(emptyState({icon: "message-square", title: t("no_messages")}));
    if (initialized && !followNow && added) unseen += added;
    if (followNow) {setPosition(viewport.scrollHeight); pinned = true; unseen = 0; notice.hidden = true;}
    else if (savedScroll) {setPosition(memory.scrollTop); notice.hidden = true;}
    else if (anchor && next.has(anchor[0])) setPosition(viewport.scrollTop + next.get(anchor[0]).node.getBoundingClientRect().top - viewport.getBoundingClientRect().top - offset);
    else {setPosition(scrollTop); if (anchor) notice.hidden = false;}
    rows = next;
    if (state?.restore) {
      const row = [...rows.values()].find(row => row.id === state.restore.message_id);
      if (row) {
        setPosition(viewport.scrollTop + row.node.getBoundingClientRect().top - viewport.getBoundingClientRect().top - state.restore.offset);
        notice.hidden = false; notice.textContent = t("conversation_restored");
      } else {notice.hidden = false; notice.textContent = t("message_anchor_missing");}
    }
    if (state?.latest) {setPosition(viewport.scrollHeight); notice.hidden = true; viewport.focus({preventScroll: true});}
    const more = state?.next_offset != null;
    if (readingActions && state) {
      reading = state.reading; olderWindow = Boolean(state.olderWindow);
      older.hidden = !more;
      older.textContent = t("message_load_earlier");
      if (more) caption.replaceChildren(t("message_window_latest", {count: next.size}) + "\u00a0· ", older);
      else {if (older.parentElement === caption) readingBar?.append(older); caption.textContent = countCaption(next.size, false);}
      const summary = readingSummary(t, reading);
      progress.textContent = summary.line;
      progress.title = [summary.detail, t("conversation_count_note")].filter(Boolean).join(" ");
    } else caption.textContent = countCaption(next.size, more);
    if (!initialized || followNow || savedScroll || state?.latest || state?.restore || state?.preserve)
      pinned = atBottom() || followNow && !state?.restore && !state?.preserve;
    initialized = true; indicator();
  };
  // A failed read draws its state in the transcript. Rows already shown stay, marked as not updated.
  const fail = ({error}) => {
    if (!alive()) return;
    if (!rows.size) {
      viewport.replaceChildren(h("div", {class: "conversation-state", "data-conversation-state": "error"}, error));
      caption.textContent = "";
    } else {
      viewport.prepend(error);
      caption.textContent = t("message_window_stale");
    }
    indicator();
  };
  // Withdrawal: drop every row and its text together.
  const clear = () => {
    copyAttempt += 1; // an outstanding clipboard failure cannot restore withdrawn text
    rows = new Map(); initialized = false; viewport.replaceChildren(); caption.textContent = ""; notice.hidden = true;
    status.textContent = ""; fallback.replaceChildren(); last = null; unseen = 0; indicator();
  };
  const setUserLabel = label => {
    if (label === userLabel) return;
    userLabel = label;
    if (last && initialized) update(last.messages, last.state ? {...last.state, restore: null, latest: false} : null);
  };
  return {box, update, fail, clear, follow, setUserLabel, dispose() {
    disposed = true;
    if (memory) memory.scrollTop = viewport.scrollTop;
    resized?.disconnect();
    viewport.removeEventListener("scroll", onScroll);
    window.removeEventListener("scroll", indicator); window.removeEventListener("resize", onResize);
  }};
}
