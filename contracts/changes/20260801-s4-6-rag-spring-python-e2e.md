# KR: S4.6 Spring-Python RAG E2E / EN: S4.6 Spring-Python RAG E2E

상태: `S4_6_RETRIEVAL_ONLY_E2E_VERIFIED`

## KR

### 목적과 순서

S4.4 public API를 바꾸지 않고 Spring과 Python 사이의 unary `RagService.Ask`를
구현한다. 신규 요청은 `auth → owner consent → rate limit → idempotency claim →
owner retrieval scope → loopback RPC → local guard/retrieval/fixture → Spring citation recheck →
encrypted history atomic complete → response` 순서를 따른다. same-owner replay는 scope 발급과
gRPC를 반복하지 않는다.

### 계약과 보안 경계

- `contracts/proto/rag.proto`, tracked Python `pb2/pyi/pb2_grpc`, canonical descriptor·SHA-256를
  deterministic generator로 고정한다. JVM은 같은 proto를 컴파일하고 descriptor bytes를
  독립 대조한다.
- request에는 opaque request/scope, public question, bounded mode/symbol/topic, effective consent,
  server-selected policy/generation/profile만 있다. JWT, API key, raw owner/account/order,
  history ciphertext는 없다.
- Python은 numeric loopback에만 bind하고 reflection을 등록하지 않는다. deadline 15초,
  Spring read budget 17초, request 64KiB, response 256KiB, concurrency 8, retry 0이다.
- Spring은 response request/generation/profile/policy, citation `cit_1..cit_N`, authorized top-5
  subset, HTTPS locator, provider physical count 0을 재검증한다. V22의 least-privilege
  `SECURITY DEFINER` projection은 owner/session/topic/expiry/active generation을 다시 검증한다.
- `RAG_GRPC_ENABLED=false`는 기존 S4.4 retrieval-only 호환 모드다. true는 같은
  배포 단위의 Python fixture process와 명시적 `RAG_GRPC_SHARED_SECRET`이 준비된 경우에만
  허용한다. RAG secret은 모든 활성 auth·Decision/Python·brokerage credential과 반드시 달라야 하며
  fallback은 없다. 따라서 RAG credential로 Disclosure RPC, JWT, brokerage capability를 재사용할 수 없다.
- Gemini·OpenAI·Voyage, market provider, account, order physical call은 0이다.

### 공개 계약 영향

REST request/response, history, feedback, consent, OpenAPI field 변경은 0이다. proto는
Spring-Python internal contract이며 breaking field-number 변경을 negative test로 거부한다.

## EN

This change adds the internal unary `RagService.Ask` without changing the S4.4 public REST
surface. New requests execute consent, rate limit, idempotency claim, owner-scope issuance,
one loopback RPC, local guard/retrieval/fixture evaluation, Spring citation revalidation,
and atomic encrypted-history completion in that order. Same-owner replay performs no new
scope issuance or RPC.

The deterministic generator tracks Python stubs plus a canonical descriptor and hash; JVM
code compiles the same proto and compares descriptor bytes independently. The request carries
only opaque scope, public bounded query fields, effective consent, and server-selected policy
identities. It carries no JWT, API key, raw owner/account/order identity, or ciphertext.

The transport is numeric-loopback only, reflection-disabled, single-attempt, and bounded to a
15-second deadline, 17-second Spring read budget, 64-KiB request, 256-KiB response, and eight
concurrent calls. Spring rechecks response identity, active generation, top-five citation scope,
and zero provider counts. V22 rechecks owner, session, topic, expiry, and active generation with
execute-only security-definer projections. Public REST and OpenAPI fields do not change.
Gemini, OpenAI, Voyage, market-provider, account, and order physical calls remain zero.

`RAG_GRPC_ENABLED=true` requires an explicit dedicated `RAG_GRPC_SHARED_SECRET`; it never falls
back to the Decision/Python Disclosure wire secret. The RAG secret must differ from every active
auth, Decision/Python, and brokerage credential, so a RagService credential cannot authenticate a
Disclosure RPC, sign a JWT, or reuse a brokerage capability.

## 검증 / Verification

```bash
uv run --project workspaces/decision-platform/python-services --frozen \
  python contracts/generate_rag_proto.py --check
uv run --project workspaces/decision-platform/python-services --frozen pytest -q \
  workspaces/decision-platform/python-services/tests/rag/test_rag_proto_parity.py \
  workspaces/decision-platform/python-services/tests/rag/test_rag_rpc.py \
  workspaces/decision-platform/python-services/tests/rag/test_rag_grpc_server.py
cd workspaces/decision-platform/spring-api
./gradlew ktlintCheck test
```
