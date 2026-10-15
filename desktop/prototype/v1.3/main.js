import "./main.css";
import { h, place } from "../../src/ui/dom.js";
import {
  button,
  pageHeader,
  sectionCard,
  statusPill,
  contextChip,
  disclosure,
  inspector,
} from "../../src/ui/components.js";
import { icon } from "../../src/ui/icons.js";
import { compactTime } from "../../src/ui/time.js";
import { timeStamp } from "../../src/ui/stamps.js";
import { viewState } from "../../src/ui/view-state.js";
import { locale, setLocale, t } from "./proto-i18n.js";
import {
  FIXED_NOW,
  GOLDEN_CONSTANTS,
  WORK_LIFECYCLE_ORDER,
  DIFF_HUNKS,
  createScenarioState,
} from "./golden-data.js";
import {
  kvList,
  inlineNotice,
  labeledPill,
  diagnosticStateCard,
  intentComposer,
  shortPlanCard,
  compoundStepList,
  liveStreamOutput,
  unifiedDiffView,
  deliveryProgressionBar,
  pmAssessmentCard,
} from "./candidate-components.js";

let currentTheme = "light";
let currentInspector = null;
let state = createScenarioState(detectInitialScenario(), locale);

function detectInitialScenario() {
  if (typeof window !== "undefined" && window.location) {
    const params = new URLSearchParams(window.location.search);
    const sc = params.get("scene");
    if (
      sc &&
      [
        "idea",
        "planned",
        "running",
        "waiting_input",
        "awaiting_verification",
        "external_readonly",
        "disconnect_recovery",
        "maintenance",
      ].includes(sc)
    ) {
      return sc;
    }
  }
  return "idea";
}

function applyTheme(nextTheme) {
  currentTheme = nextTheme === "dark" ? "dark" : "light";
  if (typeof document !== "undefined" && document.documentElement) {
    document.documentElement.dataset.theme = currentTheme;
  }
}

function switchLocale(nextLocale) {
  const prevFirst = GOLDEN_CONSTANTS.firstSentence[locale];
  const prevSecond = GOLDEN_CONSTANTS.secondSentence[locale];
  setLocale(nextLocale);
  if (state.composerDraft === prevFirst) {
    state.composerDraft = GOLDEN_CONSTANTS.firstSentence[locale];
  }
  if (state.nextStepDraft === prevSecond) {
    state.nextStepDraft = GOLDEN_CONSTANTS.secondSentence[locale];
  }
  renderApp();
}

function switchScenario(nextScenario) {
  state = createScenarioState(nextScenario, locale);
  renderApp();
}

function renderPrototypeBar() {
  const scenarios = [
    { value: "idea", label: t("proto.scene.idea") },
    { value: "planned", label: t("proto.scene.planned") },
    { value: "running", label: t("proto.scene.running") },
    { value: "waiting_input", label: t("proto.scene.waiting_input") },
    { value: "awaiting_verification", label: t("proto.scene.awaiting_verification") },
    { value: "external_readonly", label: t("proto.scene.external_readonly") },
    { value: "disconnect_recovery", label: t("proto.scene.disconnect_recovery") },
    { value: "maintenance", label: t("proto.scene.maintenance") },
  ];

  return h(
    "aside",
    {
      class: "proto-bar",
      "data-testid": "prototype-bar",
      "aria-label": t("proto.bar.badge"),
    },
    h(
      "div",
      { class: "proto-bar__row" },
      h(
        "div",
        { class: "proto-bar__identity" },
        h("span", { class: "proto-bar__badge" }, t("proto.bar.badge")),
        h("span", { class: "proto-bar__notice", "data-testid": "proto-synthetic-notice" }, t("proto.bar.notice")),
      ),
      h(
        "div",
        { class: "proto-bar__controls" },
        h(
          "label",
          { class: "proto-bar__select-label" },
          h("span", null, t("proto.bar.scenario_label")),
          h(
            "select",
            {
              class: "proto-bar__select",
              "data-testid": "scenario-picker",
              value: state.scenario,
              onchange: (e) => switchScenario(e.target.value),
            },
            ...scenarios.map((sc) =>
              h(
                "option",
                { value: sc.value, selected: sc.value === state.scenario ? "" : null },
                sc.label,
              ),
            ),
          ),
        ),
        button({
          variant: "secondary",
          size: "sm",
          label: t("proto.bar.reset"),
          "data-testid": "proto-reset",
          onClick: () => switchScenario(state.scenario),
        }),
        button({
          variant: locale === "en" ? "primary" : "secondary",
          size: "sm",
          label: t("proto.bar.lang_en"),
          "data-testid": "proto-lang-en",
          onClick: () => switchLocale("en"),
        }),
        button({
          variant: locale === "zh-TW" ? "primary" : "secondary",
          size: "sm",
          label: t("proto.bar.lang_zh"),
          "data-testid": "proto-lang-zh",
          onClick: () => switchLocale("zh-TW"),
        }),
        button({
          variant: "secondary",
          size: "sm",
          label: currentTheme === "light" ? t("proto.bar.theme_dark") : t("proto.bar.theme_light"),
          "data-testid": "proto-theme-toggle",
          onClick: () => {
            applyTheme(currentTheme === "light" ? "dark" : "light");
            renderApp();
          },
        }),
        button({
          variant: "ghost",
          size: "sm",
          label: t("proto.bar.hermes_trigger"),
          "data-testid": "proto-trigger-hermes",
          onClick: () => {
            state.hermesVisible = true;
            state.activeView = "work_hermes";
            renderApp();
          },
        }),
      ),
    ),
    h(
      "div",
      { class: "proto-bar__counters", "data-testid": "proto-safety-counters" },
      h(
        "span",
        {
          class: "proto-bar__counter",
          "data-testid": "counter-model-calls",
          "data-count": String(state.modelCalls),
        },
        t("proto.bar.counter_model_calls", { count: state.modelCalls }),
      ),
      h(
        "span",
        {
          class: "proto-bar__counter",
          "data-testid": "counter-manual-mutations",
          "data-count": String(state.manualMutations),
        },
        t("proto.bar.counter_manual_mutations", { count: state.manualMutations }),
      ),
      h(
        "span",
        {
          class: "proto-bar__counter",
          "data-testid": "counter-hermes-mutations",
          "data-count": String(state.hermesMutations),
        },
        t("proto.bar.counter_hermes_mutations", { count: state.hermesMutations }),
      ),
      h(
        "span",
        {
          class: "proto-bar__counter",
          "data-testid": "counter-start-requests",
          "data-count": String(state.startRequestsSent),
        },
        t("proto.bar.counter_start_requests", { count: state.startRequestsSent }),
      ),
      h(
        "span",
        {
          class: "proto-bar__counter",
          "data-testid": "counter-duplicate-starts",
          "data-count": String(state.duplicateStarts),
        },
        t("proto.bar.counter_duplicate_starts", { count: state.duplicateStarts }),
      ),
    ),
  );
}

