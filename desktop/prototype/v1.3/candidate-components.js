import { h } from "../../src/ui/dom.js";
import {
  button,
  statusPill,
  segmented,
  sectionCard,
} from "../../src/ui/components.js";
import { icon } from "../../src/ui/icons.js";
import { t } from "./proto-i18n.js";
import { DELIVERY_STATES_ORDER } from "./golden-data.js";

/**
 * Helper: Structured Key-Value description list
 */
export function kvList(rows = []) {
  return h(
    "dl",
    { class: "proto-kv-list" },
    ...rows.map((row) =>
      h(
        "div",
        { class: "proto-kv-list__row" },
        h("dt", { class: "proto-kv-list__term" }, row.label),
        h(
          "dd",
          {
            class: "proto-kv-list__desc",
            ...(row.mono ? { "data-mono": "" } : {}),
          },
          row.value,
        ),
      ),
    ),
  );
}

/**
 * Helper: Semantic inline status/feedback notice
 */
export function inlineNotice({ tone = "info", title, testId } = {}) {
  return h(
    "div",
    {
      class: `proto-inline-notice proto-inline-notice--${tone}`,
      role: "status",
      ...(testId ? { "data-testid": testId } : {}),
    },
    icon(tone === "good" ? "check" : tone === "warn" ? "circle-alert" : "info", {
      size: 16,
    }),
    h("span", null, title),
  );
}

/**
 * Helper: Custom labeled pill for domain-specific states that aren't in statusVocabulary
 */
export function labeledPill(tone, label, extraAttrs = {}) {
  return h(
    "span",
    {
      class: `ui-pill tone-${tone}`,
      ...extraAttrs,
    },
    label,
  );
}

/**
 * Candidate Shared Component 1: diagnosticStateCard (§6.4)
 * Every empty, unavailable, partial-failure, or disconnect state names:
 * - what is missing
 * - what can be done here
 * - where to fix it
 * - whether the draft was kept
 */
export function diagnosticStateCard({
  tone = "warn",
  title,
  whatMissing,
  whatCanDo,
  whereToFix,
  draftStatus,
  actions = [],
  testId = "diagnostic-state-card",
} = {}) {
  return h(
    "div",
    {
      class: `proto-diagnostic-card proto-diagnostic-card--${tone}`,
      "data-testid": testId,
      "data-diagnostic-tone": tone,
    },
    h(
      "div",
      { class: "proto-diagnostic-card__header" },
      icon("circle-alert", { size: 16 }),
      h("strong", { class: "proto-diagnostic-card__title" }, title),
    ),
    h(
      "ul",
      { class: "proto-diagnostic-card__list" },
      h("li", { "data-diagnostic-field": "missing" }, whatMissing),
      h("li", { "data-diagnostic-field": "can-do" }, whatCanDo),
      h("li", { "data-diagnostic-field": "where-fix" }, whereToFix),
      h("li", { "data-diagnostic-field": "draft-kept" }, draftStatus),
    ),
    actions.length
      ? h("div", { class: "proto-diagnostic-card__actions" }, ...actions)
      : null,
  );
}

/**
 * Candidate Shared Component 2: intentComposer (§6.2)
 * One composer with three explicit intents: Discuss, Plan, Do.
 * Suggestions never auto-execute.
 */
