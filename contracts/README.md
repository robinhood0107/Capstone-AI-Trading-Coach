# contracts

워크스페이스 간 유일한 진실 소스. 여기가 고정되기 전까지 각자 폴더 구현을 시작하지 않는다.

| 폴더 | 내용 |
|---|---|
| `schemas/` | principle/signal/backtest_result/risk_decision 등 JSON Schema |
| `proto/` | gRPC `.proto` 정의 (생성 코드는 커밋하지 않음) |
| `openapi/` | springdoc이 생성한 OpenAPI와 diff하는 기준 파일 |
| `examples/` | schema를 통과해야 하는 예시 payload |
| `changes/` | 계약 변경 이유·영향 범위 기록 |

## S1.3 ECOS/Naver 내부 source snapshot

S1.3은 public REST/gRPC를 추가하지 않는다. Decision Platform이 아래 sanitized JSON을 생성하고,
Return Engine은 이후 합의된 `contracts/`·`artifacts/` handoff 경계에서 manifest를 검증해 소비한다.
다른 workspace의 구현 파일이나 Decision Platform의 임의 로컬 경로를 직접 읽는 방식은 계약이 아니다.

> 구현 상태(2026-07-15): Naver lower-only batch·strict smoke, JSON Schema, offline 회귀와
> 승인된 online smoke를 완료했다. Approval A1/A2/A3는 실패 evidence로 분리한다. A4
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