function renderFixedContextStrip() {
  return h(
    "div",
    { class: "proto-context-strip", "data-testid": "fixed-context-strip" },
    contextChip({
      icon: "server",
      text: `${t("proto.shell.server_label")}: ${GOLDEN_CONSTANTS.serverId}`,
    }),
    contextChip({
      icon: "cpu",
      text: `${t("proto.shell.host_label")}: ${GOLDEN_CONSTANTS.hostId}`,
    }),
    contextChip({
      icon: "folder",
      text: `${t("proto.shell.workspace_label")}: ${t("proto.shell.workspace_name")}`,
    }),
    contextChip({
      icon: "layout-dashboard",
      text: `${t("proto.shell.project_label")}: ${t("proto.shell.project_name")}`,
    }),
    contextChip({
      icon: "list-checks",
      text: `${t("proto.shell.requirement_label")}: ${GOLDEN_CONSTANTS.requirementKey}`,
    }),
    contextChip({
      icon: "wrench",
      text: `${t("proto.shell.profile_label")}: ${t("proto.shell.profile_name")}`,
    }),
    labeledPill("info", t("proto.shell.draft_saved_pill")),
  );
}

function renderSidebar() {
  const navViews = [
    { id: "overview", label: t("proto.nav.overview") },
    { id: "discussions", label: t("proto.nav.discussions") },
    { id: "requirements", label: t("proto.nav.requirements") },
    { id: "board", label: t("proto.nav.board") },
    { id: "planning", label: t("proto.nav.planning") },
    { id: "workspace", label: t("proto.nav.workspace") },
    { id: "governance", label: t("proto.nav.governance") },
    { id: "maintenance", label: t("proto.nav.maintenance") },
  ];

  const workItems = [
    { id: "work_main", label: t("proto.nav.work_main") },
    { id: "work_manual", label: t("proto.nav.work_manual") },
    ...(state.hermesVisible ? [{ id: "work_hermes", label: t("proto.nav.work_hermes") }] : []),
    { id: "work_maint", label: t("proto.nav.work_maint") },
  ];

  const makeNavButton = (item) => {
    const active = state.activeView === item.id;
    return h(
      "button",
      {
        type: "button",
        class: `shell__item ${active ? "is-active" : ""}`,
        "aria-current": active ? "page" : null,
        "data-testid": `nav-item-${item.id}`,
        onclick: () => {
          const vs = viewState("proto-v1.3", GOLDEN_CONSTANTS.workspaceId, item.id);
          vs.lastVisitedAt = FIXED_NOW;
          state.activeView = item.id;
          state.navDrawerOpen = false;
          renderApp();
        },
      },
      h("span", { class: "shell__item-label" }, item.label),
    );
  };

  return h(
    "nav",
    {
      class: "shell__sidebar",
      "data-testid": "shell-sidebar",
      "aria-label": t("proto.shell.nav_menu"),
    },
    h(
      "div",
      { class: "shell__group" },
      h("div", { class: "shell__group-label" }, t("proto.nav.group_views")),
      h("div", { class: "shell__group-items" }, ...navViews.map(makeNavButton)),
    ),
    h(
      "div",
      { class: "shell__group" },
      h("div", { class: "shell__group-label" }, t("proto.nav.group_work")),
      h("div", { class: "shell__group-items" }, ...workItems.map(makeNavButton)),
    ),
  );
}

function handleAdoptAndStart() {
  state.planVisible = true;
  state.adoptMode = "completed";
  state.adoptSteps = state.adoptSteps.map((s) => ({ ...s, status: "done" }));
  if (!state.workStarted) {
    state.workStarted = true;
    state.startRequestsSent += 1;
  }
  state.workState = "running";
}

