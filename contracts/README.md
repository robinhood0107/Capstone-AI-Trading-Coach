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

| 계약 | Producer | Consumer | 보존 |
|---|---|---|---:|
| `schemas/ecos_macro_snapshot.schema.json` | Decision Platform `ecos-macro-collect` | Return Engine macro feature pipeline | 365일 |
| `schemas/naver_news_metadata_snapshot.schema.json` | Decision Platform `naver-news-metadata-collect` | Return Engine sentiment pipeline | 최대 30일, Naver 이용조건 gate 필요 |
| `schemas/source_snapshot_manifest.schema.json` | 두 collector의 secure publisher | handoff consumer·retention command | source snapshot과 동일 |

artifact는 ignored root의
`{source}/YYYY/MM/DD/{uuid-v4}/snapshot.json`과 `manifest.json` 두 파일로 구성한다.
consumer는 `manifest.json`만 완성 marker로 열거하고 schema, 상대경로, SHA-256을 확인한 뒤
snapshot을 읽는다. manifest가 없는 snapshot orphan은 무시한다. provider raw body/header/message,
request URL, credential·credential hash, 기사 본문과 로컬 절대경로는 두 파일 모두에 금지한다.
삭제 owner는 `decision-platform:source-snapshot-retention` 하나이며 command는 기본 dry-run,
명시적 `--apply`에서만 manifest를 먼저 지운다.
