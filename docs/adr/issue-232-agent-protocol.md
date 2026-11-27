# Issue #232 linked-document Agent pilot v1

This is a second experiment. The original eight questions and 32 completions
stay anchored by their own [pilot protocol](issue-232-pilot-protocol.md).
The [linked-document fixture](../../workspaces/decision-platform/python-services/tests/fixtures/rag/issue-232-agent.v1.json)
fixes nine invented official documents, three English questions, the expected
document and scalar, and two permitted links on each starting document.

| Case | Required relation | Expected final response |
| --- | --- | --- |
| A1 | Exception | 12 from `atlas_exception` |
| A2 | Correction | 29 from `iris_correction` |
| A3 | Result | Hold, because the result is later than the as-of date |

Every arm reads the same first official document by catalog key and as-of date.
Single lookup sends that document to the model. Deterministic two-step selects
the linked key matching the stated relation. The bounded router uses the same
model to choose a key from the first document and its links. It gets one
routing completion and can ask for at most one follow-up lookup. The policy
accepts only a key linked from the first result with that document cited, and
the follow-up query applies the same availability filter. A missing route or
future document leaves the final answer with the first document only.

The relation is part of the fixed request and is available to both selectors.
Each first document also has an unrelated official link. The final model
uses the same fixed answer prompt, parameters and citation rules from the first
pilot. A run has nine final completions, plus three router completions. The
order of the arms rotates. The official lookup is an in-memory fixture and
the router is a local stand-in for production Agent behavior.

Report observed final answer, verifiable citations and holds as in the first
pilot. Also report rejected and wrong routes separately, including the path
where a valid key yields no available document. For each arm count catalog
lookup attempts, physical model calls, input/output tokens and median total
wall time over three cases. Report zero provider charges for locally observed
calls. Runtime errors or missing rows are reported with the fixed denominator.
The fixture oracle uses gold after routing and makes no inference calls.

If deterministic two-step matches the bounded router on grounded passes and
holds, the sample supports testing the deterministic option first. The router
is only a candidate for further testing if it adds a grounded pass and
preserves the hold. The fixture structure gives deterministic routing an
explicit relation map. This pilot does not test open-ended planning or show
how either route performs with production documents.