function renderDiscussionsView() {
  const threadMessages = [
    {
      author: t("proto.f2.msg_author_owner"),
      timeNode: timeStamp(FIXED_NOW - 18 * 60_000, { milliseconds: true, now: FIXED_NOW }),
      body: t("proto.f2.msg_1_body"),
      kind: "message",
    },
    {
      author: t("proto.f2.msg_author_pm"),
      timeNode: timeStamp(FIXED_NOW - 15 * 60_000, { milliseconds: true, now: FIXED_NOW }),
      body: t("proto.f2.msg_2_body"),
      kind: "message",
    },
    ...state.discussionNotes.map((note) => ({
      author: note.author,
      timeNode: h("span", { "data-mono": "" }, compactTime(FIXED_NOW - 2 * 60_000, { locale, now: FIXED_NOW })),
      body: note.body,
      kind: note.kind,
    })),
  ];

  if (state.pmReplied) {
    threadMessages.push({
      author: t("proto.f2.msg_author_pm"),
      timeNode: timeStamp(FIXED_NOW - 60_000, { milliseconds: true, now: FIXED_NOW }),
      body: t("proto.f2.msg_pm_reply"),
      kind: "pm_reply",
    });
  }

  return h(
    "div",
    { class: "proto-view", "data-testid": "view-discussions" },
    pageHeader({
      title: t("proto.f2.title"),
      meta: t("proto.f2.subtitle"),
      pill: labeledPill(
        state.workStarted ? "ok" : "info",
        state.workStarted ? t("proto.work.state.running") : t("proto.f2.state_idea"),
      ),
      actions: [
        button({
          variant: "ghost",
          size: "sm",
          label: state.pmAvailable
            ? t("proto.f2.simulate_pm_unavailable")
            : t("proto.f2.restore_pm_available"),
          "data-testid": "toggle-pm-unavailable",
          onClick: () => {
            state.pmAvailable = !state.pmAvailable;
            renderApp();
          },
        }),
      ],
    }),
    state.ownerDecisionRecorded
      ? inlineNotice({
          tone: "good",
          title: t("proto.pm.decision_recorded_banner", { decision: state.ownerDecisionRecorded }),
          testId: "discussion-decision-banner",
        })
      : null,
    !state.pmAvailable
      ? diagnosticStateCard({
          tone: "warn",
          title: t("proto.f2.simulate_pm_unavailable"),
          whatMissing: t("proto.f2.pm_unavail_what"),
          whatCanDo: t("proto.f2.pm_unavail_here"),
          whereToFix: t("proto.f2.pm_unavail_where"),
          draftStatus: t("proto.f2.pm_unavail_draft"),
          testId: "pm-unavailable-diagnostic",
          actions: [
            button({
              variant: "primary",
              label: t("proto.f2.restore_pm_available"),
              "data-testid": "fix-pm-available",
              onClick: () => {
                state.pmAvailable = true;
                renderApp();
              },
            }),
          ],
        })
      : null,
    sectionCard({
      title: `${t("proto.nav.discussions")} (${GOLDEN_CONSTANTS.discussionId})`,
      body: h(
        "ul",
        { class: "proto-thread-list", "data-testid": "discussion-thread-list" },
        ...threadMessages.map((msg, idx) =>
          h(
            "li",
            {
              class: `proto-thread-msg ${msg.kind === "note" ? "proto-thread-msg--note" : ""}`,
              "data-testid": `thread-msg-${idx + 1}`,
              "data-msg-kind": msg.kind,
            },
            h(
              "div",
              { class: "proto-thread-msg__meta" },
              h("span", null, msg.author),
              msg.timeNode,
            ),
            h("p", { class: "proto-thread-msg__body" }, msg.body),
          ),
        ),
      ),
    }),
    intentComposer({
      intent: state.composerIntent,
      draft: state.composerDraft,
      workStarted: state.workStarted,
      pmAvailable: state.pmAvailable,
      noteSavedBanner: state.noteSavedBanner,
      onIntentChange: (nextIntent) => {
        state.composerIntent = nextIntent;
        state.noteSavedBanner = false;
        renderApp();
      },
      onDraftInput: (val) => {
        state.composerDraft = val;
      },
      onFillFirstSentence: () => {
        state.composerDraft = GOLDEN_CONSTANTS.firstSentence[locale];
        renderApp();
      },
      onFillSecondSentence: () => {
        state.composerDraft = GOLDEN_CONSTANTS.secondSentence[locale];
        renderApp();
      },
      onSaveNoteOnly: () => {
        const text = state.composerDraft.trim() || GOLDEN_CONSTANTS.firstSentence[locale];
        state.discussionNotes.push({
          author: t("proto.f2.msg_author_note"),
          body: text,
          kind: "note",
        });
        state.noteSavedBanner = true;
        // Zero model calls!
        renderApp();
      },
      onSendPm: () => {
        if (!state.pmAvailable) return;
        const text = state.composerDraft.trim() || GOLDEN_CONSTANTS.firstSentence[locale];
        state.discussionNotes.push({
          author: t("proto.f2.msg_author_owner"),
          body: text,
          kind: "message",
        });
        state.modelCalls += 1;
        state.pmReplied = true;
        state.noteSavedBanner = false;
        renderApp();
      },
      onUpdatePlan: () => {
        state.modelCalls += 1;
        state.planVisible = true;
        state.noteSavedBanner = false;
        renderApp();
      },
      onStartWork: () => {
        handleAdoptAndStart();
        state.activeView = "work_main";
        renderApp();
      },
      onSendNextStep: () => {
        state.secondSentenceQueued = true;
        state.secondSentenceSteered = false;
        state.activeView = "work_main";
        renderApp();
      },
    }),
    state.planVisible
      ? shortPlanCard({
          adoptedLater: state.planAdoptedLater,
          adoptMode: state.adoptMode,
          onAdoptLater: () => {
            state.planAdoptedLater = true;
            state.adoptMode = "idle";
            renderApp();
          },
          onAdoptAndStart: () => {
            handleAdoptAndStart();
            renderApp();
          },
          onSimulatePartial: () => {
            state.planAdoptedLater = false;
            state.adoptMode = "partial_failure";
            state.adoptSteps = [
              { id: "s1", status: "done" },
              { id: "s2", status: "done" },
              { id: "s3", status: "done" },
              { id: "s4", status: "failed" },
              { id: "s5", status: "pending" },
              { id: "s6", status: "pending" },
              { id: "s7", status: "pending" },
              { id: "s8", status: "pending" },
            ];
            renderApp();
          },
        })
      : null,
    state.planVisible && (state.adoptMode !== "idle" || state.scenario === "planned")
      ? compoundStepList({
          steps: state.adoptSteps,
          adoptMode: state.adoptMode,
          onContinueUnfinished: () => {
            handleAdoptAndStart();
            renderApp();
          },
          onOpenWork: () => {
            state.activeView = "work_main";
            renderApp();
          },
        })
      : null,
  );
}

