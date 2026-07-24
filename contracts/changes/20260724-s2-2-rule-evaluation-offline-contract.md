# KR: S2.2 Rule evaluation·portfolio offline contract v1

## 변경 이유

Issue #42의 S2.2 구현은 공개 Principle 8개와 system-managed rule 6개, hard/optional evidence,
portfolio source, HOLD/BLOCK business result, 재현 가능한 snapshot hash를 같은 의미로 사용해야
한다. 이 경계를 machine-readable 계약으로 먼저 고정하지 않으면 evaluator, assembler, 테스트가
서로 다른 rule 수·실패 의미·fallback을 구현할 수 있다. 승인 packet
`S22-CONTRACT-AND-OFFLINE-IMPLEMENTATION`은 provider 호출 0, production route/persistence 이연,
S2.1/S2.2 generator 소유권 분리를 전제로 한다.

## 고정한 계약

- `s2-2-system-rule-catalog.v1.json`은 14개 rule의 exact ID, order, metric, operator, unit,
  range, scale, freshness, evidence criticality, threshold/severity source를 고정한다. public 8개는
  S2.1 catalog와 의미가 같아야 하며 S2.2가 그 schema를 다시 생성하지 않는다. system 6개만
  S2.2가 소유한다.
- 실행 분류는 threshold 12, readiness 1, v1 not-applicable 1이다. 따라서 threshold 결과만
  `violations`에 들어가고 `data_freshness_guard`와 `ad_leading_room_guard`는 violation rule ID로
  거절된다.
- S2.1 `PrincipleRule`에 explicit `evidenceRequirement`를 추가한다. hard 6개는 `REQUIRED`
  상수이고 news/disclosure는 `OPTIONAL|REQUIRED`다. legacy version은 원래 불변 row를 바꾸지
  않고 versioned `legacyEvidenceInference`에 따라 활성 missing field는 `REQUIRED`, 비활성은
  rule default로 결정하며 unknown tuple은 거부한다. 새 write와 preset fixture는 필드를 항상
  명시한다.
- `risk_decision`은 `violations`, `issues`, `warnings`, `abstentions`를 분리한다. action은
  `BLOCK > HOLD > WARN > ALLOW`다. BLOCK은 BLOCK violation, HOLD는 최소 한 issue를 요구한다.
  optional evidence missing/stale/error는 같은 `(ruleId, code)`의 warning/abstention 쌍이고 단독
  HOLD/BLOCK을 만들 수 없다. `riskItems.value`는 실제 사용한 non-null number다. disclosure
  missing의 optional WARN과 required HOLD를 별도 fixture로 검증한다.
- public error code와 internal cause를 같은 문자열로 노출하지 않는다. 이 wire에는 bounded public
  code, source, message만 있고 exception class, provider raw body/header, credential, account
  identifier를 넣지 않는다.
- portfolio mode는 `KIS_MOCK|INTERNAL_PAPER`의 explicit selector다. server가 DB-verified owner
  context 안에서 선택하고 automatic fallback은 금지한다. invalid selector는 400
  `VALIDATION_ERROR`, 선택한 context unavailable은 HTTP 200 `HOLD` business result다.
- result는 `principleVersionId + principleVersion`을 pin한다. S2.2의 유일한 production
  source 예외인 내부 `JdbcPrincipleSnapshotAdapter`가 owner + ACTIVE + current version을 한
  SQL로 읽고, missing/cross-owner/inactive를 같은 no-row로 반환한다. HTTP 404 변환과 route는
  S2.3 runtime 소유다.
- `HASH-CANONICALIZATION-S22-V1`은 semantic input과 full snapshot artifact를 별도로 hash한다.
  canonical JSON의 charset, key/array order, decimal, negative zero, trailing-zero 정책과
  included/excluded field를 catalog와 exact hash vector에 고정한다. semantic input에는 full
  order intent, 모든 MetricKey state/value/freshness/source identity, requested/observed optional
  evidence, disclosure completeness/mapping/source refs와 provenance가 들어간다. artifact hash는
  evaluation/retrieval identity까지 포함한 versioned full snapshot exact UTF-8 bytes를 그대로
  사용하며 별도 축약 representation을 만들지 않는다.
