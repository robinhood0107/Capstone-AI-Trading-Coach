# KR: S1.3 ECOS/Naver source snapshot 계약 추가

## 변경 이유

Decision Platform이 ECOS 거시지표와 Naver News metadata를 provider raw 응답이 아닌
sanitized internal snapshot으로 생산할 수 있도록 별도 JSON 계약을 추가한다.

## 변경 범위

- `ecos_macro_snapshot.schema.json`: 검증된 registry의 일별 관측치 두 series
- `naver_news_metadata_snapshot.schema.json`: 감사된 universe 기반 News query metadata. 운영 기본은
  네 개이고 lower-only `1..4`를 허용하며 canonical smoke는 한 개를 사용한다
- `source_snapshot_manifest.schema.json`: canonical snapshot hash와 provenance를 담는 commit marker

source snapshot의 `schemaVersion`은 양의 정수 `1`이다. 기존 실험/모델
`artifact_manifest.schema.json`의 SemVer 문자열 및 `artifacts/{workspace}/{runId}` 교환 계약과
서로 다른 내부 producer 계약이며 암묵 변환하거나 혼용하지 않는다.

Naver 운영 snapshot과 1-query smoke는 별도 포맷이 아니다. 동일한 canonical 계약에서 `queries`
길이와 manifest `queryCount`가 같은 `1..4` 값이어야 하며 0개, 5개 이상, count mismatch는 거부한다.
manifest `physicalAttemptCount`는 query당 최대 2회여야 한다.
S1.3 producer 코드·JSON Schema·offline 회귀·승인된 online 검증은 완료됐고 PR #16 merge commit
`6f439155d9f5ec626fc185f29f2e0bd64ca54780`으로 `main`에 병합됐다. 이 계약을 Draft라고 부르는
범위는 Return Engine과의 cross-workspace 배포/handoff가 아직 활성화되지 않았다는 뜻이며,
S1.3 구현이나 테스트가 미완료라는 뜻이 아니다. 따라서 lower-only 정렬에서 `schemaVersion`을
올리지 않는다. Approval A1/A2/A3는 실패 evidence로 분리하고 A4
`approval-a4-692635240394-20260715T055519Z`와 B1
`approval-b1-23618d21265d-20260715T072151Z`만 성공 채택한다. accepted set은 A4+B1의
ECOS `6`+Naver `1`=`7`이며 lifetime 호출 수가 아니다. A3/A4 복구와 B1 검증은
기존 schemaVersion·public API·DB/Flyway·dependency·다른 workspace 계약을 바꾸지 않았다.

## 보안·운영 영향

- credential, credential/query가 포함된 provider request URL, auth/header, provider raw
  body/header/message, 로컬 절대경로는 금지한다. 정규화된 기사 metadata URL과 고정 provenance URL은
  canonical 계약에 따라 허용한다.
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
- Naver uses one canonical snapshot format for both normal collection and smoke. The operational
  default is four queries, lower-only values `1..4` are accepted, and canonical smoke uses one query.
  The `queries` length and manifest `queryCount` must match; zero, five or more, and count mismatches
  are rejected. `physicalAttemptCount` must not exceed two attempts per query.
- The source snapshot uses integer `schemaVersion: 1`; it is distinct from the SemVer experiment/model
  artifact bundle and must not be implicitly converted or mixed with it.
- The S1.3 producer code, JSON Schema, offline regression verification, and separately approved
  online smoke are complete and were merged to `main` by PR #16 merge commit
  `6f439155d9f5ec626fc185f29f2e0bd64ca54780`. Draft refers only to the cross-workspace
  deployment/handoff to the Return Engine not being active; it does not mean the S1.3
  implementation or verification is incomplete. The lower-only alignment therefore keeps
  `schemaVersion: 1`. Approval A1, A2, and A3 remain separate failed evidence; only A4
  `approval-a4-692635240394-20260715T055519Z` and B1
  `approval-b1-23618d21265d-20260715T072151Z` are accepted. The accepted set is ECOS `6` plus
  Naver `1` = `7`, not a lifetime-call claim. The A3/A4 recovery and B1 verification did not change
  the existing schema version, public API, DB/Flyway, dependencies, or another workspace contract.
- Credentials, provider request URLs containing credentials or queries, authentication material,
  provider raw payloads, and local absolute paths are forbidden. Normalized article metadata URLs and
  fixed provenance URLs remain allowed by the canonical contract. The manifest is the commit marker.
- ECOS retention is 365 days, Naver retention is 30 days, and the delete owner is
  `decision-platform:source-snapshot-retention`.
- S1.3 implements only the Decision Platform producer/storage boundary. Return Engine handoff and
  public REST/gRPC remain later integration work.