function renderWorkMainView() {
  const firstSentenceText =
    locale === "zh-TW"
      ? t("proto.work.turn1_sentence_zh")
      : t("proto.work.turn1_sentence_en");

  const nextStepSubtitle =
    state.workState === "waiting_input"
      ? t("proto.work.header_next_waiting")
      : state.workState === "awaiting_verification"
        ? t("proto.work.header_next")
        : t("proto.work.header_next_running");

  return h(
    "div",
    { class: "proto-view", "data-testid": "view-work-main" },
    pageHeader({
      title: t("proto.nav.work_main"),
      meta: `${GOLDEN_CONSTANTS.workId} · ${GOLDEN_CONSTANTS.requirementKey}`,
      pill: labeledPill(
        state.workState === "awaiting_verification"
          ? "ok"
          : state.workState === "waiting_input"
            ? "warn"
            : "info",
        t(`proto.work.state.${state.workState}`),
      ),
      actions: [
        button({
          variant: "ghost",
          size: "sm",
          label: t("proto.recovery.btn_simulate_disconnect"),
          "data-testid": "simulate-disconnect-btn",
          onClick: () => {
            state.disconnectedDuringStart = true;
            state.reconciledAfterDisconnect = false;
            renderApp();
          },
        }),
      ],
    }),
    state.disconnectedDuringStart
      ? diagnosticStateCard({
          tone: "warn",
          title: t("proto.recovery.status_disconnected"),
          whatMissing: t("proto.recovery.what_missing"),
          whatCanDo: t("proto.recovery.what_can_do"),
          whereToFix: t("proto.recovery.where_fix"),
          draftStatus: t("proto.recovery.draft_kept"),
          testId: "disconnect-recovery-card",
          actions: [
            button({
              variant: "primary",
              label: t("proto.recovery.btn_reconnect"),
              "data-testid": "btn-reconnect-reconcile",
              onClick: () => {
                state.disconnectedDuringStart = false;
                state.reconciledAfterDisconnect = true;
                state.workState = "running";
                renderApp();
              },
            }),
          ],
        })
      : null,
    state.reconciledAfterDisconnect
      ? inlineNotice({
          tone: "good",
          title: t("proto.recovery.reconciled_banner"),
          testId: "reconciled-banner",
        })
      : null,
    h(
      "div",
      { class: "proto-state-stepper", "data-testid": "work-state-stepper" },
      h("strong", null, t("proto.work.state_stepper_label")),
      ...WORK_LIFECYCLE_ORDER.map((st) =>
        button({
          variant: state.workState === st ? "primary" : "ghost",
          size: "sm",
          label: t(`proto.work.state.${st}`),
          "data-testid": `work-state-btn-${st}`,
          "data-work-state": st,
          onClick: () => {
            state.workState = st;
            if (st === "awaiting_verification") {
              state.delivery.self_reported = true;
              state.delivery.tests_passed = true;
            }
            renderApp();
          },
        }),
      ),
    ),
    // Reading Area 1: Header (§6.3)
    sectionCard({
      title: t("proto.work.area1_header"),
      body: h(
        "div",
        { "data-testid": "work-area-header" },
        h("p", { class: "proto-composer__subtitle" }, nextStepSubtitle),
        kvList([
          { label: t("proto.plan.field_goal"), value: t("proto.work.header_goal") },
          { label: t("proto.work.state_label"), value: t(`proto.work.state.${state.workState}`) },
          { label: t("proto.shell.profile_label"), value: t("proto.work.header_runtime") },
          { label: t("proto.shell.host_label"), value: t("proto.work.header_controller") },
        ]),
      ),
    }),
    // Reading Area 2: Main (§6.3)
    sectionCard({
      title: t("proto.work.area2_main"),
      body: h(
        "div",
        { class: "proto-work-main-content", "data-testid": "work-area-main" },
        h(
          "div",
          { class: "proto-thread-msg", "data-testid": "work-turn1-message" },
          h(
            "div",
            { class: "proto-thread-msg__meta" },
            h("span", null, t("proto.work.turn1_label")),
            h("span", { "data-mono": "" }, GOLDEN_CONSTANTS.managedSessionId),
          ),
          h("p", { class: "proto-thread-msg__body" }, firstSentenceText),
        ),
        liveStreamOutput({
          tools: [
            t("proto.work.tool_1"),
            t("proto.work.tool_2"),
            t("proto.work.tool_3"),
            t("proto.work.tool_4"),
          ],
          streamLines: [
            t("proto.work.stream_line_1"),
            t("proto.work.stream_line_2"),
            t("proto.work.stream_line_3"),
          ],
        }),
        h(
          "div",
          {
            class: "proto-question-box",
            "data-testid": "work-question-card",
            "data-question-answered": state.questionAnswered ? "true" : "false",
          },
          h("strong", null, t("proto.work.question_card_title")),
          h("p", { class: "proto-thread-msg__body" }, t("proto.work.question_text")),
          state.questionAnswered
            ? inlineNotice({
                tone: "good",
                title: t("proto.work.question_answered"),
                testId: "question-answered-notice",
              })
            : h(
                "div",
                { class: "proto-question-box__actions" },
                button({
                  variant: "primary",
                  size: "sm",
                  label: t("proto.work.question_opt_keep"),
                  "data-testid": "answer-question-keep",
                  onClick: () => {
                    state.questionAnswered = true;
                    if (state.workState === "waiting_input") {
                      state.workState = "awaiting_verification";
                      state.delivery.self_reported = true;
                      state.delivery.tests_passed = true;
                    }
                    renderApp();
                  },
                }),
                button({
                  variant: "secondary",
                  size: "sm",
                  label: t("proto.work.question_opt_clear"),
                  "data-testid": "answer-question-clear",
                  onClick: () => {
                    state.questionAnswered = true;
                    renderApp();
                  },
                }),
              ),
        ),
        h(
          "div",
          {
            class: "proto-queue-box",
            "data-testid": "work-queue-box",
            "data-second-queued": state.secondSentenceQueued ? "true" : "false",
            "data-second-steered": state.secondSentenceSteered ? "true" : "false",
          },
          h("strong", null, t("proto.work.queue_box_title")),
          h("p", { class: "proto-composer__subtitle" }, t("proto.work.queue_policy_note")),
          h(
            "label",
            { class: "proto-composer__label" },
            h("span", { class: "sr-only" }, t("proto.work.queue_box_title")),
            h(
              "textarea",
              {
                class: "proto-composer__textarea",
                "data-testid": "second-sentence-input",
                rows: "2",
                oninput: (e) => {
                  state.nextStepDraft = e.target.value;
                },
              },
              state.nextStepDraft,
            ),
          ),
          h(
            "div",
            { class: "proto-queue-box__actions" },
            button({
              variant: "primary",
              label: t("proto.work.btn_queue_second"),
              "data-testid": "btn-queue-second-sentence",
              onClick: () => {
                state.secondSentenceQueued = true;
                state.secondSentenceSteered = false;
                renderApp();
              },
            }),
            button({
              variant: "secondary",
              label: t("proto.work.btn_interrupt"),
              "data-testid": "btn-interrupt-turn",
              onClick: () => {
                state.turnInterrupted = true;
                renderApp();
              },
            }),
          ),
          state.secondSentenceQueued
            ? h(
                "div",
                {
                  class: "proto-thread-msg",
                  "data-testid": "queued-second-sentence-receipt",
                  "data-queue-policy": "after_current",
                  "data-steered": "false",
                },
                h(
                  "div",
                  { class: "proto-thread-msg__meta" },
                  labeledPill("info", t("proto.work.queued_receipt_badge")),
                  h("span", { "data-mono": "" }, "msg_q_02"),
                ),
                h("p", { class: "proto-thread-msg__body" }, state.nextStepDraft),
                h("p", { class: "proto-composer__subtitle" }, t("proto.work.queued_receipt_detail")),
              )
            : null,
          state.turnInterrupted
            ? inlineNotice({
                tone: "warn",
                title: t("proto.work.interrupt_notice"),
                testId: "interrupt-notice",
              })
            : null,
        ),
      ),
    }),
    // Reading Area 3: Results (expand on demand, §6.3, §10.2)
    sectionCard({
      title: t("proto.work.area3_results"),
      body: h(
        "div",
        { class: "proto-work-results", "data-testid": "work-area-results" },
        disclosure({
          label: t("proto.work.results_files_summary"),
          open: state.resultsExpanded.files,
          key: "proto-results-files",
          body: kvList([
            {
              label: "src/auth/sign_in_button.ts",
              value: t("proto.work.file_1_desc"),
              mono: true,
            },
            {
              label: "tests/auth/test_sign_in_double_submit.py",
              value: t("proto.work.file_2_desc"),
              mono: true,
            },
          ]),
        }),
        disclosure({
          label: t("proto.work.results_diff_summary"),
          open: state.resultsExpanded.diff,
          key: "proto-results-diff",
          body: unifiedDiffView({ hunks: DIFF_HUNKS }),
        }),
        disclosure({
          label: t("proto.work.results_tests_summary"),
          open: state.resultsExpanded.tests,
          key: "proto-results-tests",
          body: kvList([
            {
              label: t("proto.work.test_cmd_label"),
              value: "pytest -q tests/auth/test_sign_in_double_submit.py",
              mono: true,
            },
            {
              label: t("proto.delivery.state.tests_passed"),
              value: t("proto.work.test_output_summary"),
            },
          ]),
        }),
        disclosure({
          label: t("proto.work.results_pr_summary"),
          open: true,
          key: "proto-results-pr",
          body: deliveryProgressionBar({
            delivery: state.delivery,
            onCreatePr: () => {
              state.delivery.self_reported = true;
              state.delivery.tests_passed = true;
              state.delivery.pr_open = true;
              state.pmAssessment = "passed";
              renderApp();
            },
            onRecordAccept: () => {
              state.delivery.accepted = true;
              renderApp();
            },
            onRecordMerge: () => {
              state.delivery.merged = true;
              renderApp();
            },
            onRecordDeploy: () => {
              state.delivery.deployed = true;
              renderApp();
            },
          }),
        }),
        pmAssessmentCard({
          assessment: state.pmAssessment,
          ownerDecisionRecorded: state.ownerDecisionRecorded,
          onSelectAssessment: (nextAss) => {
            state.pmAssessment = nextAss;
            renderApp();
          },
          onDecideAcceptOverview: () => {
            state.delivery.accepted = true;
            state.ownerDecisionRecorded = t("proto.pm.decision_accept_return_overview");
            state.activeView = "overview";
            renderApp();
          },
          onDecideAcceptDiscussion: () => {
            state.delivery.accepted = true;
            state.ownerDecisionRecorded = t("proto.pm.decision_accept_return_discussion");
            state.activeView = "discussions";
            renderApp();
          },
          onDecideKeepReview: () => {
            state.ownerDecisionRecorded = t("proto.pm.decision_request_followup");
            state.activeView = "discussions";
            renderApp();
          },
        }),
      ),
    }),
    // Reading Area 4: Advanced (collapsed by default, §6.3)
    h(
      "div",
      { "data-testid": "work-area-advanced" },
      disclosure({
        label: t("proto.work.area4_advanced"),
        open: state.advancedExpanded,
        body: h(
          "div",
          { class: "proto-advanced-body" },
          kvList([
            { label: t("proto.work.adv_work_id"), value: GOLDEN_CONSTANTS.workId, mono: true },
            { label: t("proto.work.adv_attempt_id"), value: GOLDEN_CONSTANTS.attemptId, mono: true },
            { label: t("proto.work.adv_session_id"), value: GOLDEN_CONSTANTS.managedSessionId, mono: true },
            { label: t("proto.work.adv_worktree_id"), value: GOLDEN_CONSTANTS.managedWorktreeId, mono: true },
            { label: t("proto.work.adv_op_key"), value: GOLDEN_CONSTANTS.operationKey, mono: true },
            { label: t("proto.work.adv_plan_rev"), value: GOLDEN_CONSTANTS.planRevision, mono: true },
            { label: t("proto.work.adv_head_sha"), value: GOLDEN_CONSTANTS.headSha, mono: true },
          ]),
          h(
            "pre",
            { class: "proto-stream-console", "data-mono": "", "data-testid": "advanced-raw-events" },
            [
              `op_id: ${GOLDEN_CONSTANTS.operationId}`,
              `op_key: ${GOLDEN_CONSTANTS.operationKey}`,
              `work_id: ${GOLDEN_CONSTANTS.workId}`,
              `attempt_id: ${GOLDEN_CONSTANTS.attemptId}`,
              `queue_receipt: msg_q_02 (policy=after_current, steered=false)`,
            ].join("\n"),
          ),
        ),
      }),
    ),
  );
}

