# contracts

워크스페이스 간 유일한 진실 소스. 여기가 고정되기 전까지 각자 폴더 구현을 시작하지 않는다.

| 폴더 | 내용 |
|---|---|
| `schemas/` | principle/signal/backtest_result/risk_decision 등 JSON Schema |
| `proto/` | gRPC `.proto` 정의 (생성 코드는 커밋하지 않음) |
| `openapi/` | springdoc이 생성한 OpenAPI와 diff하는 기준 파일 |
| `examples/` | schema를 통과해야 하는 예시 payload |
| `changes/` | 계약 변경 이유·영향 범위 기록 |

## S2.1 Principle contract v1

`catalogs/s2-1-principle-contract.v1.json`은 S2.1 preset/rule/DTO/operation의
machine-readable 단일 진실이다. object key는 재귀 정렬하고 array 순서는 보존한 UTF-8/LF/2-space
JSON이며 마지막 LF를 포함한 전체 bytes의 SHA-256을 OpenAPI root
`x-s2-1-contract-sha256`에 넣는다. contract ID는
`s2-1-principle-contract/v1`이다. generated schema/fixture를 직접 편집하지 않고
`generate_principle_contracts.py`로 재생성·비교한다.

Implementation PR의 canonical OpenAPI는 실제 Spring controller에서 생성한 S2.1 6개 operation과
auth/health/error runtime path를 포함한다. Principle path/component는 수동 합성하지 않으며
controller 변경과 generated canonical OpenAPI를 같은 변경으로 갱신한다.

### Artifact map

| operation | request | success `data` / page examples | error examples |
|---|---|---|---|
| `GET /api/v1/principle-presets` | 없음 | `schemas/principle-preset-list.schema.json`, `examples/principle-presets.valid.json` | unauthorized/forbidden |
| `POST /api/v1/principles` | `schemas/principle-create-request.schema.json`, `examples/principle-create*.valid.json` | `schemas/principle.schema.json`, `examples/principle.valid.json` | validation, unauthorized, forbidden, payload-too-large |
| `GET /api/v1/principles` | `cursor,size,sort`만 | `schemas/principle-list-response.schema.json`, `examples/principle-list{,-next-page,-empty}.valid.json` | validation/cursor, unauthorized, forbidden |
| `GET /api/v1/principles/{principleId}` | 없음 | `schemas/principle.schema.json`, `examples/principle.valid.json` | validation, unauthorized, forbidden, not-found |
| `PUT /api/v1/principles/{principleId}` | `schemas/principle-update-request.schema.json`, `examples/principle-update{,-no-op}.valid.json` | `schemas/principle.schema.json`, `examples/principle.valid.json` | validation, unauthorized, forbidden, not-found, conflict, version-exhausted, payload-too-large |
| `GET /api/v1/principles/{principleId}/versions` | `cursor,size,sort`만 | `schemas/principle-history-response.schema.json`, `examples/principle-history{,-next-page,-empty}.valid.json` | validation/cursor, unauthorized, forbidden, not-found |

오류의 complete five-field envelope는
`examples/principle-error-{validation,cursor,unauthorized,forbidden,not-found,conflict,version-exhausted,payload-too-large}.valid.json`에
있다. rule tuple/range/scale/severity와 unknown/duplicate/empty/oversized negative fixture는
`examples/invalid/principle*.invalid.json`에 있다. operation/error allowlist와 모든 exact limit은
catalog를 기준으로 한다.

### Consumer matrix / 소비자 영향

| consumer | KR | EN |
|---|---|---|
| Experience Dashboard | exact 3개 preset의 KR/EN 이름·설명·disclaimer와 8 rules를 표시한다. create rules 생략은 deep copy, PUT은 full replacement다. create timeout을 blind retry하지 않고 owner list 후보를 사용자가 확인하게 한다. | Render the exact three localized presets, disclaimer, and eight rules. Omitted create rules deep-copy the preset; PUT is full replacement. Do not blindly retry an indeterminate create. |
| Return Engine | exact 8개 rule tuple과 canonical 순서를 사용한다. ratio는 fraction, loss/MDD는 signed ratio, money/count는 integer이며 `principleId + version` full snapshot을 참조한다. | Consume the exact eight rule tuples in canonical order. Ratios are fractions, loss/MDD are signed ratios, money/count are integers, and `principleId + version` identifies a full snapshot. |
| Decision Platform | DB-verified JWT `sub`만 owner로 사용하고 missing/cross-owner를 동일 404로 처리한다. update는 owner-scoped SQL CAS, history/audit는 append-only다. Principle은 finance idempotency 대상이 아니다. | Trust only the DB-verified JWT subject as owner, collapse missing/cross-owner to one 404, use owner-scoped SQL CAS, keep history/audit append-only, and exclude Principle from finance idempotency. |

