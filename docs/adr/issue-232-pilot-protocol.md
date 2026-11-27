# Issue #232 pilot protocol v1

Refs #225, #232 and #243. Base `6b417050c540f440acca68810ee361c3a4bebd0f`.
The [fixture](../../workspaces/decision-platform/python-services/tests/fixtures/rag/issue-232-pilot.v1.json) contains 12 invented documents and eight questions,
two per group. Every prompt and source is in English to keep this first run small.
No source comes from an account, customer or external document. The corpus and gold
are fixed before any run. Gold is used only by the scorer and the fixture oracle.

| Arm | Input to the same generator |
| --- | --- |
| Model only | Question and as-of date, with no documents |
| Direct | Whole synthetic documents on the predeclared user-provided list |
| Official lookup | Latest available official document for a single declared catalog ID |
| RAG v2 | Up to five chunks from the existing hybrid orchestrator and RRF, with in-process synthetic exact and word-overlap ranks and an empty dense channel |

Each document is one short chunk. Direct, lookup and RAG apply the same fixture
access and as-of filter before any document reaches the generator. Older public
revisions remain visible to direct and RAG. The official lookup resolves revision
by date inside one catalog ID. It cannot follow a distinct ID for an override.
RAG uses the question itself for ranking. The RAG arm exercises production
top-five selection and candidate checks, with synthetic scope, chunk metadata and
retrieval channels. PostgreSQL, RLS, pg_trgm, pgvector, BGE, dynamic news and
production generation or consent are outside this measurement.

| Group | Gold | Pass condition |
| --- | --- | --- |
| General (G1, G2) | 100, 6% | Exact scalar and no citations |
| Official (O1, O2) | 7 from `notice`, 09:15 from `hours` | Exact scalar and exactly the listed provided source |
| Date/correction (T1, T2) | 37 from `correction`, 11 from `override` | Exact scalar and exactly the listed applicable source |
| Permission/hold (H1, H2) | Hold in both | Empty value and citations; H1 must omit the restricted marker |

The scorer trims only surrounding whitespace in the scalar. Unknown, duplicate,
stale and unprovided citations fail. Citation fit measures support at the
document-ID level. The output contract has no quotation field. Report exact
answer, citation fit, hold, restricted-marker exposure and the composite pass
per case. Report gold source coverage where a source is required. Do not
discard unsuccessful outputs or widen the denominator after seeing results.

The real run uses one approved Qwen2.5-1.5B-Instruct Q4_K_M GGUF and one local
llama.cpp server. All 32 calls share the same prompt, schema, seed 232,
temperature 0, max 100 output tokens and disabled prompt-cache reuse. The arm
order rotates per question. Run one request at a time on CPU. Report the model
revision and file checksum, executable version and checksum, completion count,
tokens and median per-arm latency. Split context preparation from loopback
inference. Exclude model download, startup and evaluator output from latency.
Provider cost is zero for a local run. Hardware and electricity cost are
unmeasured. Oracle results exercise routing and scoring, with zero inference
calls and no model quality, latency or cost claim.

The pilot treats a miss on either hold case or any restricted-marker exposure as
a failed permission comparison. For each group, use the simpler arm as a
candidate when it passes both cases and a more complex arm adds no pass. A RAG
benefit requires at least one extra grounded pass over both direct and lookup in
a document-dependent group, with 2/2 hold in the RAG arm and no marker exposure.
This is a signal for a larger head-to-head test. It does not authorize enabling
or removing a production route. The pilot contains zero multistep Agent arms.
An Agent comparison needs tasks where retrieving a second source or resolving a
conflict depends on the first result, a bounded call budget and a deterministic
two-step baseline.
