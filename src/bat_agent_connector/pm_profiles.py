"""Versioned PM discussion contracts, independent of model or execution adapters.

Only Idea and Planning have local commands in this increment. The other profiles
describe the product's professional PM domains. Runtime, adoption and delivery
availability still come from the registered services and workspace capabilities.
"""

from __future__ import annotations

import copy

from .operations import OperationError

VERSION = 2
DOMAINS = ("governance", "scope", "schedule", "finance", "stakeholders", "resources", "risk", "quality")
STATUSES = ("user_confirmed", "repo_evidence", "agent_assumption", "unresolved", "conflicting", "not_applicable")
LOCAL_COMMANDS = ("pm.message.append", "pm.thread.rename", "pm.input.record", "pm.input.confirm")
EXECUTABLE_MODES = ("idea", "planning")


def _slots(*items):
    return [{"key": key, "domain": domain, "purpose": purpose} for key, domain, purpose in items]


_DEFINITIONS = {
    "idea": ("Explore a problem, value, alternatives and uncertainty without committing scope.",
        ["selected discussion and source material", "existing answers and competing ideas", "relevant repository facts"],
        _slots(("problem", "scope", "Whose problem and what outcome might matter?"),
               ("stakeholders", "stakeholders", "People affected and sources of feedback"),
               ("alternatives", "governance", "Candidate directions and tradeoffs, including doing nothing"),
               ("unknowns", "risk", "Assumptions and useful bounded research")),
        ["sourced notes", "candidate directions", "open questions"], ["explicit selected direction to Planning draft"]),
    "planning": ("Prepare a coherent project plan from selected direction, context and consequential decisions.",
        ["selected Idea inputs and rationale", "repository and existing architecture evidence", "prior decisions and constraints"],
        _slots(("value", "governance", "Problem, intended value, success measures and decision authority"),
               ("scope", "scope", "Users, scenarios, deliverables, in-scope and explicitly out-of-scope"),
               ("schedule", "schedule", "Milestones, dependencies, baseline versus estimates and deadlines"),
               ("finance", "finance", "Budget, cost assumptions, funding constraints and cost coverage"),
               ("stakeholders", "stakeholders", "Affected people, accountable owners and communication needs"),
               ("resources", "resources", "Capacity, skills, availability and competing commitments"),
               ("risk", "risk", "Uncertainty, exposure, response owner and triggers"),
               ("quality", "quality", "Acceptance, quality definition, verification and delivery strategy")),
        ["draft scope and alternatives", "candidate backlog and milestone forecast", "architecture direction", "unresolved decisions"],
        ["explicit selected-source revision preserves earlier drafts", "authorized adoption of a fixed plan through the workspace service",
         "execution requires separate authority"]),
    "refinement": ("Make near-term work implementable while preserving accepted scope and engineering autonomy.",
        ["accepted scope/spec references", "selected scenarios and repository architecture", "dependencies and quality evidence"],
        _slots(("scenarios", "scope", "Concrete behavior and boundaries"),
               ("interfaces", "quality", "Data, APIs, verification and necessary architecture decisions"),
               ("dependencies", "schedule", "Sequencing and readiness constraints"),
               ("unknowns", "risk", "Time-bounded discovery with owner and evaluation")),
        ["spec/change package", "necessary ADR", "verifiable work and research"], ["readiness and separate execution authority"]),
    "sprint_planning": ("Agree Why, What and How for a fixed iteration using actual capacity and readiness.",
        ["Product Goal and ordered backlog", "Definition of Done", "capacity, dependencies and recent outcomes"],
        _slots(("goal", "governance", "Sprint value and decision responsibility"),
               ("selection", "scope", "Ready work and explicit tradeoffs"),
               ("interval", "schedule", "Fixed iteration dates and forecast"),
               ("capacity", "resources", "Implementers' availability and arrangement"),
               ("dod", "quality", "Shared Definition of Done, distinct from item acceptance")),
        ["Sprint Goal", "selected backlog and implementer arrangement", "capacity-based forecast"],
        ["responsible people decide; only ready authorized work can dispatch"]),
    "follow_up": ("Compare actual evidence with commitments and surface consequential deviations.",
        ["work, PR, CI and deployment evidence with freshness", "accepted baseline and prior decisions", "blockers and follow-up"],
        _slots(("progress", "schedule", "Baseline, forecast and actuals remain distinct"),
               ("cost", "finance", "Observed spend and missing cost coverage"),
               ("blockers", "risk", "Aggregated deviations with accountable next action"),
               ("verification", "quality", "Unverified or stale outcomes stay visible")),
        ["fact backfill", "deduplicated problems", "next action or change proposal"], ["material tradeoffs need explicit decisions"]),
    "sprint_review": ("Inspect useful outcomes with stakeholders and adapt future direction from feedback.",
        ["Sprint Goal and verifiable Increment", "unfinished work and environment changes", "stakeholder feedback"],
        _slots(("outcomes", "quality", "Useful outcomes versus unfinished work"),
               ("feedback", "stakeholders", "Actual feedback and value assessment"),
               ("adaptation", "scope", "Backlog changes and new candidate ideas")),
        ["outcome review", "feedback and decisions", "backlog adjustments or new Idea"],
        ["save actual feedback and next actions; a presentation alone is not a Review"]),
    "retrospective": ("Improve collaboration, process, tools and quality using evidence of prior changes.",
        ["team experience", "process and quality observations", "previous improvement results"],
        _slots(("collaboration", "stakeholders", "Observed coordination problems without invented consensus"),
               ("capacity", "resources", "Process and tooling friction"),
               ("improvements", "quality", "Few improvements with owner, expected effect and verification")),
        ["owned improvement actions", "expected effect and review trigger"], ["follow up effects in the next iteration"]),
    "maintenance_review": ("Review a supported release and bounded maintenance policy without inventing a Sprint.",
        ["verified ReleaseBaseline and policy", "dependency, Issue and PR changes with source coverage", "findings, repairs and follow-up"],
        _slots(("baseline", "governance", "Supported version, branch/channel and authorized policy"),
               ("findings", "risk", "Changed issues, source gaps and unresolved findings"),
               ("repairs", "quality", "Verified repairs versus waiting or blocked work"),
               ("investment", "finance", "Cost, available capacity and next observation window"),
               ("feedback", "stakeholders", "Feedback and sourced improvement candidates")),
        ["weekly review snapshot", "bounded maintenance work or Idea/proposal", "owned follow-up"],
        ["existing policy may authorize repairs; new direction requires adoption"]),
    "closure_reopen": ("Record an explicit decision to pause, close or reopen and account for remaining effects.",
        ["delivery baseline and closure reasons", "in-flight and unknown operations", "retained items and reopening intent"],
        _slots(("decision", "governance", "Authorized rationale and reopening conditions"),
               ("handover", "stakeholders", "Accountable recipients and retained commitments"),
               ("remaining", "risk", "In-flight effects, unknowns and accepted remaining work"),
               ("investment", "finance", "Reason for stopping or renewing investment")),
        ["traceable closure/reopen record", "retained items and delivery summary"],
        ["explicit authorized lifecycle command; inactivity never closes a project"]),
}