export function intentComposer({
  intent = "discuss",
  draft = "",
  workStarted = false,
  pmAvailable = true,
  noteSavedBanner = false,
  onIntentChange,
  onDraftInput,
  onSendPm,
  onSaveNoteOnly,
  onUpdatePlan,
  onStartWork,
  onSendNextStep,
  onFillFirstSentence,
  onFillSecondSentence,
} = {}) {
  const placeholderKey =
    intent === "plan"
      ? "proto.composer.placeholder_plan"
      : intent === "do"
        ? "proto.composer.placeholder_do"
        : "proto.composer.placeholder_discuss";

  const intentSelect = segmented({
    label: t("proto.composer.intent_label"),
    value: intent,
    onChange: onIntentChange,
    options: [
      { value: "discuss", label: t("proto.composer.intent_discuss") },
      { value: "plan", label: t("proto.composer.intent_plan") },
      { value: "do", label: t("proto.composer.intent_do") },
    ],
  });

  const primaryActions = [];
  if (intent === "discuss") {
    primaryActions.push(
      button({
        variant: "primary",
        label: t("proto.composer.action_send_pm"),
        "data-testid": "composer-send-pm",
        disabled: !pmAvailable,
        onClick: onSendPm,
      }),
      button({
        variant: "secondary",
        label: t("proto.composer.action_save_note"),
        "data-testid": "composer-save-note",
        onClick: onSaveNoteOnly,
      }),
    );
  } else if (intent === "plan") {
    primaryActions.push(
      button({
        variant: "primary",
        label: t("proto.composer.action_update_plan"),
        "data-testid": "composer-update-plan",
        onClick: onUpdatePlan,
      }),
    );
  } else if (intent === "do") {
    if (!workStarted) {
      primaryActions.push(
        button({
          variant: "primary",
          label: t("proto.composer.action_start_work"),
          "data-testid": "composer-start-work",
          onClick: onStartWork,
        }),
      );
    } else {
      primaryActions.push(
        button({
          variant: "primary",
          label: t("proto.composer.action_send_next"),
          "data-testid": "composer-send-next",
          onClick: onSendNextStep,
        }),
      );
    }
  }

  const showSuggestion = intent === "discuss" && draft.trim().length > 0;

  return h(
    "section",
    {
      class: "proto-composer",
      "data-testid": "intent-composer",
      "data-active-intent": intent,
    },
    h(
      "div",
      { class: "proto-composer__top" },
      h(
        "div",
        { class: "proto-composer__heading" },
        h("h3", { class: "proto-composer__title" }, t("proto.composer.title")),
        h("p", { class: "proto-composer__subtitle" }, t("proto.composer.subtitle")),
      ),
      h("div", { class: "proto-composer__controls" }, intentSelect),
    ),
    h(
      "label",
      { class: "proto-composer__label" },
      h("span", { class: "sr-only" }, t("proto.composer.input_label")),
      h(
        "textarea",
        {
          class: "proto-composer__textarea",
          "data-testid": "composer-textarea",
          rows: "3",
          placeholder: t(placeholderKey),
          oninput: (e) => onDraftInput && onDraftInput(e.target.value),
        },
        draft,
      ),
    ),
    showSuggestion
      ? h(
          "div",
          {
            class: "proto-composer__suggestion",
            "data-testid": "intent-suggestion-banner",
          },
          h("span", { class: "proto-composer__suggestion-text" }, t("proto.composer.suggestion_banner")),
          h(
            "div",
            { class: "proto-composer__suggestion-actions" },
            button({
              variant: "ghost",
              size: "sm",
              label: t("proto.composer.suggestion_switch_plan"),
              "data-testid": "suggest-switch-plan",
              onClick: () => onIntentChange && onIntentChange("plan"),
            }),
            button({
              variant: "ghost",
              size: "sm",
              label: t("proto.composer.suggestion_switch_do"),
              "data-testid": "suggest-switch-do",
              onClick: () => onIntentChange && onIntentChange("do"),
            }),
          ),
        )
      : null,
    noteSavedBanner
      ? inlineNotice({
          tone: "good",
          title: t("proto.composer.note_saved_banner"),
          testId: "note-saved-banner",
        })
      : null,
    h(
      "div",
      { class: "proto-composer__footer" },
      h(
        "div",
        { class: "proto-composer__quickfill" },
        button({
          variant: "ghost",
          size: "sm",
          label: t("proto.composer.fill_first_sentence"),
          "data-testid": "fill-first-sentence",
          onClick: onFillFirstSentence,
        }),
        button({
          variant: "ghost",
          size: "sm",
          label: t("proto.composer.fill_second_sentence"),
          "data-testid": "fill-second-sentence",
          onClick: onFillSecondSentence,
        }),
      ),
      h("div", { class: "proto-composer__actions" }, ...primaryActions),
    ),
  );
}

/**
 * Candidate Shared Component 3: shortPlanCard (F3/F4)
 * Displays the 5-field short plan summary card and the two adoption choices.
 */