function renderOverviewView() {
  return h(
    "div",
    { class: "proto-view", "data-testid": "view-overview" },
    pageHeader({
      title: t("proto.overview.title"),
      meta: t("proto.overview.subtitle"),
      pill: statusPill(state.delivery.accepted ? "accepted" : "in_review"),
      actions: [
        button({
          variant: "secondary",
          size: "sm",
          label: t("proto.overview.open_work_btn"),
          "data-testid": "overview-open-work",
          onClick: () => {
            state.activeView = "work_main";
            renderApp();
          },
        }),
        button({
          variant: "secondary",
          size: "sm",
          label: t("proto.overview.open_discussion_btn"),
          "data-testid": "overview-open-discussion",
          onClick: () => {
            state.activeView = "discussions";
            renderApp();
          },
        }),
      ],
    }),
    deliveryProgressionBar({
      delivery: state.delivery,
      onCreatePr: () => {
        state.delivery.pr_open = true;
        state.pmAssessment = "passed";
        renderApp();
      },
      onRecordAccept: () => {
        state.delivery.accepted = true;
        renderApp();
      },
      onRecordMerge: () => {
        state.delivery.merged = true;
        renderApp();
      },
      onRecordDeploy: () => {
        state.delivery.deployed = true;
        renderApp();
      },
    }),
    pmAssessmentCard({
      assessment: state.pmAssessment,
      ownerDecisionRecorded: state.ownerDecisionRecorded,
      onSelectAssessment: (nextAss) => {
        state.pmAssessment = nextAss;
        renderApp();
      },
      onDecideAcceptOverview: () => {
        state.delivery.accepted = true;
        state.ownerDecisionRecorded = t("proto.pm.decision_accept_return_overview");
        renderApp();
      },
      onDecideAcceptDiscussion: () => {
        state.delivery.accepted = true;
        state.ownerDecisionRecorded = t("proto.pm.decision_accept_return_discussion");
        state.activeView = "discussions";
        renderApp();
      },
      onDecideKeepReview: () => {
        state.ownerDecisionRecorded = t("proto.pm.decision_request_followup");
        state.activeView = "discussions";
        renderApp();
      },
    }),
  );
}