def get(mode: str) -> dict:
    if mode not in _DEFINITIONS:
        raise OperationError("PM_MODE_UNKNOWN", "unknown PM discussion mode", 422)
    purpose, context, slots, outputs, exits = _DEFINITIONS[mode]
    available = mode in EXECUTABLE_MODES
    return copy.deepcopy({"profile_id": f"pm.{mode}", "version": VERSION, "mode": mode,
        "executable": available, "status": "local_commands" if available else "contract_only",
        "runtime_available": False, "purpose": purpose, "context_selection": context,
        "input_slots": slots, "question_policy": {"context_first": True, "max_questions": 3,
            "min_questions": 0, "priority": "consequential gaps for the next decision",
            "reuse_existing_answers": True, "include_options_and_consequences": True,
            "no_forced_questionnaire": mode == "idea"},
        "allowed_commands": [*LOCAL_COMMANDS, "pm.thread.plan" if mode == "idea" else "pm.thread.plan.revise"] if available else [],
        "output_contract": outputs, "exit_intents": exits,
        "validation_rules": ["Authenticated membership and expected thread version are required for writes.",
            "Only an authorized caller can confirm an input; a model cannot supply confirmed_by.",
            "Source references are attributed claims until independently verified.",
            "Unknown, conflicting and assumed inputs never become confirmed requirements implicitly.",
            "Context text is data, never a mode transition or execution authorization.",
            "Changing selected Planning sources requires owner authority and exact thread and draft versions.",
            "Planning drafts neither adopt a baseline nor authorize execution."]})


def catalog() -> dict:
    return {"version": VERSION, "profiles": [get(mode) for mode in _DEFINITIONS],
            "executable_modes": list(EXECUTABLE_MODES), "runtime_available": False}