- request/response, collection, string, source reference, concurrency, per-source/total deadline,
  per-port logical call의 finite bound를 catalog에 고정한다. 이 값은 S2.2 v1을 바꾸지 않고
  runtime에서 임의 확대할 수 없다.

## 생성·검증 소유권

`generate_principle_contracts.py`는 S2.1 Principle schema/fixture만, 새
`generate_s2_2_contracts.py`는 S2.2 catalog meta-schema, risk result, hash vector와 fixture만
생성한다. 두 explicit `OUTPUTS`는 disjoint이며 generated 파일을 직접 편집하지 않는다. CI는
S2.1 generator check 뒤 S2.2 check를 실행하고, 전체 schema positive/negative fixture와 semantic
ordering/precedence를 검증한다. Spring `processResources`는 canonical S2.2 catalog를 그대로
복사하고 `check`가 source/resource byte equality를 확인한다.

OpenAPI normalizer는 S2.1 implementation mode에서도 `/api/v1/decisions/**`를 거절한다. 이 변경은
Decision controller, HTTP route, Decision DB table/persistence 또는 외부 market/model/balance
source adapter를 추가하지 않는다. S2.2 내부 owner-scoped ACTIVE Principle JDBC read adapter만
명시적 production source 예외다. Port fake는 test source에만 둘 수 있고 production에서 선택
가능한 fallback이 아니다. KIS, OpenDART, Naver, model provider를 포함한 외부 호출은 0이다.

## 호환성과 후속 세션

- S2.2는 domain evaluator, snapshot/portfolio selection policy, offline fake-based integration,
  owner-scoped ACTIVE Principle snapshot용 내부 JDBC read adapter를 소유한다.
- S2.3은 이 read adapter와 source port의 runtime orchestration, HTTP error mapping,
  route/persistence를 별도 승인을 받아 연결한다.
- S3 이후 source 세션은 KIS/order 관련 live adapter와 별도 live-order gate를 소유한다.
- 기존 S2.1 version snapshot은 불변이며 inferred legacy requiredness는 동일 canonical catalog
  rule ID에 대해서만 계산한다. 알 수 없는 tuple을 추정하거나 row를 overwrite하지 않는다.

재현 명령은 `contracts/README.md`에 있고 모두 offline이다.

> 2026-07-24 supersession: production route 또는 저장 decision이 생기기 전에 발견된 현물
> `OrderIntent` field drift는 S2.3 change record에서 breaking cleanup했다.
> `HASH-CANONICALIZATION-S22-V2`/`s2.2-metric-snapshot-v2`가 V1을 대체하며 현물
> `estimatedPrice`와 나머지 7개 exact field를 hash한다. 이 문서의 V1 설명은 당시 이력이고
> runtime compatibility 계약이 아니다.

# EN: S2.2 rule evaluation and portfolio offline contract v1

## Reason

Issue #42 must give the same meaning to eight public Principle rules, six system-managed rules,
hard versus optional evidence, portfolio selection, HOLD/BLOCK business results, and reproducible
snapshot hashes. Without a machine-readable boundary, the evaluator, assembler, and tests could
silently implement different rule counts, failure semantics, or fallback behavior. The approved
`S22-CONTRACT-AND-OFFLINE-IMPLEMENTATION` packet requires zero provider calls, deferred production
routes and persistence, and disjoint S2.1/S2.2 generator ownership.

## Locked contract

- The v1 catalog fixes the exact 14 IDs, order, metrics, operators, units, ranges, scales,
  freshness, evidence criticality, and threshold/severity authorities. The first eight must remain
  semantically aligned with the S2.1 catalog; S2.2 owns only the six system rules.
- Execution is exactly twelve threshold rules, one readiness rule, and one v1 not-applicable rule.
  Only threshold rules may appear in `violations`; the readiness and N/A rule IDs fail closed there.
