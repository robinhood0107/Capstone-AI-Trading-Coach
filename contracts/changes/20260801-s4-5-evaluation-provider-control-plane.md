# KR: S4.5 exact-60 평가와 provider 제어면 / EN: S4.5 exact-60 evaluation and provider control plane

## KR

### 목적

S4.7C exact-30 corpus에 결속된 공개·합성 평가 60개와 재현 가능한 deterministic report를
고정한다. 선택 provider는 실행하지 않고, Voyage S4.2C와 Gemini S4.4G의 packet·상태·mock
경계만 구현한다.

### 평가 계약

- evaluation manifest SHA-256은
  `7fe566be2a7945e811a43ca28b951a46529d5d7a61fdafa0c7fa3b8d3a6915dd`다.
- 허용 질문 50개는 identifier 15, 공식 API/product 15, model/method assumption 12,
  multi-source 8개다. adversarial 10개는 injection 4, advice 2, PII/account 2,
  unauthorized/cross-owner citation 요청 2개다.
- 실사용자 자유 질문, owner/session/account identifier, provider raw response는 0개다.
- production `RrfFusion(k=60)`, local guardrail, untrusted-data delimiter와 citation parser를
  fixture 경로에서도 재사용한다. live LLM judge는 0이다.
- Recall@5, MRR baseline non-regression, citation coverage, retrieval failure, advice block,
  model assumption, injection escape와 unauthorized citation gate를 분모와 실패 question ID와
  함께 report에 고정한다.

### provider 경계

- Voyage는 `voyage-context-4`, 1024차원, overlap 0, sourceId-stable no-split 30문서/1배치
  plan만 생성한다. 공식 tokenizer는 supply-chain review 전 설치하지 않고 UTF-8 byte upper
  bound만 사용한다. Files/Batch API, paid usage, retry와 outbound call은 0이다.
- Gemini는 2026-07 current Interactions API의 `gemini-3.5-flash-lite` request DTO를
  `store=false`, `background=false`, output cap 800, tool/function/URL/file/search/code/MCP/
  grounding/cache 0으로 고정한다.
- Voyage와 Gemini packet은 closed JSON Schema이며 목적을 교차 재사용할 수 없다.
  fresh provider packet, paid project/ZDR evidence와 별도 production activation 승인이 없으므로
  두 live executor는 `HARD_DISABLED`다.
- generation materialization, partial publish, profile pointer change와 activation은 0이다.

## EN

### Purpose

Freeze a public/synthetic exact-60 evaluation set and deterministic report bound to the S4.7C
exact-30 corpus. Optional providers are not executed; this change implements only the packet,
state-machine, mock-transport, and fail-closed boundaries for Voyage S4.2C and Gemini S4.4G.

### Evaluation contract

- The evaluation manifest SHA-256 is
  `7fe566be2a7945e811a43ca28b951a46529d5d7a61fdafa0c7fa3b8d3a6915dd`.
- The 50 allowed questions comprise 15 identifier, 15 official API/product, 12 model/method
  assumption, and eight multi-source cases. Ten adversarial cases cover injection, advice,
  PII/account data, and unauthorized or cross-owner citation requests.
- No raw user question, owner/session/account identifier, or provider raw response is retained.
- The fixture path reuses production RRF, local guardrail, untrusted-data delimiter, and citation
  parser. It uses no live LLM judge.
- Every metric keeps its denominator and deidentified failing question IDs in the tracked report.

### Provider boundaries

- Voyage produces only a stable, no-split 30-document/one-batch offline plan for
  `voyage-context-4` at 1024 dimensions. It installs no tokenizer before supply-chain review and
  makes no Files, Batch, paid, retry, or outbound calls.
- Gemini models the July 2026 Interactions API request for `gemini-3.5-flash-lite` with stateless
  storage, no tools or grounding, and an 800-token output cap.
- Closed packet schemas separate purposes. With no fresh provider packet, paid-project/ZDR
  evidence, or production activation approval, both live executors remain hard-disabled.
- No Voyage generation is materialized, partially published, activated, or selected by a pointer.

## 검증 / Verification

```bash
uv run --project workspaces/decision-platform/python-services --frozen \
  python capstone-rag/generate_s4_5_evaluation.py --check
uv run --frozen python contracts/generate_s4_5_provider_contracts.py --check
uv run --project workspaces/decision-platform/python-services --frozen pytest -q \
  workspaces/decision-platform/python-services/tests/rag/test_s4_5_evaluation.py \
  workspaces/decision-platform/python-services/tests/rag/test_provider_control_plane.py
uv run --frozen python -m unittest contracts.tests.test_s4_5_provider_contracts -v
uv run --frozen python contracts/validate.py
```