function renderExternalManualView() {
  return h(
    "div",
    {
      class: "proto-view",
      "data-testid": "view-external-manual",
      "data-session-readonly": "true",
    },
    pageHeader({
      title: t("proto.f6.title"),
      meta: t("proto.f6.subtitle"),
      pill: statusPill("read_only"),
    }),
    inlineNotice({
      tone: "info",
      title: t("proto.f6.policy_banner"),
      testId: "manual-readonly-banner",
    }),
    sectionCard({
      title: t("proto.f6.transcript_title"),
      body: h(
        "div",
        null,
        h("p", { class: "proto-composer__subtitle", "data-mono": "" }, t("proto.f6.checkpoint_subtitle")),
        h(
          "ul",
          { class: "proto-thread-list" },
          h(
            "li",
            { class: "proto-thread-msg" },
            h("p", { class: "proto-thread-msg__body", "data-mono": "" }, t("proto.f6.msg_1")),
          ),
          h(
            "li",
            { class: "proto-thread-msg" },
            h("p", { class: "proto-thread-msg__body", "data-mono": "" }, t("proto.f6.msg_2")),
          ),
        ),
      ),
    }),
    sectionCard({
      title: t("proto.f6.actions_title"),
      body: h(
        "div",
        null,
        h(
          "div",
          { class: "proto-plan-card__actions", "data-testid": "manual-session-actions" },
          button({
            variant: "secondary",
            label: t("proto.f6.btn_view_original"),
            "data-testid": "manual-view-original",
            onClick: () => {
              state.manualActionFeedback = "view_original";
              renderApp();
            },
          }),
          button({
            variant: "secondary",
            label: t("proto.f6.btn_link_existing"),
            "data-testid": "manual-link-existing",
            onClick: () => {
              state.manualActionFeedback = "linked";
              renderApp();
            },
          }),
          button({
            variant: "primary",
            label: t("proto.f6.btn_derive_new"),
            "data-testid": "manual-derive-new",
            onClick: () => {
              state.manualActionFeedback = "derived";
              renderApp();
            },
          }),
        ),
        state.manualActionFeedback === "view_original"
          ? inlineNotice({
              tone: "info",
              title: t("proto.f6.view_original_feedback"),
              testId: "manual-view-original-notice",
            })
          : null,
        state.manualActionFeedback === "linked"
          ? inlineNotice({
              tone: "good",
              title: t("proto.f6.linked_feedback"),
              testId: "manual-linked-notice",
            })
          : null,
        state.manualActionFeedback === "derived"
          ? inlineNotice({
              tone: "good",
              title: t("proto.f6.derived_feedback"),
              testId: "manual-derived-notice",
            })
          : null,
      ),
    }),
  );
}

