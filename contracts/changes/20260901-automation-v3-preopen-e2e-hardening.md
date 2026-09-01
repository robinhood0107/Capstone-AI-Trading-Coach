# Automation V3 pre-open E2E hardening

## Reason

The provider-free E2E sequence exposed two gaps before the next market session.

- `brokerage_e2e` inherited the currently active KIS Mock online flag after
  `full_pipeline_e2e` restored the stack. Its first mock write could therefore
  reach the physical mock provider instead of the deterministic after-hours
  transport.
- Automation V3 run reads selected `automation_ai_judgements`. The historical
  runtime RLS policy referenced the private `automation_runtime_checkpoint`
  table directly, so PostgreSQL required `decision_app` to have a privilege it
  must never receive and returned 403.
- The live-readiness contract test used pytest-style module functions even
  though Contracts CI runs `unittest discover`, so those checks were not in the
  actual CI test set.

## Change

- The after-hours brokerage runner now switches to the loopback fixture,
  exercises submit, idempotent replay, balance, buyable, cancel, and reconcile,
  and restores the exact previously observed runtime flags. It never reads or
  mutates the registered mock-account environment.
- The fixture now has a process-local provider-reference store and deterministic
  cancel response, allowing the production gateway cancellation path to run
  without a provider socket.
- V114 replaces the runtime judgement policy's direct private-table subquery
  with a session-bound security-definer boolean. Owner reads retain the existing
  owner policy; no base-table privilege is granted to `decision_app`.
- V115 lets `expectedVersion=0` create the first V3 policy after immutable V1/V2
  history while continuing the internal append-only version sequence. Later V3
  updates still require the current V3 version CAS.
- Role bootstrap reapplies the V114 helper's exact two-role EXECUTE grant after
  an existing-volume restart, and the public seed target allowlist advances to
  V115 without changing the sealed seed bytes.
- The contract checks are a real `unittest.TestCase`, and the API E2E now covers
  V3 status, policy CAS, runs, positions, and owner-masked missing detail.

Public V1/V2/V3 OpenAPI bytes and operation counts are unchanged.
