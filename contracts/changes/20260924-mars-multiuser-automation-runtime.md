# MARS multi-user automation runtime claim boundary

## Decision

The FULL product uses one bounded scheduler for active owners. It does not accept a global owner
ID or reuse one user's brokerage credential for another user's run. PostgreSQL owns owner discovery,
the 100-owner admission limit, and the atomic owner/session claim.

## V207 internal database contract

- `p1_list_armed_automation_users_v1()` is executable only by
  `decision_automation_runtime`. It returns active users with an ARMED control or ACTIVE claim,
  ordered by user ID, and fails closed above the admission limit of 100.
- `p1_claim_automation_session_for_owner_v1(user_id, session_date, claim_hash)` accepts only one
  syntactically valid owner ID and one SHA-256 claim hash. Both replay and new schedule paths match
  that exact owner and session; the claim row and schedule are locked together.
- During a rolling upgrade, an already claimed session for that exact owner is rebound to the new
  owner-scoped claim hash before the runtime resumes it. Python has no fallback to the old global
  claim format. This compatibility is limited to an existing ACTIVE claim and is not used to select
  another owner's run.
- The runtime receives no table `SELECT` grant. Function ownership, role grants, owner RLS context,
  policy snapshot, and run/account fields remain within the existing database boundary.

## Runtime behavior

One worker may process each owner concurrently, with a fixed pool capped at 100. A failure or retry
for one owner does not mark peer owners complete or reuse their claim. User-scoped KIS credentials
continue to be resolved by the existing owner/account-bound brokerage bridge.

This change does not certify a user's mock account, arm automation, or assert multi-user capacity.
The public automation gate stays closed until certification, DB integration, two-user end-to-end
isolation, and N=10/50/100 measurements pass.