function renderHermesWorkView() {
  return h(
    "div",
    {
      class: "proto-view",
      "data-testid": "view-hermes-work",
      "data-external-controller": "Hermes",
    },
    pageHeader({
      title: t("proto.f7.title"),
      meta: t("proto.f7.live_arrived"),
      pill: labeledPill("info", t("proto.f7.badge")),
    }),
    sectionCard({
      title: GOLDEN_CONSTANTS.hermesWorkId,
      body: h(
        "div",
        null,
        kvList([
          { label: t("proto.f7.label_requester"), value: t("proto.f7.requester") },
          { label: t("proto.f7.label_controller"), value: t("proto.f7.controller") },
          { label: t("proto.f7.label_runtime"), value: t("proto.f7.runtime") },
          { label: t("proto.f7.label_attempt"), value: t("proto.f7.attempt") },
        ]),
        h("p", { class: "proto-pm-card__judgment" }, t("proto.f7.policy_note")),
        h(
          "div",
          { class: "proto-plan-card__actions", "data-testid": "hermes-actions" },
          button({
            variant: "primary",
            label: t("proto.f7.btn_send_inbox"),
            "data-testid": "hermes-send-inbox",
            onClick: () => {
              state.hermesActionFeedback = "inbox";
              renderApp();
            },
          }),
          button({
            variant: "secondary",
            label: t("proto.f7.btn_start_separate"),
            "data-testid": "hermes-start-separate",
            onClick: () => {
              state.hermesActionFeedback = "separate";
              renderApp();
            },
          }),
        ),
        state.hermesActionFeedback === "inbox"
          ? inlineNotice({
              tone: "good",
              title: t("proto.f7.inbox_sent_feedback"),
              testId: "hermes-inbox-feedback",
            })
          : null,
        state.hermesActionFeedback === "separate"
          ? inlineNotice({
              tone: "info",
              title: t("proto.f7.separate_work_feedback"),
              testId: "hermes-separate-feedback",
            })
          : null,
      ),
    }),
  );
}

function renderMaintenanceView() {
  return h(
    "div",
    { class: "proto-view", "data-testid": "view-maintenance" },
    pageHeader({
      title: t("proto.maint.title"),
      meta: GOLDEN_CONSTANTS.maintReviewId,
      pill: labeledPill(
        state.maintenanceReviewReturned ? "ok" : "warn",
        t("proto.maint.release_badge"),
      ),
    }),
    sectionCard({
      title: t("proto.maint.finding_title"),
      actions: [
        labeledPill("info", t("proto.maint.profile_badge")),
        labeledPill("ok", t("proto.maint.work_id_badge")),
      ],
      body: h(
        "div",
        null,
        h("p", { class: "proto-composer__subtitle" }, t("proto.maint.finding_detail")),
        kvList([
          { label: t("proto.work.adv_work_id"), value: GOLDEN_CONSTANTS.maintWorkId, mono: true },
          { label: t("proto.shell.profile_label"), value: t("proto.shell.profile_maintainer") },
          { label: t("proto.shell.requirement_label"), value: GOLDEN_CONSTANTS.requirementKey, mono: true },
        ]),
        h(
          "div",
          { class: "proto-plan-card__actions" },
          button({
            variant: "secondary",
            label: t("proto.maint.btn_start_fix"),
            "data-testid": "maint-start-fix",
            onClick: () => {
              state.maintenanceFixStarted = true;
              renderApp();
            },
          }),
          button({
            variant: "primary",
            label: t("proto.maint.btn_return_review"),
            "data-testid": "maint-return-review",
            onClick: () => {
              state.maintenanceReviewReturned = true;
              renderApp();
            },
          }),
        ),
        state.maintenanceReviewReturned
          ? inlineNotice({
              tone: "good",
              title: t("proto.maint.review_returned_banner"),
              testId: "maint-review-returned-banner",
            })
          : null,
      ),
    }),
  );
}

function renderPlaceholderView(viewId) {
  const viewTitleMap = {
    requirements: t("proto.nav.requirements"),
    board: t("proto.nav.board"),
    planning: t("proto.nav.planning"),
    workspace: t("proto.nav.workspace"),
    governance: t("proto.nav.governance"),
  };
  const viewTitle = viewTitleMap[viewId] || viewId;

  return h(
    "div",
    { class: "proto-view", "data-testid": `view-placeholder-${viewId}` },
    pageHeader({
      title: viewTitle,
      meta: t("proto.placeholder.title", { view: viewTitle }),
      pill: labeledPill("neutral", t("proto.placeholder.badge")),
    }),
    sectionCard({
      title: t("proto.placeholder.title", { view: viewTitle }),
      body: h(
        "div",
        null,
        h("p", null, t("proto.placeholder.body")),
        h(
          "p",
          { "data-testid": "preserved-draft-indicator" },
          state.composerDraft
            ? t("proto.placeholder.draft_status", { draft: state.composerDraft })
            : t("proto.placeholder.draft_empty"),
        ),
        h(
          "div",
          { class: "proto-plan-card__actions" },
          button({
            variant: "primary",
            label: t("proto.placeholder.return_discussions"),
            "data-testid": "placeholder-return-discussions",
            onClick: () => {
              state.activeView = "discussions";
              renderApp();
            },
          }),
          button({
            variant: "secondary",
            label: t("proto.placeholder.return_work"),
            "data-testid": "placeholder-return-work",
            onClick: () => {
              state.activeView = "work_main";
              renderApp();
            },
          }),
        ),
      ),
    }),
  );
}

