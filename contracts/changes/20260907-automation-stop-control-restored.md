# Automation stop control restored on the owner screen

`5a133675` removed the automation stop button from the owner screen on 2026-09-04 and
`cdd0f5b8` locked that removal with a contract assertion that the view must not call
`api.disarmAutomation`. Two problems drove the removal. The old label named the action
as an order block, which is what the Kill Switch does, so a viewer could not tell which
of the two controls they had pressed. And disarming during a session could leave the
owner unable to re-arm, because `canArm` requires a configured v3 policy, no legacy
position, market history in `READY`, and no other blocker; a stop with no way back is
worse than no stop at all.

The removal left the screen able to start automation and unable to stop it. The only
remaining control was the Kill Switch, which blocks orders while leaving
`automation_control.control_state` at `ARMED`, so the resident runtime keeps claiming
sessions. That is a different guarantee from the one an operator reaching for a stop
button expects.

Both original reasons are addressed rather than avoided. The action is labelled
"자동운용 정지" and its success text states what actually changes — the next scheduled
session does not open, holdings and existing fills are untouched — so it no longer reads
as an order block. The confirmation step reports whether re-arming is currently possible
and, when it is not, names the blocking gates, so the owner decides with that in front
of them instead of discovering it afterwards.

No new HTTP operation is added. `POST /api/v1/automation/disarm` and
`api.disarmAutomation` already existed; only the caller was missing. The operation reads
and writes the single `automation_control` row and is therefore shared by v1, v2 and v3.
`p1_disarm_automation_v1` keeps its `controlVersion` compare-and-set, its idempotent
replay through `automation_control_idempotency`, and its refusal to change any state
other than `ARMED` to `DISARMED`, so a repeated click is not a second effect. The button
is rendered only while `controlState` is `ARMED`, matching that function's behaviour.

The contract assertions are updated to lock the new intent instead of the removal: the
misleading label stays forbidden, and the view is now required to call disarm, to render
the control-change label, to gate the action behind a confirmation step, and to show it
only in the armed state. Root OpenAPI operation counts, error codes, risk rules, order
budgets and Live authority are unchanged.