### Reproducible checks

아래 명령은 provider key를 읽거나 provider 호출을 만들지 않는다. OpenAPI fixture는 Gradle이
`build/openapi-fixture/openapi.env`에 mode `0600`으로 생성하고 strict parser가 한 descriptor로
검증한다. dotenv text를 shell에서 `source`하지 않는다.

```bash
uv run --frozen python contracts/generate_principle_contracts.py --check
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
workspaces/decision-platform/spring-api/gradlew \
  -p workspaces/decision-platform/spring-api --no-daemon prepareOpenApiFixtureEnv
uv run --frozen python contracts/run_openapi_gate.py \
  --env-file workspaces/decision-platform/spring-api/build/openapi-fixture/openapi.env
```

## S2.2 Rule evaluation offline contract v1

`catalogs/s2-2-system-rule-catalog.v1.json`은 offline RuleEvaluator와
MetricSnapshot assembler가 공유하는 14-rule 단일 진실이다. 앞의 public 8개는 S2.1 catalog의
rule ID·metric·operator·range·scale·순서를 읽기 전용으로 투영하고, 뒤의 system-managed 6개는
S2.2가 소유한다. 실행 분류는 threshold 12개, readiness 1개, v1 not-applicable 1개다.
`violations`에는 threshold rule만 들어갈 수 있으며 readiness 실패는 `issues`, v1 N/A는
`abstentions`로 표현한다.

새 S2.1 version snapshot과 public read 표현은 `PrincipleRule.evidenceRequirement`를 항상
명시한다. 기존 immutable 저장 row는 rewrite하지 않고 catalog의 versioned
`legacyEvidenceInference`에 따라 field가 없을 때 활성 rule은 `REQUIRED`, 비활성 rule은 해당
rule default로 보충하며 unknown tuple은 거부한다. hard 6개 public rule은 `REQUIRED`만 허용하고
news/disclosure는 `OPTIONAL|REQUIRED`를 허용한다. 동일한 disclosure source 오류라도
optional이면 같은 `(ruleId, code)`의 `WARN` + warning/abstention이고 required이면 `HOLD` +
issue다. optional evidence 하나만으로 HOLD/BLOCK을 만들 수 없다.

`schemas/risk_decision.schema.json`은 business result를 `violations`, `issues`, `warnings`,
`abstentions`로 분리하고 precedence를 `BLOCK > HOLD > WARN > ALLOW`로 고정한다. HOLD/BLOCK도
향후 runtime에서 HTTP 200 business result이며 transport/auth/validation error와 섞지 않는다.
`riskItems`는 실제 사용한 evidence만 담으므로 `value`는 non-null number다. `riskSummary`와
`signalSummary`는 선택 필드라서 unavailable component를 빈 성공처럼 강제하지 않는다.

portfolio selector는 `KIS_MOCK|INTERNAL_PAPER`만 허용한다. 선택 권한은 server-side
owner-scoped context에 있고 `INTERNAL_PAPER`만 저장 source `PAPER`에 매핑한다. 자동 fallback은
없다. selector 자체가 잘못되면 HTTP 400 `VALIDATION_ERROR`, 선택한 context가 없거나 사용할 수
없으면 HTTP 200 `HOLD`다. result는 immutable `principleVersionId + principleVersion`을 pin한다.

hash contract `HASH-CANONICALIZATION-S22-V2`와 `s2.2-metric-snapshot-v2`는 semantic input
hash와 snapshot artifact hash를 분리한다. object key는 사전식, 배열은 명시된 stable key,
숫자는 exponent 없는 plain decimal,
`-0`은 `0`, trailing zero는 제거한다. exact input/canonical bytes/SHA-256 vector는
`examples/s2-2-hash-vector.valid.json`에 있다. semantic input은 full order intent, 모든
MetricKey state/value/freshness/source identity, requested/observed optional evidence, disclosure
completeness/mapping/source refs와 provenance를 포함한다. artifact hash는 evaluation/retrieval
identity까지 포함한 versioned full snapshot의 exact UTF-8 bytes를 그대로 사용하며 별도 축약
hash map을 만들지 않는다.

현물 v1 full order intent는
`symbol,side,orderType,quantity,estimatedPrice,estimatedAmount,timeframe,strategyId`다. MARKET과
LIMIT 모두 `estimatedPrice`를 사용하며 `price`/`limitPrice`는 unknown property다.
`estimatedAmount`는 `quantity * estimatedPrice`의 exact overflow-checked 원화 정수 결과다. P2
`derivativeOrderIntent.limitPrice`와 provider wire 가격은 별도 namespace다.

