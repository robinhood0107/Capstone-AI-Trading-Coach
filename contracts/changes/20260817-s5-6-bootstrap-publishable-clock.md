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

## Local artifact vault and restore boundary

Production data is never committed to the public repository or Git LFS. The operator
keeps primary and secondary content-addressed, owner-private copies beneath two
server-configured approved roots:

```text
S5_ARTIFACT_VAULT_PRIMARY=<owner-private primary root>
S5_ARTIFACT_VAULT_SECONDARY=<owner-private secondary root>
```

The S4 copy contains a PostgreSQL custom-format dump of the active
`capstone-pre-s5-fresh` database plus only the active `pre-s5-fresh/local-corpus`
namespace. The S5 copy contains the bootstrap packet, closed source and feature bundles,
qualification seal, model release, Signal batch, and daily state. Environment files,
credentials, tokens, account identifiers, provider raw bodies or headers,
local-only reference material, and unrelated research-model caches are excluded.

Each vault release has a sorted relative-path inventory with file size, mode, and
SHA-256 plus an archive digest. The WSL copy uses directory mode `0700` and file mode
`0600`; the Windows copy is restricted to the current Windows user. A release is accepted
only when both archive digests match and a fresh isolated restore succeeds without
provider or Voyage calls. S4 restore must reproduce `FULL_READY`, the active Voyage
profile, sources/chunks `142/7,871`, document batches `63/63`, and evaluation batches
`2/2`. S5 restore must pass the existing source, feature, release, and batch validators.
Git ignore and tracked-file checks must show zero runtime payloads before completion.
