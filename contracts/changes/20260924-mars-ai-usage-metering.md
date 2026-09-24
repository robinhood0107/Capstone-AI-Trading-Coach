# MARS AI usage meter without aggregate call blocking

V208 keeps the existing `operator_ai_gross_usage_reservations` table as a best-effort
record of conservative list-price estimates. Its KST `usage_date` remains useful for
measurement and reporting; it is not a bill or a provider free-balance reading.

The daily hard/soft dollar policy, ADMIN `/api/v1/admin/ai-budget` endpoint, and their
required `MARS_AI_DAILY_HARD_CAP_USD` deployment setting are retired. Vertex, Voyage,
public Agent, and automated-trading provider calls are not rejected because an aggregate
estimate exceeds a day limit or because the estimate cannot be written. Voyage records
usage through an independent best-effort writer call so metering failure cannot invalidate
its already-created one-shot authorization.

Provider-enforced availability/rate limits, authorization packets, per-request byte/token
and physical-call bounds, owner/account binding, and KIS order/reconciliation gates remain
active. Vertex Free Trial without paid upgrade is governed by Google Cloud's account state.
Voyage pricing grants the first 200 million tokens for `voyage-context-4`; subsequent usage
is priced per token, so deployments must monitor the provider account. MARS does not read
provider free-balance data or promise that the provider will reject paid overage.

This supersedes the runtime blocking behavior described by
`20260923-mars-operator-ai-budget-policy.md` and
`20260923-mars-ai-gross-reservation-v1.md`; those files remain as historical rationale.
