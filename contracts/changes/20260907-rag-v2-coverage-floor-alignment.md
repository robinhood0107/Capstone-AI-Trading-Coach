# RAG v2 citation coverage floor alignment

Commit `12593078` (rag-always-answer) removed the citation coverage floor from the
application layer on 2026-09-02: coverage became a figure the screen reports, not a
threshold that decides whether an answer is kept. The persistence boundary kept that
floor in two places, so answers below it were generated and billed and then rejected
only at storage time.

`V107` enforced `EVIDENCE >= 0.8` and `EVIDENCE_WITH_REASONING >= 0.2` inside
`persist_s4_9_strong_llm_history_v2`, and `V129` repeated the same rule in the
`rag_v2_answer_history_status_result_check` table constraint. `RagV2Controller`'s
`DataAccessException`/`RuntimeException` handler folded the resulting `22023` into a
single `503 RAG_UNAVAILABLE`, which the dashboard rendered as "설명 근거 저장소를 지금
사용할 수 없습니다". The store was healthy; the contract disagreed with itself.

V150 removes the floor from both places together. The function only checks that
`p_citation_coverage` is present and within `0.0..1.0`; the table constraint checks
`citation_count BETWEEN 1 AND 5` and the same range. Nothing else moves: the basis
enumeration, the `MODEL_KNOWLEDGE` shape (`coverage = 0.0`, exactly
`ARRAY['MODEL_KNOWLEDGE_ONLY']`, empty citations), the seven-value guardrail
vocabulary, the `REASONING_SENTENCES_PRESENT` marker consistency in both directions,
the guardrail cardinality bound, the owner and scope-claim boundary, and the quote and
number binding performed by `canonicalize_s4_9_strong_llm_citations_v2` all remain.
Detecting fabricated citations and setting a floor on how many sentences carry one are
different concerns; only the second is withdrawn.

`MAX_GUARDRAIL_FLAGS` in `RagV2RuntimeService` was reported as an `8` versus `7`
mismatch against the same function. It is not one. That constant applies to retrieval
evaluation flags, which never reach a database function. The flags that are stored come
from `strongLlmGuardrailFlags`, and `RagV2VertexGenerationRuntime` already bounds the
provider warnings to five distinct values from a six-value vocabulary, so at most six
flags are ever written. The database bound of seven is not reachable and is left alone.

No public HTTP operation, error code, OpenAPI schema, or root operation count changes.
Signal, Decision, RiskDecision, order authority and decision hashes are untouched.
This change does not assert that any provider call succeeded.

The related diagnosability gap is closed separately without altering any public
contract: `RagV2Controller` now logs the originating SQLSTATE alongside the exception
class, and `JdbcAutomationRepository.translate` logs `22023` and `23514` explicitly
before returning the existing storage exception. HTTP status codes and response bodies
are unchanged, and no query value, question text, credential or provider response is
recorded.
