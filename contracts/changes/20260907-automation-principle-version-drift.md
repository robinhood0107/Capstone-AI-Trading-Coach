# Automation notices principle version drift before it claims a session

Automation pins a principle version when it arms: `automation_control` stores
`principle_version_id` and `principle_version`, and `p1_arm_automation_v3` refuses to arm when
that version is no longer the principle's `current_version`. Nothing, however, stopped the owner
from editing the principle after arming. Policy edits are locked while armed in two places, in
the dashboard and in `p1_put_automation_policy_v1`; principle edits are locked nowhere. The
principle screen did not read the automation state, `PrincipleService.update` and
`update_owned_principle_authorized` never consult `automation_control`, and no trigger exists.

The edit therefore succeeded silently and the already scheduled session kept running against the
armed snapshot. Order sizing reads the pinned version through the runtime state; the risk verdict
reads `principles.current_version` through the shared decision path. Once the two disagreed the
submit transition rejected the binding with `40001`, and the tick retried every twenty seconds
fifteen times before giving up. The owner saw automation die shortly after editing a principle and
no screen said why.

The owner's chosen meaning is that a principle change is allowed and applies from the next
session. A session that already began keeps the snapshot it started with, so the correct
behaviour is to skip a drifted session cleanly before it claims rather than to kill it moments
before an order. Two functions decide that, and only fixing one of them was not enough.

V151 adds the version comparison to `principle_current` in
`p1_automation_runtime_readiness_v1`. That closes the paths that consult readiness: the readiness
command, `mock start`, the arm gate, and the bounded data-gap fallback. It does not close the
resident runtime, because `serve()` does not call readiness before claiming — it calls
`ensure_daily_signals`, then claims, then drives the claim.

V152 therefore also overrides `principleActiveCurrent` in
`p1_read_automation_runtime_state_v4`, which is the value the engine actually reads before it
decides whether to run. `automation.py` already closes the session with
`SKIPPED_DATA_UNAVAILABLE` when that flag is false, so no Python change is required. V4 is
redefined rather than V1 because production calls only V4; V1 through V3 exist as a delegation
chain, and rewriting V1 would mean transcribing a hundred and fifty lines whose other clauses
could drift in the process. A reader looking only at V1 will not see the version check, so V152
records that the runtime reads the overridden value and that the clause should move if V1 is ever
rewritten.

Both functions treat a disarmed control, or a control with no pinned version, as current. Without
that a first arm would be permanently blocked.

Recovery is the existing path: stop automation, save the policy again so it snapshots the current
principle version, then arm. The stop control is on the owner screen. The principle screen now
states, while automation is armed, that a change applies from the next session and what the
recovery sequence is; it reads the automation status as a side concern so the screen still renders
if that read fails.

Principle editing is still not blocked. Neither the version read by order sizing nor the version
read by the risk verdict changes. No new state, column, HTTP operation, OpenAPI surface, or Python
change is introduced. `PRINCIPLE_VERSION_DRIFT` was considered as a status blocker so the arm
button could name its own reason, and was withdrawn: the blocker list is a closed enum whose root
OpenAPI copy is synchronised by a one-time transition script that requires a pre-V3 exact-69 root,
and a partially synchronised contract gate is worse than an unnamed disabled button. That naming
remains outstanding.
