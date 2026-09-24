# MARS FULL automation worker is enabled

The FULL Compose service starts the existing supervised automation runtime. It uses the bounded
owner-scoped scheduler and owner/account-bound KIS_MOCK credential bridge. DEMO keeps its automation
worker disabled and contains no brokerage capability.

Starting the worker does not arm users or bypass readiness. A user needs an owner control, policy,
account baseline, current release/model evidence, valid certification, and clear reconciliation
state before the existing `arm` transaction can create an ARMED schedule. The scheduler may reconcile
an existing owner claim; otherwise it processes only users returned by the bounded active-owner DB
function. KIS_LIVE remains unavailable.

This is configuration for a future manual NAS rollout. CI publishes images and does not pull, switch,
or start containers on the NAS.