function renderActiveCanvas() {
  switch (state.activeView) {
    case "discussions":
      return renderDiscussionsView();
    case "work_main":
      return renderWorkMainView();
    case "overview":
      return renderOverviewView();
    case "work_manual":
      return renderExternalManualView();
    case "work_hermes":
      return renderHermesWorkView();
    case "maintenance":
    case "work_maint":
      return renderMaintenanceView();
    default:
      return renderPlaceholderView(state.activeView);
  }
}

function renderInspectorPane() {
  if (currentInspector) {
    currentInspector.dispose();
  }
  const insp = inspector({
    title: t("proto.inspector.title"),
    dock: true,
    onClose: () => {
      state.inspectorDrawerOpen = false;
    },
  });
  insp.setAttribute("data-testid", "context-inspector");
  place(
    insp.body,
    "replaceChildren",
    sectionCard({
      title: t("proto.inspector.sec_context"),
      body: kvList([
        { label: t("proto.shell.server_label"), value: GOLDEN_CONSTANTS.serverId, mono: true },
        { label: t("proto.shell.host_label"), value: GOLDEN_CONSTANTS.hostId, mono: true },
        { label: t("proto.shell.workspace_label"), value: GOLDEN_CONSTANTS.workspaceSlug, mono: true },
        { label: t("proto.shell.repo_label"), value: GOLDEN_CONSTANTS.repository, mono: true },
        { label: t("proto.shell.requirement_label"), value: GOLDEN_CONSTANTS.requirementKey, mono: true },
      ]),
    }),
    sectionCard({
      title: t("proto.inspector.sec_safety"),
      body: kvList([
        { label: t("proto.inspector.authority_label"), value: t("proto.inspector.authority_val") },
        { label: t("proto.inspector.manual_policy_label"), value: t("proto.inspector.manual_policy_val") },
        { label: t("proto.inspector.hermes_policy_label"), value: t("proto.inspector.hermes_policy_val") },
      ]),
    }),
    sectionCard({
      title: t("proto.inspector.sec_evidence"),
      body: kvList([
        { label: t("proto.work.adv_work_id"), value: GOLDEN_CONSTANTS.workId, mono: true },
        { label: t("proto.work.adv_op_key"), value: GOLDEN_CONSTANTS.operationKey, mono: true },
        { label: t("proto.work.adv_head_sha"), value: GOLDEN_CONSTANTS.headSha, mono: true },
      ]),
    }),
  );
  currentInspector = insp;
  if (state.inspectorDrawerOpen) {
    queueMicrotask(() => {
      if (currentInspector === insp) {
        insp.open();
        for (const el of document.querySelectorAll('textarea[style=""]')) {
          el.removeAttribute("style");
        }
      }
    });
  }
  return insp;
}

export function renderApp() {
  const root = document.getElementById("app");
  if (!root) return;

  const shellEl = h(
    "div",
    {
      class: "shell",
      "data-nav-open": state.navDrawerOpen ? "true" : "false",
      "data-testid": "product-shell",
    },
    h(
      "header",
      { class: "shell__topbar" },
      h(
        "button",
        {
          type: "button",
          class: "shell__nav-toggle",
          "data-testid": "nav-drawer-toggle",
          "aria-label": t("proto.shell.nav_menu"),
          "aria-expanded": state.navDrawerOpen ? "true" : "false",
          onclick: () => {
            state.navDrawerOpen = !state.navDrawerOpen;
            renderApp();
          },
        },
        icon("menu", { size: 16 }),
      ),
      h(
        "div",
        { class: "shell__brand" },
        h("span", { class: "shell__brand-mark" }),
        h("span", { class: "shell__brand-title" }, t("proto.shell.brand")),
      ),
      h("div", { class: "shell__spacer" }),
      h(
        "div",
        { class: "shell__utilities" },
        button({
          variant: "secondary",
          size: "sm",
          label: t("proto.shell.inspector_toggle"),
          "data-testid": "inspector-drawer-toggle",
          onClick: () => {
            state.inspectorDrawerOpen = !state.inspectorDrawerOpen;
            if (state.inspectorDrawerOpen && currentInspector) {
              currentInspector.open();
            } else if (!state.inspectorDrawerOpen && currentInspector) {
              currentInspector.close();
            }
            for (const el of document.querySelectorAll('textarea[style=""]')) {
              el.removeAttribute("style");
            }
          },
        }),
      ),
    ),
    h(
      "div",
      { class: "shell__body" },
      renderSidebar(),
      h("div", {
        class: "shell__backdrop",
        "data-testid": "nav-drawer-backdrop",
        onclick: () => {
          state.navDrawerOpen = false;
          renderApp();
        },
      }),
      h(
        "main",
        { class: "shell__workspace" },
        renderFixedContextStrip(),
        h(
          "div",
          { class: "proto-workspace-grid" },
          h("div", { class: "proto-canvas", "data-testid": "proto-canvas" }, renderActiveCanvas()),
          renderInspectorPane(),
        ),
      ),
    ),
  );

  place(root, "replaceChildren", renderPrototypeBar(), shellEl);
}

applyTheme("light");
renderApp();