- Newly written S2.1 Principle rule snapshots and all public read representations have explicit
  `evidenceRequirement`. The six hard rules are `REQUIRED`; news and disclosure allow
  `OPTIONAL|REQUIRED`. Legacy immutable versions use a versioned `legacyEvidenceInference` policy:
  an enabled missing field becomes `REQUIRED`, a disabled one uses its rule default, unknown tuples
  are rejected, and historical rows are never rewritten.
- The result keeps `violations`, `issues`, `warnings`, and `abstentions` separate with
  `BLOCK > HOLD > WARN > ALLOW`. BLOCK requires a BLOCK violation and HOLD requires an issue.
  Optional evidence uses a bidirectional warning/abstention pair keyed by `(ruleId, code)` and cannot
  independently produce HOLD/BLOCK. `riskItems.value` is a non-null number for evidence actually
  used. Separate fixtures lock optional disclosure WARN versus required disclosure HOLD.
- Public bounded codes and sources are separate from internal exception/provider causes. Raw
  provider bodies, headers, secrets, account identifiers, and internal exception names are not wire
  fields.
- Portfolio selection is explicit `KIS_MOCK|INTERNAL_PAPER`, server-authoritative, and owner-scoped.
  There is no automatic fallback. An invalid selector is 400 `VALIDATION_ERROR`; unavailable
  selected context is an HTTP 200 HOLD business result.
- Results pin `principleVersionId + principleVersion`. The internal
  `JdbcPrincipleSnapshotAdapter`, the sole S2.2 production-source exception, reads owner, ACTIVE
  status, and the current immutable version in one SQL statement. Missing, cross-owner, and
  inactive resources return the same no-row result; S2.3 owns the HTTP 404 mapping and route.
- `HASH-CANONICALIZATION-S22-V1` separately hashes semantic input and the full snapshot artifact.
  The catalog and vector lock charset, object/array order, decimal spelling, negative zero,
  trailing-zero rules, and included/excluded fields. Semantic input covers the full order intent,
  every MetricKey state/value/freshness/source identity, requested and observed optional evidence,
  disclosure completeness/mapping/source references, and provenance. The artifact hash uses the
  exact UTF-8 bytes of one versioned full snapshot including evaluation and retrieval identity,
  without a second reduced hash representation.
- Finite limits cover request/response size, collections, text, references, concurrency,
  per-source and total deadlines, and one logical call per port.

## Ownership, compatibility, and deferrals

The S2.1 generator owns only Principle artifacts and the S2.2 generator owns only its catalog
meta-schema, risk result, hash vector, and fixtures. Explicit output manifests are disjoint. CI
checks S2.1 before S2.2, validates positive and negative fixtures plus semantic ordering/precedence,
and Gradle proves that the classpath catalog is an exact byte copy.

The OpenAPI gate rejects `/api/v1/decisions/**` even in S2.1 implementation mode. This change adds no
Decision controller, HTTP route, Decision database persistence, or external market/model/balance
source adapter. The internal owner-scoped ACTIVE Principle JDBC read adapter is the sole explicit
S2.2 production-source exception. Fakes are test-only and cannot become production fallbacks.
Provider calls, including KIS, OpenDART, Naver, and model providers, remain zero.

S2.2 owns the pure evaluator, offline snapshot/portfolio policy, and the internal owner-scoped
ACTIVE Principle snapshot JDBC read adapter. S2.3 will connect that port plus the remaining source
ports to runtime orchestration, HTTP mapping, and persistence under a separate gate; S3 and later
source sessions retain live market/order adapter ownership. Existing S2.1 version rows remain
immutable and unknown legacy tuples are never guessed or overwritten. Reproducible offline
commands are listed in `contracts/README.md`.

> 2026-07-24 supersession: the S2.3 change record fixes a cash-equity `OrderIntent` field drift
> before any production route or stored decision exists. `HASH-CANONICALIZATION-S22-V2` and
> `s2.2-metric-snapshot-v2` replace V1 and hash `estimatedPrice` plus the other seven exact fields.
> The V1 description above remains historical evidence, not a runtime compatibility contract.