export function shortPlanCard({
  adoptedLater = false,
  adoptMode = "idle",
  onAdoptLater,
  onAdoptAndStart,
  onSimulatePartial,
} = {}) {
  const pillNode =
    adoptMode === "partial_failure"
      ? labeledPill("warn", t("proto.plan.partial_banner_title"))
      : adoptedLater || adoptMode === "completed"
        ? statusPill("adopted")
        : statusPill("draft");

  return sectionCard({
    title: t("proto.plan.card_title"),
    actions: [pillNode],
    body: h(
      "div",
      { class: "proto-plan-card__body", "data-testid": "short-plan-card" },
      h("p", { class: "proto-composer__subtitle" }, t("proto.plan.card_version")),
      kvList([
        { label: t("proto.plan.field_goal"), value: t("proto.plan.val_goal") },
        { label: t("proto.plan.field_scope"), value: t("proto.plan.val_scope"), mono: true },
        { label: t("proto.plan.field_done"), value: t("proto.plan.val_done") },
        { label: t("proto.plan.field_profile"), value: t("proto.plan.val_profile") },
        { label: t("proto.plan.field_dest"), value: t("proto.plan.val_dest"), mono: true },
      ]),
      adoptedLater
        ? inlineNotice({
            tone: "info",
            title: t("proto.plan.adopted_later_notice"),
            testId: "plan-adopted-later-notice",
          })
        : null,
      h(
        "div",
        { class: "proto-plan-card__actions", "data-testid": "short-plan-actions" },
        button({
          variant: "secondary",
          label: t("proto.plan.btn_adopt_later"),
          "data-testid": "plan-adopt-later",
          onClick: onAdoptLater,
        }),
        button({
          variant: "primary",
          label: t("proto.plan.btn_adopt_start"),
          "data-testid": "plan-adopt-start",
          onClick: onAdoptAndStart,
        }),
        button({
          variant: "ghost",
          size: "sm",
          label: t("proto.plan.btn_simulate_partial"),
          "data-testid": "plan-simulate-partial",
          onClick: onSimulatePartial,
        }),
      ),
    ),
  });
}

/**
 * Candidate Shared Component 4: compoundStepList (§7.3)
 * Renders the 8 ordered steps for "Adopt and start", including partial failure
 * ("計畫已採用，工作尚未開始") and continuing only the unfinished step.
 */
export function compoundStepList({
  steps = [],
  adoptMode = "idle",
  onContinueUnfinished,
  onOpenWork,
} = {}) {
  const stepMeta = [
    { id: "s1", label: t("proto.step.s1_label"), detail: t("proto.step.s1_detail") },
    { id: "s2", label: t("proto.step.s2_label"), detail: t("proto.step.s2_detail") },
    { id: "s3", label: t("proto.step.s3_label"), detail: t("proto.step.s3_detail") },
    { id: "s4", label: t("proto.step.s4_label"), detail: t("proto.step.s4_detail") },
    { id: "s5", label: t("proto.step.s5_label"), detail: t("proto.step.s5_detail") },
    { id: "s6", label: t("proto.step.s6_label"), detail: t("proto.step.s6_detail") },
    { id: "s7", label: t("proto.step.s7_label"), detail: t("proto.step.s7_detail") },
    { id: "s8", label: t("proto.step.s8_label"), detail: t("proto.step.s8_detail") },
  ];

  const rows = stepMeta.map((meta, idx) => {
    const stateObj = steps[idx] || { status: "pending" };
    const status = stateObj.status;
    const tone =
      status === "done"
        ? "ok"
        : status === "failed"
          ? "warn"
          : status === "running"
            ? "info"
            : "neutral";
    const statusLabel =
      status === "done"
        ? t("proto.step.status_done")
        : status === "failed"
          ? t("proto.step.status_failed")
          : status === "running"
            ? t("proto.step.status_running")
            : t("proto.step.status_pending");

    return h(
      "li",
      {
        class: `proto-step-item proto-step-item--${status}`,
        "data-testid": `adopt-step-${meta.id}`,
        "data-step-status": status,
      },
      h(
        "div",
        { class: "proto-step-item__main" },
        h("span", { class: "proto-step-item__title" }, meta.label),
        h("span", { class: "proto-step-item__detail" }, meta.detail),
      ),
      labeledPill(tone, statusLabel),
    );
  });

  return sectionCard({
    title: t("proto.step.title"),
    body: h(
      "div",
      { class: "proto-step-section" },
      adoptMode === "partial_failure"
        ? diagnosticStateCard({
            tone: "warn",
            title: t("proto.plan.partial_banner_title"),
            whatMissing: t("proto.plan.partial_banner_what"),
            whatCanDo: t("proto.plan.partial_banner_here"),
            whereToFix: t("proto.plan.partial_banner_where"),
            draftStatus: t("proto.plan.partial_banner_draft"),
            testId: "partial-adopt-diagnostic",
            actions: [
              button({
                variant: "primary",
                label: t("proto.plan.btn_continue_unfinished"),
                "data-testid": "continue-unfinished-step",
                onClick: onContinueUnfinished,
              }),
            ],
          })
        : null,
      h("ol", { class: "proto-step-list", "data-testid": "compound-step-list" }, ...rows),
      adoptMode === "completed"
        ? h(
            "div",
            { class: "proto-step-section__footer" },
            button({
              variant: "primary",
              label: t("proto.step.open_work_btn"),
              "data-testid": "adopt-open-work-page",
              onClick: onOpenWork,
            }),
          )
        : null,
    ),
  });
}

