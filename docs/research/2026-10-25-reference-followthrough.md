# Reference review: discussion provenance and delivery evidence

Reviewed against Connector `f9473c4118cf3ed1b93190b253ea8b015738ab1a`.
The two references are the repositories named in the adopted
[PM plan](../product/pm-v1.2-plan.md):
Multi-AI Chat Desktop and Multi-AI Terminal. This review uses their current public
source, rather than treating the plan's older reference commits as current behavior.

| Reference and inspected source | Useful practice | Connector application |
| --- | --- | --- |
| [Chat Desktop export provenance](https://github.com/TED-H-com/multi-ai-chat-desktop/blob/6bcf904dfce890f7ebe198cbfc329bf8d685ce62/src/ui/exportMarkdown.ts) | Attach workflow, snapshot, app and adapter provenance only to a matching conversation. | Keep each Result's original handoff, requirement IDs and assessment references together. Connector's immutable IDs are the binding authority; matching question text is insufficient here. |
| [Chat Desktop compatibility evidence](https://github.com/TED-H-com/multi-ai-chat-desktop/blob/6bcf904dfce890f7ebe198cbfc329bf8d685ce62/docs/COMPATIBILITY.md) | Separate automated coverage from real desktop/provider acceptance; preserve reading position during updates. | Exercise HTTP and desktop IPC presentation in both languages, retain disclosure/focus state, and report fixtures separately from native/live acceptance. |
| [MAT evidence digest](https://github.com/TED-H-com/multi-ai-terminal/blob/f230a57a53e2ee9f08b73454c0e0357fd1e3a62d/server/src/engine/digest.ts) | Present verification separately from an agent's answer; identify each attempt and explicitly mark bounded evidence. | Display verified-but-unassessed requirements, original accepted/rejected conclusions, current applicability and partial coverage without choosing a newest assessment. |
| [MAT workflow and verification contract](https://github.com/TED-H-com/multi-ai-terminal/blob/f230a57a53e2ee9f08b73454c0e0357fd1e3a62d/README.md) | Keep verification, review, artifacts and delivery understandable within the work view. | Extend the existing Result panel and quality editor. Default dependency readiness and an explicitly selected original assessment remain separate facts. |

These are design adaptations; this increment copies no source code or assets.
Python remains the authority for relations, readiness and acceptance. No MAT
server/store or browser-provider automation is introduced. MAT's degraded advance
after an unparseable decision and interrupt-by-default steering do not fit the
adopted Connector contracts and are not adopted.

## Implemented increment

The [Result contract](../design/v1.3-e04-result.md#e09-original-requirement-relations--原始需求關聯)
already supplied original relations through HTTP and MCP. The shared browser/desktop
Result panel now displays them in English and Traditional Chinese:

- Original requirements and acceptance input identities, with each assessment's
  original conclusion, current applicability, reviewer, criteria and evidence.
- Separate unselected dependency readiness and each selection frozen at handoff.
- Explicit incomplete coverage; no empty-relation claim for older servers.
- Existing permission withdrawal and late-response handling, plus stable disclosure
  and focus identities. Merely opening this section performs no extra reads or writes.
- Overview's exact original assessment reference, original operation and existing
  discussion context, with stable refresh and no live references in fixed reports.

## Remaining opportunities

1. Extend PM's comparison of two fixed Results with original requirement and criterion
   references, unresolved disagreements and coverage. Reuse the existing discussion
   sources and continuation, not an independent orchestration/store layer.
2. Keep bounded evidence and explicit source coverage in longer discussion exports.
   A transcript or digest must not acquire another thread's provenance.
3. Continue the existing native/AHP Work routes and Fleet participant integration
   against their own contracts. These reference patterns do not supply the missing
   credential, ownership or live acceptance evidence.

This is a branch implementation record, not merge, release, installation or live
acceptance evidence. Existing v1.2 and v1.3 remaining work is unchanged.
