export const FIXED_NOW = Date.parse("2026-04-12T14:30:00Z");
export const GOLDEN_CONSTANTS = {
  serverId: "demo-central",
  hostId: "demo-host",
  workspaceId: "ws_00000000000000000000000000000001",
  workspaceSlug: "membership-workspace",
  repository: "example/membership",
  requirementKey: "membership.email_sign_in",
  profileId: "prof_claude_bat_01",
  maintainerProfileId: "maint_profile_01",
  discussionId: "disc_signin_01",
  workId: "wi_signin_double_submit_01",
  attemptId: "att_signin_01",
  managedSessionId: "bat_sess_managed_01",
  managedWorktreeId: "wt_managed_signin_01",
  operationId: "op_00000000000000000000000000000042",
  operationKey: "opk_signin_double_submit_start_01",
  planRevision: "rev_02",
  headSha: "8f4c2a1",
  branchName: "fix/signin-double-submit",
  prNumber: "PR #48",
  manualSessionId: "manual-reset-notes",
  manualCheckpointSha: "4b91e0c",
  manualUncommittedFiles: "scratch.txt, .env.local",
  derivedWorkId: "wi_derived_reset_02",
  derivedIdentity: {
    workId: "wi_derived_reset_02",
    attemptId: "att_reset_02",
    sessionId: "bat_sess_reset_02",
    worktreeId: "wt_managed_reset_02",
    operationKey: "opk_reset_review_start_02",
    operationId: "op_reset_start_02"
  },
  separateIdentity: {
    workId: "wi_cookie_review_03",
    attemptId: "att_cookie_03",
    sessionId: "bat_sess_cookie_03",
    worktreeId: "wt_managed_cookie_03",
    operationKey: "opk_cookie_review_start_03",
    operationId: "op_cookie_start_03"
  },
  hermesPrincipalId: "principal_hermes_mcp",
  hermesControllerId: "ctr_hermes_mcp",
  hermesWorkId: "wi_hermes_mcp_01",
  hermesAttemptId: "att_hermes_01",
  maintFindingId: "lodash-security-alert",
  maintWorkId: "wi_maint_fix_01",
  maintReviewId: "mrev_00000000000000000000000000000001",
  firstSentence: {
    en: "Fix the sign-in button's double submit, add a test, open a PR first.",
    "zh-TW": "修正登入按鈕重複送出，補測試，先開 PR。"
  },
  secondSentence: {
    en: "Add a case that resubmits after a failure.",
    "zh-TW": "再補一個失敗後重新送出的案例。"
  }
};
export const WORK_LIFECYCLE_ORDER = ["accepted", "preparing_worktree", "starting", "running", "waiting_input", "turn_ended", "awaiting_verification"];
export const DELIVERY_STATES_ORDER = ["self_reported", "tests_passed", "pr_open", "accepted", "merged", "deployed"];
export const DIFF_HUNKS = [{
  file: "src/auth/sign_in_button.ts",
  stats: "+19 -4",
  lines: [{
    type: "context",
    oldLine: 18,
    newLine: 18,
    text: "export async function handleSignInSubmit(form: HTMLFormElement) {"
  }, {
    type: "del",
    oldLine: 19,
    newLine: null,
    text: "  const submitBtn = form.querySelector('button[type=\"submit\"]');"
  }, {
    type: "del",
    oldLine: 20,
    newLine: null,
    text: "  return postSession(new FormData(form));"
  }, {
    type: "add",
    oldLine: null,
    newLine: 19,
    text: "  if (form.dataset.submitting === 'true') return;"
  }, {
    type: "add",
    oldLine: null,
    newLine: 20,
    text: "  const submitBtn = form.querySelector<HTMLButtonElement>('button[type=\"submit\"]');"
  }, {
    type: "add",
    oldLine: null,
    newLine: 21,
    text: "  form.dataset.submitting = 'true';"
  }, {
    type: "add",
    oldLine: null,
    newLine: 22,
    text: "  if (submitBtn) { submitBtn.disabled = true; submitBtn.setAttribute('aria-busy', 'true'); }"
  }, {
    type: "add",
    oldLine: null,
    newLine: 23,
    text: "  try {"
  }, {
    type: "add",
    oldLine: null,
    newLine: 24,
    text: "    return await postSession(new FormData(form));"
  }, {
    type: "add",
    oldLine: null,
    newLine: 25,
    text: "  } finally {"
  }, {
    type: "add",
    oldLine: null,
    newLine: 26,
    text: "    delete form.dataset.submitting;"
  }, {
    type: "add",
    oldLine: null,
    newLine: 27,
    text: "    if (submitBtn) { submitBtn.disabled = false; submitBtn.removeAttribute('aria-busy'); }"
  }, {
    type: "add",
    oldLine: null,
    newLine: 28,
    text: "  }"
  }, {
    type: "context",
    oldLine: 21,
    newLine: 29,
    text: "}"
  }]
}, {
  file: "tests/auth/test_sign_in_double_submit.py",
  stats: "+48 -0",
  lines: [{
    type: "add",
    oldLine: null,
    newLine: 1,
    text: "def test_ignores_second_click_while_in_flight(client, sign_in_form):"
  }, {
    type: "add",
    oldLine: null,
    newLine: 2,
    text: "    first = sign_in_form.click_submit(hold_response=True)"
  }, {
    type: "add",
    oldLine: null,
    newLine: 3,
    text: "    second = sign_in_form.click_submit()"
  }, {
    type: "add",
    oldLine: null,
    newLine: 4,
    text: "    assert second is None"
  }, {
    type: "add",
    oldLine: null,
    newLine: 5,
    text: "    assert client.sent_requests == 1"
  }, {
    type: "add",
    oldLine: null,
    newLine: 6,
    text: ""
  }, {
    type: "add",
    oldLine: null,
    newLine: 7,
    text: "def test_allows_resubmit_after_network_failure(client, sign_in_form):"
  }, {
    type: "add",
    oldLine: null,
    newLine: 8,
    text: "    client.QueueStatus(500)"
  }, {
    type: "add",
    oldLine: null,
    newLine: 9,
    text: "    sign_in_form.click_submit()"
  }, {
    type: "add",
    oldLine: null,
    newLine: 10,
    text: "    assert not sign_in_form.submit_button.disabled"
  }, {
    type: "add",
    oldLine: null,
    newLine: 11,
    text: "    client.QueueStatus(200)"
  }, {
    type: "add",
    oldLine: null,
    newLine: 12,
    text: "    retry = sign_in_form.click_submit()"
  }, {
    type: "add",
    oldLine: null,
    newLine: 13,
    text: "    assert retry.status_code == 200"
  }]
}];
export function createScenarioState(scenario = "idea", currentLocale = "en") {
  const firstSentence = GOLDEN_CONSTANTS.firstSentence[currentLocale] || GOLDEN_CONSTANTS.firstSentence.en;
  const secondSentence = GOLDEN_CONSTANTS.secondSentence[currentLocale] || GOLDEN_CONSTANTS.secondSentence.en;
  const base = {
    scenario,
    activeView: "discussions",
    navDrawerOpen: false,
    inspectorDrawerOpen: false,
    // Counters (honest safety evidence)
    modelCalls: 0,
    manualMutations: 0,
    hermesMutations: 0,
    startRequestsSent: 0,
    duplicateStarts: 0,
    // Composer state (§6.2)
    composerIntent: "discuss",
    discussionTab: "context",
    composerDraft: firstSentence,
    noteSavedBanner: false,
    pmAvailable: true,
    pmReplied: false,
    discussionNotes: [],
    // Short plan & Adopt steps (F3/F4, §7.3)
    planVisible: false,
    planAdoptedLater: false,
    adoptMode: "idle",
    // "idle" | "partial_failure" | "completed"
    adoptSteps: [{
      id: "s1",
      status: "pending"
    }, {
      id: "s2",
      status: "pending"
    }, {
      id: "s3",
      status: "pending"
    }, {
      id: "s4",
      status: "pending"
    }, {
      id: "s5",
      status: "pending"
    }, {
      id: "s6",
      status: "pending"
    }, {
      id: "s7",
      status: "pending"
    }, {
      id: "s8",
      status: "pending"
    }],
    // Work page state (F5, §6.3)
    workStarted: false,
    additionalWorks: [],
    workState: "accepted",
    questionAnswered: false,
    nextStepDraft: secondSentence,
    secondSentenceQueued: false,
    queuedMessage: null,
    deliveredMessages: [],
    queueAvailable: true,
    queueFailed: false,
    workOpened: false,
    secondSentenceSteered: false,
    turnInterrupted: false,
    resultsExpanded: {
      files: false,
      diff: false,
      tests: false,
      pr: false
    },
    advancedExpanded: false,
    // Delivery & PM Backfill (§10.2, §12, A32)
    delivery: {
      self_reported: false,
      tests_passed: false,
      pr_open: false,
      accepted: false,
      merged: false,
      deployed: false
    },
    pmAssessment: "insufficient",
    pmReviewOpen: false,
    // "insufficient" | "passed" | "failed"
    ownerDecisionRecorded: null,
    // External Read-Only Session (F6)
    manualActionFeedback: null,
    // "view_original" | "linked" | "derived"

    // External Agent Work - Hermes MCP (F7)
    hermesVisible: false,
    hermesActionFeedback: null,
    // "inbox" | "separate"

    // Disconnect Recovery (§13)
    disconnectedDuringStart: false,
    reconciledAfterDisconnect: false,
    // Maintenance (§12.5)
    maintenanceFixStarted: false,
    maintenanceReviewReturned: false
  };
  if (scenario === "idea") {
    base.activeView = "discussions";
    base.composerIntent = "discuss";
    base.composerDraft = firstSentence;
  } else if (scenario === "planned") {
    base.activeView = "discussions";
    base.composerIntent = "discuss";
    base.composerDraft = firstSentence;
    base.planVisible = true;
    base.modelCalls = 1;
  } else if (["accepted", "preparing_worktree", "starting", "running", "turn_ended"].includes(scenario)) {
    base.activeView = "work_main";
    base.composerIntent = "do";
    base.planVisible = true;
    base.adoptMode = "completed";
    base.adoptSteps = base.adoptSteps.map(s => ({
      ...s,
      status: "done"
    }));
    base.workStarted = true;
    base.workState = scenario;
    base.delivery.self_reported = scenario === "turn_ended";
    base.startRequestsSent = 1;
    base.modelCalls = 2;
  } else if (scenario === "waiting_input") {
    base.activeView = "work_main";
    base.composerIntent = "do";
    base.planVisible = true;
    base.adoptMode = "completed";
    base.adoptSteps = base.adoptSteps.map(s => ({
      ...s,
      status: "done"
    }));
    base.workStarted = true;
    base.workState = "waiting_input";
    base.secondSentenceQueued = true;
    base.queuedMessage = secondSentence;
    base.nextStepDraft = "";
    base.startRequestsSent = 1;
    base.modelCalls = 2;
  } else if (scenario === "awaiting_verification") {
    base.activeView = "work_main";
    base.composerIntent = "do";
    base.planVisible = true;
    base.adoptMode = "completed";
    base.adoptSteps = base.adoptSteps.map(s => ({
      ...s,
      status: "done"
    }));
    base.workStarted = true;
    base.workState = "awaiting_verification";
    base.questionAnswered = true;
    base.secondSentenceQueued = true;
    base.queuedMessage = secondSentence;
    base.nextStepDraft = "";
    base.startRequestsSent = 1;
    base.modelCalls = 3;
    base.delivery.self_reported = true;
    base.delivery.tests_passed = true;
    base.delivery.pr_open = false;
    base.pmAssessment = "insufficient";
    base.resultsExpanded = {
      files: false,
      diff: false,
      tests: false,
      pr: true
    };
  } else if (scenario === "external_readonly") {
    base.activeView = "work_manual";
    base.workStarted = true;
    base.startRequestsSent = 1;
  } else if (scenario === "disconnect_recovery") {
    base.activeView = "work_main";
    base.workStarted = true;
    base.workState = "starting";
    base.disconnectedDuringStart = true;
    base.reconciledAfterDisconnect = false;
    base.startRequestsSent = 1;
    base.duplicateStarts = 0;
    base.advancedExpanded = false;
  } else if (scenario === "maintenance") {
    base.activeView = "maintenance";
    base.maintenanceFixStarted = true;
    base.delivery = {
      self_reported: true,
      tests_passed: true,
      pr_open: true,
      accepted: true,
      merged: true,
      deployed: true
    };
  }
  if (["turn_ended", "awaiting_verification"].includes(scenario)) {
    base.deliveredMessages = [secondSentence];
    base.queuedMessage = null;
    base.secondSentenceQueued = false;
    base.nextStepDraft = "";
  }
  if (base.planVisible) {
    base.discussionNotes = [{
      body: firstSentence,
      sent: true
    }];
    base.composerDraft = "";
    base.pmReplied = true;
  }
  return base;
}