/**
 * Candidate Shared Component 5: liveStreamOutput (F5 Main area)
 * Displays public tool invocations and live stream lines without innerHTML or inline styles.
 */
export function liveStreamOutput({ tools = [], streamLines = [] } = {}) {
  return h(
    "div",
    { class: "proto-live-stream", "data-testid": "live-stream-output" },
    h("h4", { class: "proto-live-stream__heading" }, t("proto.work.tool_activity_title")),
    h(
      "ul",
      { class: "proto-tool-list" },
      ...tools.map((item, idx) =>
        h(
          "li",
          { class: "proto-tool-list__item", "data-testid": `tool-activity-${idx + 1}` },
          icon("cpu", { size: 14 }),
          h("span", { class: "proto-tool-list__text" }, item),
        ),
      ),
    ),
    h(
      "pre",
      { class: "proto-stream-console", "data-mono": "", "data-testid": "stream-console" },
      streamLines.join("\n"),
    ),
  );
}

/**
 * Candidate Shared Component 6: unifiedDiffView (F5 Results area)
 * Displays structured unified diff hunks for modified files.
 */
export function unifiedDiffView({ hunks = [] } = {}) {
  return h(
    "div",
    { class: "proto-diff-view", "data-testid": "unified-diff-view" },
    ...hunks.map((hunk) =>
      h(
        "div",
        { class: "proto-diff-file", "data-diff-file": hunk.file },
        h(
          "div",
          { class: "proto-diff-file__header" },
          h("code", { class: "proto-diff-file__name", "data-mono": "" }, hunk.file),
          h("span", { class: "proto-diff-file__stats", "data-mono": "" }, hunk.stats),
        ),
        h(
          "div",
          { class: "proto-diff-table", role: "region", "aria-label": hunk.file },
          ...hunk.lines.map((line) =>
            h(
              "div",
              { class: `proto-diff-line proto-diff-line--${line.type}` },
              h(
                "span",
                { class: "proto-diff-line__num", "data-mono": "" },
                line.oldLine != null ? String(line.oldLine) : "",
              ),
              h(
                "span",
                { class: "proto-diff-line__num", "data-mono": "" },
                line.newLine != null ? String(line.newLine) : "",
              ),
              h(
                "span",
                { class: "proto-diff-line__prefix", "data-mono": "" },
                line.type === "add" ? "+" : line.type === "del" ? "-" : " ",
              ),
              h("span", { class: "proto-diff-line__code", "data-mono": "" }, line.text),
            ),
          ),
        ),
      ),
    ),
  );
}

/**
 * Candidate Shared Component 7: deliveryProgressionBar (§10.2, A32)
 * Keeps self-reported, tests passed, PR open, accepted, merged, and deployed
 * visually and semantically distinct.
 */
export function deliveryProgressionBar({
  delivery = {},
  onCreatePr,
  onRecordAccept,
  onRecordMerge,
  onRecordDeploy,
} = {}) {
  return h(
    "div",
    { class: "proto-delivery-bar", "data-testid": "delivery-progression-bar" },
    h("h4", { class: "proto-delivery-bar__title" }, t("proto.delivery.title")),
    h(
      "div",
      { class: "proto-delivery-grid", role: "list" },
      ...DELIVERY_STATES_ORDER.map((stateKey) => {
        const achieved = Boolean(delivery[stateKey]);
        return h(
          "div",
          {
            class: `proto-delivery-step ${achieved ? "proto-delivery-step--achieved" : "proto-delivery-step--pending"}`,
            role: "listitem",
            "data-testid": `delivery-state-${stateKey}`,
            "data-delivery-state": stateKey,
            "data-delivery-achieved": achieved ? "true" : "false",
          },
          h("span", { class: "proto-delivery-step__label" }, t(`proto.delivery.state.${stateKey}`)),
          labeledPill(
            achieved ? "ok" : "neutral",
            achieved ? t("proto.delivery.state_present") : t("proto.delivery.state_pending"),
          ),
        );
      }),
    ),
    h(
      "div",
      { class: "proto-delivery-bar__notice" },
      delivery.pr_open ? t("proto.delivery.pr_created_label") : t("proto.delivery.pr_not_created"),
    ),
    h(
      "div",
      { class: "proto-delivery-bar__actions" },
      !delivery.pr_open
        ? button({
            variant: "primary",
            label: t("proto.delivery.btn_create_pr"),
            "data-testid": "btn-create-pr",
            onClick: onCreatePr,
          })
        : null,
      delivery.pr_open && !delivery.accepted
        ? button({
            variant: "secondary",
            label: t("proto.delivery.btn_record_accept"),
            "data-testid": "btn-record-accept",
            onClick: onRecordAccept,
          })
        : null,
      delivery.pr_open && !delivery.merged
        ? button({
            variant: "secondary",
            label: t("proto.delivery.btn_record_merge"),
            "data-testid": "btn-record-merge",
            onClick: onRecordMerge,
          })
        : null,
      delivery.merged && !delivery.deployed
        ? button({
            variant: "secondary",
            label: t("proto.delivery.btn_record_deploy"),
            "data-testid": "btn-record-deploy",
            onClick: onRecordDeploy,
          })
        : null,
    ),
  );
}

