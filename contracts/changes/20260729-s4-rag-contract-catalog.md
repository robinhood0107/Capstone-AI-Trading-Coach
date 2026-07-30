# S4 RAG profile·policy contract catalog

상태: `CONTRACT_LOCKED / RUNTIME_PARTIAL / MIGRATION_RED`
현재 catalog SHA-256: `9b9881f9b25b6486f20999f27c0dd7043048fc26491e33cf2af892817dabbe0a`

> 이 SHA는 두 profile·세 policy와 public ask의 server-owned selector 경계를 식별한다.
> catalog generator/schema/fixture와 Spring/Python consumer parity는 고정됐지만,
> next-free normalized migration·source API·registry가 red이므로 S4.0 완료나 release
> contract로 과장하지 않는다.

## KR

이 변경은 S4/P1 RAG의 embedding profile과 policy를 하나의 versioned static catalog로
잠근다. public API, Spring, Python은 임의 model/provider 문자열을 받지 않고
`contracts/catalogs/s4-rag-contract.v1.json`의 exact bytes만 소비한다.

catalog ID는 `s4-rag-contract/v1`이다. 위 SHA-256은 아직 보정 전 draft bytes의 식별자다.

### 잠긴 결정

1. 활성 embedding profile은 정확히 두 개뿐이다.
   - `bge_m3_local_1024_v1`
   - `voyage_context_4_1024_v1`
2. `voyage_context_3_1024_v1`은 P1 active profile·비교 profile·fallback profile 어디에도
   등록하지 않는다. fixture가 이 ID를 active catalog에 넣으면 실패해야 한다.
3. 활성 policy는 정확히 세 개뿐이다.
   - `bge_only_v1`
   - `voyage_only_v1`
   - `bge_then_voyage_on_sla_v1`
4. `bge_then_voyage_on_sla_v1`은 요청마다 BGE 실패 후 Voyage를 호출하는 runtime fallback이
   아니다. BGE warm p95 SLA 실패, Voyage 평가 통과, 관리자 승인 후 default pointer를 한 번
   원자 전환하는 정책이다.
5. public `POST /api/v1/rag/ask` body는 `embeddingProfileId`, `embeddingPolicyId`,
   `profileId`, `policyId`, `provider`, `model`, `topK`, `sourceTier`를 받지 않는다.
   profile/policy 선택과 generation 활성화는 서버·관리자 경계에 남긴다.
6. static catalog는 profile, policy, dimension 1024, provider/model identity, chunk input
   strategy, API enum과 negative fixture를 소유한다. DB는 source/revision, generation,
   materialization/evaluation, active pointer, policy transition, usage ledger 같은 동적 상태만
   소유한다.

### merge 전 필수 보정

아래 네 항목은 이미 잠긴 S4.0 계약이며 현재 draft bytes와 다르다.

1. `answerModes`는 정확히 `CONCISE`, `DETAILED`다.
2. question은 NFC 정규화 뒤 1~1,000 Unicode scalar이고 UTF-8 최대 8KiB다.
3. BGE artifact format은 pinned `ONNX_DATA_ONLY`다. safetensors나 pickle/joblib을
   production artifact로 읽지 않는다.
4. canonical chunk 자체는 overlap 0이다. BGE의 15% 인접 문맥은 ephemeral embedding input일
   뿐 저장 chunk/citation/content hash가 아니다.

이 보정은 generated catalog만 수동 편집하지 않고 source generator, JSON Schema,
positive/negative fixture, Spring/Python parity test를 함께 바꾼다. 보정 뒤 catalog hash가
바뀌는 것은 정상이며 본 문서의 draft SHA를 새 검증값으로 교체한다.

### 산출물과 검증

| 산출물 | 역할 |
|---|---|
| `catalogs/s4-rag-contract.v1.json` | S4 RAG profile/policy SSOT |
| `schemas/s4-rag-contract.schema.json` | catalog exact-shape schema |
| `schemas/s4-rag-ask-request.schema.json` | public ask body schema |
| `schemas/s4-rag-admin-policy-selection.schema.json` | admin policy pointer selection schema |
| `generate_s4_rag_contracts.py` | generated artifacts drift check |
| `examples/invalid/s4-rag-*.invalid.json` | context-3, profile/policy confusion, public model selection 거부 |

재현 명령은 provider/live/account/order/broker 호출을 만들지 않는다.

```bash
uv run --frozen python contracts/generate_s4_rag_contracts.py --check
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
```

## EN

This change locks the S4/P1 RAG embedding profiles and policies in one versioned
static catalog. Public API, Spring, and Python consume exact catalog bytes instead
of accepting arbitrary model or provider strings.

The only active profiles are `bge_m3_local_1024_v1` and
`voyage_context_4_1024_v1`. `voyage_context_3_1024_v1` is explicitly rejected.
The only policies are `bge_only_v1`, `voyage_only_v1`, and
`bge_then_voyage_on_sla_v1`. The last policy is an admin-approved default pointer
transition after BGE SLA failure and Voyage evaluation success, not a per-request
runtime fallback. Public ask requests cannot carry profile, policy, provider,
model, top-k, or source-tier controls.

This file currently describes an implementation draft, not a release-ready
contract. Before merge, answer modes must become exactly `CONCISE` and `DETAILED`,
the question boundary must become 1,000 Unicode scalars plus an 8 KiB UTF-8 cap,
and the pinned BGE artifact format must become data-only ONNX. The generator,
schemas, fixtures, and Spring/Python parity tests must change together and the
catalog SHA-256 must then be regenerated.
