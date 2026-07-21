# KR: S1.5 KIS 데이터 품질 리포트 계약 추가

## 변경 이유

S1.1 canonical KIS 일봉의 품질을 provider 재호출 없이 재현 가능하게 평가하고, JSON과 Markdown이
동일한 sanitized aggregate를 표현하도록 report와 bundle completion manifest 계약을 추가한다.

## 변경 범위

- kis_data_quality_report.schema.json: manifest로 고정한 입력 provenance, 세 상태 축, 순서가 고정된
  Metric Policy v1 결과, bounded sample, 데이터 분류와 retention metadata
- kis_data_quality_bundle_manifest.schema.json: immutable report.json/report.md의 exact filename,
  byte size, SHA-256과 bundle identity
- positive/negative fixture: 정상 report/bundle, forbidden raw payload, denominator 0 계약 위반,
  traversal bundle path

이 계약은 Decision Platform의 내부 CLI 산출물이며 public REST/gRPC/OpenAPI나 다른 workspace handoff를
추가하지 않는다. report producer의 provider/network outbound는 0이고 실제 KIS read-only 수집은
별도 exact approval gate 뒤에만 수행한다. reportId는 analysis fingerprint 기반 UUIDv5이며 report
자체에는 self hash나 Markdown hash를 넣지 않는다.

## 보안·운영 영향

- schema는 provider raw body/header/query/URL, credential/token/account/PII, raw OHLCV와 local absolute
  path를 위한 필드를 제공하지 않는다.
- sample은 symbol/session/rule과 derived bounded 값만 허용한다.
- ordinary bundle은 시연/평가 종료 후 28일까지 owner
  decision-platform:python-data-quality가 보존한다. 날짜가 확정되지 않으면
  HOLD_UNTIL_EVENT_DATE_CONFIGURED, 보고서 인용본은 최종 제출 완료까지 pin한다.
- canonical KIS Parquet 삭제와 automatic prune/scheduler는 포함하지 않는다.

# EN: Add the S1.5 KIS data-quality report contracts

## Reason

Add report and bundle-completion contracts so canonical S1.1 KIS daily data can be evaluated
reproducibly without provider calls and the JSON and Markdown outputs represent the same sanitized
aggregate.

## Scope and impact

- kis_data_quality_report.schema.json defines manifest-pinned input provenance, three independent
  status axes, ordered Metric Policy v1 results, bounded samples, data classification, and retention.
- kis_data_quality_bundle_manifest.schema.json owns exact filenames, byte sizes, SHA-256 values,
  and bundle identity for immutable report.json and report.md.
- Positive and negative fixtures cover normal reports/bundles, forbidden raw payloads, an invalid
  zero-denominator rate, and path traversal.
- This is an internal Decision Platform CLI artifact. It adds no public REST, gRPC, OpenAPI, or
  cross-workspace handoff. Reporter provider/network outbound is zero, and actual KIS read-only
  collection remains behind a separate exact approval gate.
- The schemas expose no field for provider raw data, credentials, tokens, account/PII, raw OHLCV, or
  local absolute paths. Bounded samples allow only symbol/session/rule and derived values.
- The retention owner is decision-platform:python-data-quality. Ordinary bundles are held until
  28 days after the evaluation event, an unknown event date is held, and cited report IDs are pinned
  through final submission. Canonical Parquet deletion and automatic pruning/scheduling are excluded.