S2.2 generated artifact는 `generate_s2_2_contracts.py`의 explicit `OUTPUTS`만 소유하고 S2.1
generator output과 겹치지 않는다. canonical catalog SHA-256은
`a4714ee9ce3031199b9067919b15931fb42e106857da5f8d8ad7a95bafa8ad7b`다. Spring classpath에는
catalog bytes를 변환 없이 복사하고 Gradle `check`가 byte equality를 검증한다. S2.2에서는
S2.2 커밋 자체는 Decision controller, persistence, OpenAPI path를 추가하지 않았다. S2.3
implementation mode부터 normalizer는 승인된 Decision path 3개와 `S23*` component 5개를 exact
allowlist로 요구한다. 외부 provider adapter는 추가하지 않고 S2.3 stored-source reader만
연결한다.

```bash
uv run --frozen python contracts/generate_principle_contracts.py --check
uv run --frozen python contracts/generate_s2_2_contracts.py --check
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
```

## S2.3 Decision runtime과 stored-source 경계

S2.3 runtime은 `POST /api/v1/decisions/evaluate-order`, owner-scoped detail/audit와 V9
decision/trace/artifact/audit/outbox/idempotency 원자 저장을 제공한다. provider HTTP fallback
없이 저장된 sanitized source만 읽는다. V9의
`market_quote_observations`는 S1.1 producer, KIS_MOCK
`portfolio_balance_observations`/`portfolio_position_observations`는 S3 producer가,
`deterministic_risk_observations`/`daily_order_count_observations`는 deterministic producer가,
`corporation_registry_observations`는 S1.6 producer가 별도 최소권한으로 INSERT한다.
이번 S2.3 prerequisite는 fixture/mock transport/Testcontainers로 offline producer와 projection을
검증하며 provider 호출 권한이 아니다. INTERNAL_PAPER는 기존 ledger의 owner-scoped projection을
사용한다. `decision_app`은 SELECT만 가지며 production seed는 없다. source 구조 자체가 빠지면
`S23_RUNTIME_SOURCE_BLOCKED`, 구조가 준비된 뒤 row가 비거나 stale/incomplete/future이면 typed
unavailable과 persisted 200 HOLD다. 자세한 소유권·hash 전환은
[`20260724-s2-3-decision-contract-lock.md`](changes/20260724-s2-3-decision-contract-lock.md)를
따른다. canonical S2.3 catalog SHA-256은
`58e55ebda0154a079cff3d5c2527da66743cf3fdeeaf063b86b23b581371fab3`이며 tracked OpenAPI의
`x-s2-3-contract-sha256`과 CI에서 일치해야 한다.

## S1.5 KIS 데이터 품질 리포트

S1.5는 public API가 아니라 Decision Platform 내부 CLI `kis-data-quality-report`가 생산하는
sanitized aggregate artifact다. reporter는 selected universe/dataset/collection manifest와 canonical
KIS 일봉만 읽고 provider/network outbound를 만들지 않는다. 실제 KIS 데이터 수집·백필은 이 CLI와
결합하지 않으며 별도의 현재 사용자 exact approval 없이는 실행하지 않는다.

| 계약 | Producer | Consumer | 보존 |
|---|---|---|---|
| `schemas/kis_data_quality_report.schema.json` | `decision-platform:python-data-quality` | 중간·최종보고서 evidence와 S6.5 strict nightly | 시연/평가 종료 후 28일까지 |
| `schemas/kis_data_quality_bundle_manifest.schema.json` | S1.5 secure bundle publisher | latest pointer verifier와 report consumer | report bundle과 동일 |

bundle은 ignored KIS data root의
`quality/YYYY/MM/DD/<reportId>/{report.json,report.md,manifest.json}`에 있고,
`quality/latest-manifest.json`은 완성 bundle 뒤에만 교체한다. report JSON에는 자기 hash나 Markdown
hash를 넣지 않으며 bundle manifest가 두 파일의 exact name/size/SHA-256을 소유한다. 동일
fingerprint 재실행은 existing mode/hash/content를 검증한 no-op이고 손상된 같은 reportId를 덮어쓰지
않는다.

`schemaVersion=1`, `metricPolicyVersion=s1-5-quality-report-v1`이다. 일반 실행은 collection accounting을
생략할 수 있지만 API metric은 `NOT_AVAILABLE`, evidence는 `PARTIAL`이 된다. 보고서 acceptance에는
`--fail-on-quality --require-complete-evidence`를 함께 쓰며 exit precedence는 `2 > 3 > 1 > 0`이다.
event date가 미확정이면 `HOLD_UNTIL_EVENT_DATE_CONFIGURED`, 인용한 reportId는 최종 제출 완료까지
pin한다. S1.5는 canonical Parquet이나 bundle을 자동 삭제하지 않는다.

