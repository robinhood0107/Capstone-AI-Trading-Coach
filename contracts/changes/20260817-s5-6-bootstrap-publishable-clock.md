# S5.6 bootstrap publishable-clock amendment

## Decision

S5.6 initial production bootstrap may author a packet for the latest XKRX session whose
label evidence clock has already matured at the current operator cutoff.

This does not allow using an immature latest completed session. If the latest completed
session's `[t+1, t+6]` label evidence is not yet mature, the packet author steps back one
XKRX session at a time until `labelAsOf(session) <= operatorCutoff`, then writes the
bootstrap packet with that publishable cutoff.

## Rationale

Holiday chains can make the latest completed XKRX session unavailable for S5 labels even
though the previous session is still the current publishable Signal batch clock. On
2026-08-17, for example, 2026-08-14 was completed but its next XKRX evidence clock was
2026-08-18 08:10 KST, while 2026-08-13 was already publishable.

## Invariants

- immature latest completed sessions remain unavailable;
- provider calls stay behind the bootstrap packet and approved caps;
- no order, account balance, or RiskDecision wiring is added;
- the packet remains canonical JSON and is still regenerated during validation;
- the model release and signal batch still must pass all S5.6 gates before activation;
- a later session is handled by the S5 daily refresh path, not by silently rewriting the
  already-authored bootstrap packet.
