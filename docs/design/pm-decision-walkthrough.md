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
screenshots go under `$TMPDIR/shots`, falling back to the ignored repo-local
`desktop/test-results/walkthrough`. Each screenshot disables animations.

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
| Resolve review | Secondary action opens a rationale naming the adopted revision; the person edits it before sending | `09-resolution-review.png` |
| Resolve | Lost acknowledgement recovers the exact original key and full finding; readback shows Reported resolved and Not assessed | `10-issue-resolved.png` |
| Return | Goal remains, new plan head is visible, blocker count is zero, issue leaves Needs attention and adopted revision appears under Recently decided with its actor | `11-decision-overview.png` |
| Decision register | Recent decision opens its exact inspector; decision filter includes that same adopted revision | `12-decision-register.png` |
| Issue register | Issue remains in the register with Reported resolved status | `13-resolved-register.png` |

Before returning to Overview, fixture read-only verification requires exactly
five browser parent operations, two identical HTTP adoption attempts and two
identical resolution attempts, one new durable adoption and one finding revision,
unchanged stable requirement IDs, no excluded work in
the accepted plan, a Git HEAD matching the adoption receipt, no execution
authorization, no task dispatch, no BAT/provider writes and unchanged manual
checkout and remote refs. Resolution preserves every original finding field except
status and rationale, all affected IDs, original source identities and the original
snapshot. The version advances once, snapshot count stays unchanged and verification
remains unassessed. The script records all API response statuses and page
errors. The optional bearer-session probe and unconfigured current maintenance
observation may return 404; only these exact reads and the deliberately lost
adoption and resolution acknowledgements are allowed to fail.
Completion waits use the UI's own receipt state, without polling the API from the
test. The stdin verification reads the journal after that UI proof.

If the child pipeline outlasts automatic bounded checks, the walkthrough uses the
existing Check original request control again, revealing it in Details when
necessary. Subsequent checks read the acknowledged original ID; the adoption
POST still occurs exactly twice with one key and unchanged request.
Central readback can render Adopted while that original receipt is still being
checked. If its recovery form remains in Details, the test requires its terminal
Saved marker before proceeding; a retained disabled control is not a new write.

## Decision loop contract

The integrated backend recognizes retained `governance.goal` input and returns
completed history through `recent_decisions`, its nullable count and complete/truncated
flags. Overview keeps pending decisions separate from completed history, and fixed
reports never borrow live history. See the [Overview contract](pm-overview-discussion-context.md).

Adoption alone leaves the sourced issue open. Resolve issue is an explicit durable
`pm.governance.revise` form, available only to the assigned writer or current workspace
owner with the registered action and observe/manage scopes. It reads the original
record, retains the full finding and original sources, uses `expected_version`, settles
the original operation, then rereads the finding and Overview. A changed baseline must
be reviewed; withdrawn sources remove the controls, and late reads cannot revive them.
Resolution is a report, not requirement acceptance or verification. See the
[governance sequence](pm-governance.md).

No product contract blocker remains in this walkthrough. Saved adoption context
receipts read the fixed plan title and revision number; the original purpose stays in
Details. Context-chip/list labels and shared time formatting remain owned by the
parallel cross-view sweep.

`test:walkthrough` has its own Linux CI step alongside `test:pm`, outside the existing
central scripts' ten-minute step budget. There is no expected-failure bypass.
Two consecutive complete local runs took 34.33 and 34.47 seconds including cleanup;
the separate CI step has a two-minute timeout to cover both locales and overhead.
Failures include locale, width, step, elapsed time, recent HTTP statuses, operation
keys, the last UI-observed Overview response and a failure screenshot.
This proves real-central browser acceptance with mock external transports, not
installed native or live-provider acceptance.
