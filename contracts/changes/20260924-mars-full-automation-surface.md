# MARS FULL owner automation API surface

FULL exposes only the existing authenticated owner automation operations needed by the dashboard:

- `/api/v2/automation/status` and `/positions` for owner-scoped read views.
- `/api/v3/automation/status`, `/policy`, `/arm`, `/runs`, `/runs/{runId}`, and `/positions`.
- `/api/v1/automation/disarm` as the existing stop operation.
- `/api/v4/automation/capital-policy` and `/capital-status`.

The surface gate matches method and path exactly; only a valid run-detail identifier matches its
path pattern. All other FULL endpoints remain closed unless separately listed for Google OIDC,
credential setup, AI budget management, and the owner RAG Agent. DEMO continues to reject every
automation path. Spring Security still requires an authenticated principal for these routes, and
the services derive owner identity only from that principal.

Opening the route does not make an owner ready to trade. Existing automation readiness checks still
require valid owner certification, release/source binding, current model evidence, risk state, and
account reconciliation. This route change does not enable the worker or bypass those gates.
