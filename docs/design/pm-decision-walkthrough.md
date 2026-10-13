# PM decision walkthrough against central

Run from `desktop` after generating the browser assets:

```sh
npm run build:browser
heavy npm run test:walkthrough
```

The script runs English at 1440 × 900 and Traditional Chinese at 390 × 844.
It serves this checkout's generated dashboard from an actual `TaskDaemon` over
loopback HTTP. The journal, configuration and Git repositories live in a unique
disk directory under `~/agent-work/tmp/pytest`, removed after the fixture exits.
`BATC_PM_BACKEND` and `BATC_PM_PYTHON` can select another checkout/interpreter,
as in `test:pm`. `BATC_WALKTHROUGH_SHOTS` selects an artifact directory; otherwise
screenshots go under `$TMPDIR/shots`. Each screenshot disables animations.

The fixture uses the registered operations to create the project, charter inputs,
accepted two-requirement plan, acceptance criteria, bound work and open issue.
Login work is Doing; recovery work is Waiting and its requirement is blocked by
the sourced issue. There is no invented `blocked` work-item state.

An accepted plan requires a centrally recorded managed Git binding. A zero-host
configuration cannot create that binding through the existing adoption action.
This fixture therefore uses the existing MockBAT and FakeGitHub transports plus
real temporary Git, including a manual checkout with an uncommitted edit and a
separate managed worktree. No live BAT host, credential or native configuration
is used. The native reply uses the existing scripted `FakeRuntime` and validated
protocol events; this proves the central proposal/adoption path, not a live model
or native desktop installation. There are no SQL seed writes or additional
registered actions.

## Assertions and artifacts

Screenshot names start with `en-1440-` or `zh-TW-390-`.

| Step | User experience and assertion | Screenshot suffix |
| --- | --- | --- |
| Understand | Goal, 0 of 2 accepted, one blocker, zero pending decisions, six charter sections and the issue in attention | `01-understand.png` |
| Find | Attention opens the exact Issues & changes inspector; its title, next step and recovery requirement are visible | `02-problem.png` |
| Draft | Discuss opens a focused unsent draft, meaningful issue title and central record context chip | `03-discussion-draft.png` |
| Reload | Title, chip and typed message survive; no operation has been created | `04-draft-reload.png` |
| Send | Thread route and central message appear; composer clears | `05-message-sent.png` |
| Propose | Native analysis returns six review rows, the 15-minute criterion and unchecked referral exclusion | `06-proposal.png` |
| Review | Rationale is entered and Adopt is enabled | `07-review.png` |
| Adopt | Lose one acknowledgement after admission; Check original request repeats the exact key and envelope. UI receipt checks reach Adopted and link to the new accepted revision | `08-adopted.png` |
| Return | Overview uses the new plan head and retains the goal and completed decision | `09-decision-overview.png` |
| Register | Decision filter includes the adopted revision and opens its inspector | `10-decision-register.png` |

Before returning to Overview, fixture read-only verification requires exactly
four browser parent operations, two HTTP adoption attempts with the same key,
one new durable adoption, unchanged stable requirement IDs, no excluded work in
the accepted plan, a Git HEAD matching the adoption receipt, no execution
authorization, no task dispatch, no BAT/provider writes and unchanged manual
checkout and remote refs. The script records all API response statuses and page
errors. The optional bearer-session probe and unconfigured current maintenance
observation may return 404; only these exact reads and the deliberately lost
adoption acknowledgement are allowed to fail.
Completion waits use the UI's own receipt state, without polling the API from the
test. The stdin verification reads the journal after that UI proof.

If the child pipeline outlasts automatic bounded checks, the walkthrough uses the
existing Check original request control again, revealing it in Details when
necessary. Subsequent checks read the acknowledged original ID; the adoption
POST still occurs exactly twice with one key and unchanged request.
Central readback can render Adopted while that original receipt is still being
checked. If its recovery form remains in Details, the test requires its terminal
Saved marker before proceeding; a retained disabled control is not a new write.

## Current contract blockers

The walkthrough deliberately fails at Return, preserving both owner requirements:

- Adoption records the retained goal as `governance.goal`, while `pm_overview`
  recognizes only exact `value`, `goal` or `problem` slots. The updated charter
  contains the goal, but the answer line reports Not set / 未設定, with
  `plan.goal = null` and `no_explicit_goal` from central.
- Overview's Decisions / 待決事項 is a pending-attention projection. Once the
  revision is adopted and the proposal becomes non-adoptable, its decision is
  absent from that projection. The response contains no adopted-decision history.
  Changing the pending count or fabricating an attention row would misrepresent
  this contract.

Per the task boundary, no backend fields, goal-slot mappings, adoption semantics
or decision-history contract were changed. The final Register step is implemented
but remains unreached after the Return failure. The original open issue also
remains open: plan adoption does not revise governance or rebind work implicitly.

`test:walkthrough` is available locally but is not added to the existing passing
`test:pm` CI step until these contract decisions are resolved. A nonzero exit is
required for the missing owner experience; there is no expected-failure bypass.
Failures include locale, width, step, elapsed time, recent HTTP statuses, operation
keys, the last UI-observed Overview response and a failure screenshot.
Two consecutive local runs reached the same Return failure in both locales in
32.5 and 34.0 seconds total, with the adoption and central safety checks passing.
These are runtimes to the reported boundary, not passing end-to-end results.
