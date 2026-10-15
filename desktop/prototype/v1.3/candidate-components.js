import { h } from "../../src/ui/dom.js";
import { button, segmented, sectionCard, disclosure, agentMark } from "../../src/ui/components.js";
import { timeStamp } from "../../src/ui/stamps.js";
import { t } from "./proto-i18n.js";
import { FIXED_NOW, GOLDEN_CONSTANTS as C, DELIVERY_STATES_ORDER } from "./golden-data.js";
export const pill = (label, tone = "neutral", attrs = {}) => h("span", {
  class: `ui-pill tone-${tone}`,
  ...attrs
}, label);
export const action = (key, testId, onClick, variant = "secondary", attrs = {}) => button({
  label: t(key),
  variant,
  size: "sm",
  "data-testid": testId,
  onClick,
  ...attrs
});
export const notice = (key, testId, tone = "info") => h("p", {
  class: `proto-notice tone-${tone}`,
  role: "status",
  "data-testid": testId
}, t(key));
export const details = (body, label = "details") => disclosure({
  label: t(label),
  body
});
export const facts = rows => h("dl", {
  class: "proto-facts"
}, ...rows.map(([key, value]) => h("div", {}, h("dt", {}, t(key)), h("dd", {}, value))));
export function message(author, body, pm = false) {
  return h("article", {
    class: `msg proto-message ${pm ? "proto-pm-message" : "user"}`
  }, ["PM", "Claude", "BAT"].includes(author) ? agentMark(author) : h("span", {
    class: "ui-agent-mark",
    "aria-hidden": "true"
  }, author === "mei.lin" ? "ML" : t("message_person").slice(0, 1)), h("div", {
    class: "message-meta"
  }, h("div", {
    class: "who"
  }, h("span", {
    class: "message-name"
  }, author), h("span", {
    class: "message-time"
  }, timeStamp(FIXED_NOW - 3 * 60000, {
    milliseconds: true,
    now: FIXED_NOW
  })))), h("div", {
    class: "message-body"
  }, body));
}
export function composer({
  state,
  onIntent,
  onInput,
  onSend,
  onNote
}) {
  const intent = state.composerIntent;
  const keys = {
    discuss: ["sendPm", "composer-send-pm"],
    plan: ["updatePlan", "composer-update-plan"],
    do: [state.workStarted ? "sendNext" : "startWork", state.workStarted ? "composer-send-next" : "composer-start-work"]
  };
  return h("section", {
    class: "proto-composer",
    "data-testid": "intent-composer",
    "data-active-intent": intent
  }, segmented({
    label: t("intent"),
    value: intent,
    options: ["discuss", "plan", "do"].map(value => ({
      value,
      label: t(value)
    })),
    onChange: onIntent
  }), h("textarea", {
    rows: 3,
    "aria-label": t("messageLabel"),
    placeholder: t("messagePlaceholder"),
    "data-testid": "composer-textarea",
    oninput: e => onInput(e.target.value)
  }, state.composerDraft), intent === "discuss" && state.composerDraft.trim() ? h("div", {
    class: "proto-suggestion",
    "data-testid": "intent-suggestion-banner"
  }, t("suggestion"), action("toPlan", "suggest-switch-plan", () => onIntent("plan"), "ghost"), action("toDo", "suggest-switch-do", () => onIntent("do"), "ghost")) : null, state.noteSavedBanner ? notice("noteSaved", "note-saved-banner", "ok") : null, h("div", {
    class: "proto-actions"
  }, intent === "discuss" ? action("saveNote", "composer-save-note", onNote) : null, action(keys[intent][0], keys[intent][1], onSend, "primary", {
    disabled: intent === "discuss" && !state.pmAvailable
  })));
}
export function planCard(state, adoptLater, adoptStart) {
  return sectionCard({
    title: t("planTitle"),
    actions: [pill(state.planAdoptedLater || state.adoptMode !== "idle" ? t("ui_status_adopted") : t("ui_status_draft"))],
    body: h("div", {
      "data-testid": "short-plan-card"
    }, h("p", {
      class: "muted"
    }, t("planVersion")), facts([["goal", t("goalValue")], ["scope", t("scopeValue")], ["doneConditions", t("doneValue")], ["profileLabel", t("profile")], ["host", C.hostId], ["destination", t("destinationValue")]]), details(h("code", {}, C.planRevision)), state.planAdoptedLater ? notice("adoptedLater", "plan-adopted-later-notice") : null, state.adoptMode === "idle" ? h("div", {
      class: "proto-actions"
    }, action("adoptLater", "plan-adopt-later", adoptLater), action("adoptStart", "plan-adopt-start", adoptStart, "primary")) : null)
  });
}
export function stepList(state, resume, openWork) {
  const ids = [C.operationKey, "membership.email_sign_in", C.planRevision, C.workId, C.managedWorktreeId, C.managedSessionId, "msg_first_01", C.discussionId];
  return sectionCard({
    title: t("stepsTitle"),
    body: h("div", {}, state.adoptMode === "partial_failure" ? h("div", {
      class: "proto-diagnostic",
      "data-testid": "partial-adopt-diagnostic"
    }, h("strong", {}, t("partialTitle")), h("p", {}, t("partialReason")), h("p", {}, t("partialRecovery")), h("p", {}, t("draftKept")), action("continueSteps", "continue-unfinished-step", resume, "primary")) : null, h("ol", {
      class: "proto-steps",
      "data-testid": "compound-step-list"
    }, ...state.adoptSteps.map((step, i) => h("li", {
      "data-testid": `adopt-step-s${i + 1}`,
      "data-step-status": step.status
    }, h("div", {}, h("span", {
      class: "proto-step-result"
    }, step.status === "done" ? t(`s${i + 1}`) : t(step.status === "failed" ? "pausedStep" : `s${i + 1}Pending`)), details(h("code", {}, ids[i]))), pill(t(step.status === "done" ? "ui_status_done" : step.status === "failed" ? "ui_status_needs_you" : "ui_status_not_started"), step.status === "done" ? "ok" : "neutral")))), state.adoptMode === "completed" ? action("openWork", "adopt-open-work-page", openWork, "primary") : null)
  });
}
export function deliveryBar(delivery, createPr) {
  return h("div", {
    "data-testid": "delivery-progression-bar"
  }, h("h3", {}, t("delivery")), h("dl", {
    class: "proto-delivery"
  }, ...DELIVERY_STATES_ORDER.map(key => h("div", {
    "data-testid": `delivery-state-${key}`,
    "data-delivery-achieved": String(Boolean(delivery[key]))
  }, h("dt", {
    class: "proto-delivery-step__label"
  }, t(key === "accepted" ? "delivery_accepted" : key)), h("dd", {}, pill(t(delivery[key] ? "recorded" : "notRecorded"), delivery[key] ? "ok" : "neutral"))))), delivery.pr_open ? h("p", {
    class: "muted"
  }, t("prCreated")) : createPr ? action("createPr", "btn-create-pr", createPr, "primary") : null);
}
export function unifiedDiffView({
  hunks = []
} = {}) {
  return h("div", {
    class: "proto-diff-view",
    "data-testid": "unified-diff-view"
  }, ...hunks.map(hunk => h("div", {
    class: "proto-diff-file",
    "data-diff-file": hunk.file
  }, h("div", {
    class: "proto-diff-file__header"
  }, h("code", {
    class: "proto-diff-file__name",
    "data-mono": ""
  }, hunk.file), h("span", {
    class: "proto-diff-file__stats",
    "data-mono": ""
  }, hunk.stats)), h("div", {
    class: "proto-diff-table",
    role: "region",
    "aria-label": hunk.file
  }, ...hunk.lines.map(line => h("div", {
    class: `proto-diff-line proto-diff-line--${line.type}`
  }, h("span", {
    class: "proto-diff-line__num",
    "data-mono": ""
  }, line.oldLine != null ? String(line.oldLine) : ""), h("span", {
    class: "proto-diff-line__num",
    "data-mono": ""
  }, line.newLine != null ? String(line.newLine) : ""), h("span", {
    class: "proto-diff-line__prefix",
    "data-mono": ""
  }, line.type === "add" ? "+" : line.type === "del" ? "-" : " "), h("span", {
    class: "proto-diff-line__code",
    "data-mono": ""
  }, line.text)))))));
}