/**
 * Candidate Shared Component 8: pmAssessmentCard (§12, §12.1)
 * Displays PM requirement backfill assessment (passed / failed / insufficient evidence),
 * verified sources, current judgment, this round's recommendation, and owner decision buttons.
 */
export function pmAssessmentCard({
  assessment = "insufficient",
  ownerDecisionRecorded = null,
  onSelectAssessment,
  onDecideAcceptOverview,
  onDecideAcceptDiscussion,
  onDecideKeepReview,
} = {}) {
  const tone =
    assessment === "passed" ? "ok" : assessment === "failed" ? "bad" : "warn";
  const judgmentText =
    assessment === "passed"
      ? t("proto.pm.judgment_passed")
      : assessment === "failed"
        ? t("proto.pm.judgment_failed")
        : t("proto.pm.judgment_insufficient");

  return sectionCard({
    title: t("proto.pm.backfill_title"),
    actions: [
      labeledPill(tone, `${t("proto.pm.assessment_label")}：${t(`proto.pm.assessment.${assessment}`)}`, {
        "data-testid": "pm-assessment-pill",
      }),
    ],
    body: h(
      "div",
      { class: "proto-pm-card", "data-testid": "pm-assessment-card" },
      h("p", { class: "proto-composer__subtitle" }, t("proto.pm.req_bound")),
      h(
        "div",
        { class: "proto-pm-card__switcher" },
        segmented({
          label: t("proto.pm.assessment_label"),
          value: assessment,
          onChange: onSelectAssessment,
          options: [
            { value: "passed", label: t("proto.pm.assessment.passed") },
            { value: "failed", label: t("proto.pm.assessment.failed") },
            { value: "insufficient", label: t("proto.pm.assessment.insufficient") },
          ],
        }),
      ),
      h("p", { class: "proto-pm-card__judgment", "data-testid": "pm-current-judgment" }, judgmentText),
      h("p", { class: "proto-pm-card__recommendation", "data-testid": "pm-recommendation" }, t("proto.pm.recommendation")),
      h(
        "div",
        { class: "proto-pm-card__sources" },
        h("strong", { class: "proto-pm-card__sources-title" }, t("proto.pm.sources_label")),
        h(
          "ul",
          { class: "proto-pm-card__sources-list" },
          h("li", null, t("proto.pm.source_1")),
          h("li", null, t("proto.pm.source_2")),
          h("li", null, t("proto.pm.source_3")),
        ),
      ),
      ownerDecisionRecorded
        ? inlineNotice({
            tone: "good",
            title: t("proto.pm.decision_recorded_banner", { decision: ownerDecisionRecorded }),
            testId: "pm-owner-decision-banner",
          })
        : null,
      h(
        "div",
        { class: "proto-pm-card__decisions", "data-testid": "pm-owner-decisions" },
        h("strong", { class: "proto-pm-card__decisions-title" }, t("proto.pm.decisions_title")),
        h(
          "div",
          { class: "proto-pm-card__decision-buttons" },
          button({
            variant: "primary",
            label: t("proto.pm.decision_accept_return_overview"),
            "data-testid": "decision-accept-overview",
            onClick: onDecideAcceptOverview,
          }),
          button({
            variant: "secondary",
            label: t("proto.pm.decision_accept_return_discussion"),
            "data-testid": "decision-accept-discussion",
            onClick: onDecideAcceptDiscussion,
          }),
          button({
            variant: "ghost",
            label: t("proto.pm.decision_request_followup"),
            "data-testid": "decision-keep-review",
            onClick: onDecideKeepReview,
          }),
        ),
      ),
    ),
  });
}
