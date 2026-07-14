# KR: S1.3 ECOS/Naver source snapshot 계약 추가

## 변경 이유

Decision Platform이 ECOS 거시지표와 Naver News metadata를 provider raw 응답이 아닌
sanitized internal snapshot으로 생산할 수 있도록 별도 JSON 계약을 추가한다.

## 변경 범위

- `ecos_macro_snapshot.schema.json`: 검증된 registry의 일별 관측치 두 series
- `naver_news_metadata_snapshot.schema.json`: 감사된 universe 기반 네 개 News query metadata
- `source_snapshot_manifest.schema.json`: canonical snapshot hash와 provenance를 담는 commit marker

source snapshot의 `schemaVersion`은 양의 정수 `1`이다. 기존 실험/모델
`artifact_manifest.schema.json`의 SemVer 문자열 및 `artifacts/{workspace}/{runId}` 교환 계약과
서로 다른 내부 producer 계약이며 암묵 변환하거나 혼용하지 않는다.

## 보안·운영 영향

- credential, request URL, auth/header, provider raw body/header/message, 로컬 절대경로는 금지한다.
- manifest가 존재할 때만 snapshot을 완성된 산출물로 취급한다.
- ECOS 보존은 365일, Naver 보존은 30일이며 삭제 owner는
  `decision-platform:source-snapshot-retention`이다.
- S1.3은 Decision Platform producer/storage만 구현한다. Return Engine 직접 workspace 수정이나
  public REST/gRPC는 포함하지 않으며 handoff는 후속 통합 계약으로 확정한다.

# EN: Add S1.3 ECOS/Naver source snapshot contracts

## Reason

Add dedicated JSON contracts so the Decision Platform can produce sanitized internal ECOS macro
and Naver News metadata snapshots without retaining provider raw responses.

## Scope and impact

- Separate ECOS, Naver, and source-manifest schemas with positive and negative examples.
- The source snapshot uses integer `schemaVersion: 1`; it is distinct from the SemVer experiment/model
  artifact bundle and must not be implicitly converted or mixed with it.
- Credentials, request URLs, authentication material, provider raw payloads, and local absolute paths
  are forbidden. The manifest is the commit marker.
- ECOS retention is 365 days, Naver retention is 30 days, and the delete owner is
  `decision-platform:source-snapshot-retention`.
- S1.3 implements only the Decision Platform producer/storage boundary. Return Engine handoff and
  public REST/gRPC remain later integration work.
