# S4 RAG profile·policy contract catalog

상태: `S4_0_VERIFIED / S4_1_VERIFIED / S4_7A_VERIFIED / S4_2_APPROVAL_GATED`
현재 catalog SHA-256: `9b9881f9b25b6486f20999f27c0dd7043048fc26491e33cf2af892817dabbe0a`

> 이 SHA는 두 profile·세 policy와 public ask의 server-owned selector 경계를 식별한다.
> deterministic manifest, generator/schema/fixture, Spring/Python semantic parity,
> next-free V16 normalized migration과 exact source projection이 같은 bytes를 검증한다.

## KR

이 변경은 S4/P1 RAG의 embedding profile과 policy를 하나의 versioned static catalog로
잠근다. public API, Spring, Python은 임의 model/provider 문자열을 받지 않고
`contracts/catalogs/s4-rag-contract.v1.json`의 exact bytes만 소비한다.

catalog ID는 `s4-rag-contract/v1`이다. 위 SHA-256은 현재 승인된 canonical catalog bytes의
식별자이며 `catalogs/s4-rag-contract.v1.sha256.json`이 이를 단일 source로 전달한다.

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
7. `rag-source-card-v1`은 PROJECT tier 공식 source card의 exact 30개 front matter field,
   canonical HTTPS locator·digest, UTC `Z` 검증 시각, exact 20 upstream source ID,
   evidence class별 기관 authority, 보존·외부 처리 제한, 근거·반증·질문 경계를 잠근다.
   unknown field/upstream, authority 불일치, non-NFC, non-UTC offset, 과대 입력, 잘못된
   hash·URL·enum, 필수 license·retention 누락, 비어 있는 model assumption과
   instruction-like content는 거부한다.
8. S4.7A는 공식 source card 정확히 5개를 local private 영역에서 검증했고, tracked
   manifest는 bounded evidence 5개의 digest·bytes·locator와 card content digest만
   보존한다. 원 evidence payload와 private card 본문은 Git에 포함하지 않는다.

### 검증 완료된 보정

아래 네 항목은 generator·schema·fixture와 두 runtime consumer에서 함께 검증된다.

1. `answerModes`는 정확히 `CONCISE`, `DETAILED`다.
2. question은 NFC 정규화 뒤 1~1,000 Unicode scalar이고 UTF-8 최대 8KiB다.
3. BGE artifact format은 pinned `ONNX_DATA_ONLY`다. safetensors나 pickle/joblib을
   production artifact로 읽지 않는다.
4. canonical chunk 자체는 overlap 0이다. BGE의 15% 인접 문맥은 ephemeral embedding input일
   뿐 저장 chunk/citation/content hash가 아니다.
5. source card parser는 bounded UTF-8/NFC/LF Markdown과 exact YAML front matter만 읽고
   duplicate key, custom tag, anchor·alias·merge, raw HTML, code fence와 instruction-like
   content를 fail-closed한다.
6. S4.7A official evidence manifest는 `evidenceCount=5`, `rawEvidenceTracked=false`를
   검증하며 corpus generation은 활성화하지 않는다.
7. repository-owned source-card/evidence 파일은 absolute fixed root에서만 읽고 symlink,
   hardlink, raw dot-segment, 소유자 불일치, group/other writable mode와 read race를
   거부한다. 새 파일 publish는 current-owner `0600` anonymous inode와 no-overwrite
   hard-link를 사용한다.

generated catalog만 수동 편집하거나 manifest와 catalog를 함께 임의 변경하면 contract
change digest 검증 또는 semantic parity test가 실패한다.

### 산출물과 검증

| 산출물 | 역할 |
|---|---|
| `catalogs/s4-rag-contract.v1.json` | S4 RAG profile/policy SSOT |
| `catalogs/s4-rag-contract.v1.sha256.json` | canonical bytes 승인 digest의 deterministic manifest |
| `schemas/s4-rag-contract.schema.json` | catalog exact-shape schema |
| `schemas/s4-rag-ask-request.schema.json` | public ask body schema |
| `schemas/s4-rag-admin-policy-selection.schema.json` | admin policy pointer selection schema |
| `schemas/rag-source-card-v1.schema.json` | PROJECT source card exact-shape schema |
| `generate_s4_rag_contracts.py` | generated artifacts drift check |
| `examples/invalid/s4-rag-*.invalid.json` | context-3, profile/policy confusion, public model selection 거부 |
| `examples/rag-source-card-v1.valid.json` | source card positive contract fixture |
| `examples/invalid/rag-source-card-v1.*.invalid.json` | source card shape·encoding·URL·policy negative fixtures |
| `../capstone-rag/manifests/s4-7a-official-evidence.v1.json` | official evidence/card digest 5개 manifest |
| `../workspaces/decision-platform/python-services/app/rag/source_card.py` | bounded Markdown parser와 semantic validator |

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

The canonical catalog, deterministic digest manifest, schemas, fixtures, and
Spring/Python semantic consumers now verify the same approved bytes. Answer modes
are exactly `CONCISE` and `DETAILED`; questions are bounded by 1,000 Unicode
scalars and 8 KiB after NFC normalization; BGE artifacts are pinned to data-only
ONNX; and stored canonical chunks have zero overlap.

S4.7A also locks the exact project source-card shape and fail-closed Markdown
validation boundary. The contract requires canonical UTC `Z`, the exact 20-source
upstream allowlist, and evidence-class authority parity. Repository-owned reads
also require a fixed absolute root, current ownership, non-shared-writable modes,
and symlink/hardlink/dot-segment/race rejection. Five local private official cards
and five bounded official evidence records are verified; only their deterministic
manifest and digests are tracked. Raw evidence and private card bodies remain
untracked, corpus generation remains inactive, and S4.2 model acquisition and
runtime materialization remain separately approval-gated.