## S1.3 ECOS/Naver 내부 source snapshot

S1.3은 public REST/gRPC를 추가하지 않는다. Decision Platform이 아래 sanitized JSON을 생성하고,
Return Engine은 이후 합의된 `contracts/`·`artifacts/` handoff 경계에서 manifest를 검증해 소비한다.
다른 workspace의 구현 파일이나 Decision Platform의 임의 로컬 경로를 직접 읽는 방식은 계약이 아니다.

> 구현 상태(2026-07-16): Naver lower-only batch·strict smoke, JSON Schema, offline 회귀와
> 승인된 online smoke를 완료하고 PR #16 merge commit
> `6f439155d9f5ec626fc185f29f2e0bd64ca54780`으로 `main`에 병합했다. Approval A1/A2/A3는
> 실패 evidence로 분리한다. A4
> `approval-a4-692635240394-20260715T055519Z`는 physical `4`·Redis `+4`로 성공했고,
> `semantic-3bb3810728cf` 승인 뒤 registry를 활성화했다. B1
> `approval-b1-23618d21265d-20260715T072151Z`는 ECOS physical `2`·Redis `+2`와 Naver
> physical `1`·Redis `+1`로 성공했다. B1 evidence SHA-256은
> `ecb62e114352439994fa799096a916757ba7fba081f08f1d1b78ec35397d85fb`다.

| 계약 | Producer | Consumer | 보존 |
|---|---|---|---:|
| `schemas/ecos_macro_snapshot.schema.json` | Decision Platform `ecos-macro-collect` | Return Engine macro feature pipeline | 365일 |
| `schemas/naver_news_metadata_snapshot.schema.json` | Decision Platform `naver-news-metadata-collect` | Return Engine sentiment pipeline | 최대 30일, Naver 이용조건 gate 필요 |
| `schemas/source_snapshot_manifest.schema.json` | 두 collector의 secure publisher | handoff consumer·retention command | source snapshot과 동일 |

artifact는 ignored root의
`{source}/YYYY/MM/DD/{uuid-v4}/snapshot.json`과 `manifest.json` 두 파일로 구성한다.
consumer는 `manifest.json`만 완성 marker로 열거하고 schema, 상대경로, SHA-256을 확인한 뒤
snapshot을 읽는다. manifest가 없는 snapshot orphan은 무시한다. provider raw body/header/message,
credential/query가 포함된 provider request URL, auth/header, credential·credential hash, 기사 본문과
로컬 절대경로는 두 파일 모두에 금지한다. schema가 검증하는 정규화된 기사 metadata URL과 고정
provenance URL은 허용한다.
삭제 owner는 `decision-platform:source-snapshot-retention` 하나이며 command는 기본 dry-run,
명시적 `--apply`에서만 manifest를 먼저 지운다.

Naver canonical snapshot은 운영용과 smoke용 포맷을 나누지 않고 동일한 `schemaVersion: 1`에서
`queries` 길이 `1..4`를 허용한다. producer 설정 `NAVER_BATCH_SIZE`는 기본 4이고 `1..4` 범위에서만
하향하며 canonical smoke는 1이다. consumer는 정확히 네 query를 가정하지 않고 snapshot의
`queries` 배열 길이와 manifest `queryCount`를 교차 검증한다. 두 값이 다르거나 0 또는 5 이상이면
artifact를 거부한다. manifest `physicalAttemptCount`도 query당 최대 2회, 즉
`physicalAttemptCount <= 2 * queryCount`여야 한다.

Approval A preflight의 안전 진단은 contract schema를 늘리지 않고 ignored operator-evidence
v1의 `sanitizedPreflight.diagnostic`에만 저장한다. A1/A2/A3는 실패 evidence로 분리하고,
성공 채택 집합은 A4와 원자적으로 성공한 B1만으로 구성한다. B1은 ECOS `D-29..D` 2회를
완전히 성공한 뒤 Naver rank-1 `display=10` 1회를 성공했다. accepted set은 A4+B1의
ECOS `6`+Naver `1`=`7`이며 실패 run을 합산하거나 프로젝트 lifetime 호출 수로 표현하지 않는다.
A3/A4 복구와 B1 검증은 기존 3개 source snapshot schema, public API, DB/Flyway, dependency,
다른 workspace를 변경하지 않았다.

여기서 구현·병합 완료는 Decision Platform producer/storage 경계를 뜻한다. Return Engine이
이 snapshot을 실제 교환 artifact로 소비하는 cross-workspace handoff는 별도 계약 합의 전까지
활성 상태로 간주하지 않는다. S1.3K KRX universe 자동화는 이 계약 파일을 변경하지 않은 별도
내부 트랙이다.
