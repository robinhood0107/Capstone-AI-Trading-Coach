# KR: P2 모델 위험·파생 모의주문 문서 계약 정합화

연결 이슈: #29

## 변경 이유

현재 wire schema를 바꾸지 않으면서 모델 evidence의 권한과 기본 비활성 P2 파생 모의주문의
안전 경계를 공개 설계 문서에서 명확히 한다. HMM confidence나 모델 신호는 판단 근거이지 주문
권한이 아니며, 주문 가능 여부는 deterministic hard guard와 검증된 instrument master가 결정한다.

## 변경 범위

- 현재 5-value market-regime wire enum과 `confidence`의 `0..1` 범위는 유지한다.
- active HMM 구현은 2-state 해석을 사용하고, required evidence를 만들 수 없으면 기존
  `PYTHON_SERVICE_UNAVAILABLE` fail-closed 경계를 따른다고 명시한다.
- P2 파생 모의주문은 계속 기본 OFF다. Synthetic 또는 검증되지 않은 instrument는 주문할 수 없다.
- post-trade naked-short 노출, one-use confirmation, idempotency, reconciliation, risk reservation을
  향후 활성화의 필수 hard guard로 문서화한다.

## 현재 계약 영향

- JSON Schema, REST/gRPC API, DB/Flyway, production 코드, provider 호출 변경: 없음
- production S1.4, S1.4R, S1.4X 변경: 없음
- 다른 팀원 workspace 변경: 없음
- `docs/최종_프로젝트_명세서.md` 변경: 없음

따라서 이번 변경은 wire-compatible 문서 정합화이며 P2 endpoint나 주문 기능을 활성화하지 않는다.
향후 P2를 실제 구현하거나 schema를 추가할 때는 별도 `contracts/changes/` 기록, bilingual Issue/PR,
테스트와 승인 gate가 필요하다.

# EN: Align the P2 model-risk and derivative mock-order documentation contract

Linked issue: #29

## Reason

Clarify model-evidence authority and the safety boundary of the default-off P2 derivative mock-order
design without changing the current wire schema. HMM confidence and model signals are evidence, not
order authority; deterministic hard guards and a verified instrument master decide order eligibility.

## Scope

- Preserve the current five-value market-regime wire enum and the `0..1` confidence range.
- Document the active two-state HMM interpretation and the existing fail-closed
  `PYTHON_SERVICE_UNAVAILABLE` boundary when required evidence cannot be produced.
- Keep P2 derivative mock ordering disabled by default. Synthetic or unverified instruments are not
  orderable.
- Record post-trade naked-short exposure checks, one-use confirmation, idempotency, reconciliation,
  and risk reservation as mandatory guards for any future activation.

## Current contract impact

- No JSON Schema, REST/gRPC API, database, Flyway, production-code, or provider-call change.
- No production S1.4, S1.4R, or S1.4X change.
- No change to another team's workspace.
- No change to `docs/최종_프로젝트_명세서.md`.

This is a wire-compatible documentation clarification and does not activate a P2 endpoint or order
feature. Any future P2 implementation or schema addition requires a separate `contracts/changes/`
record, bilingual Issue/PR, tests, and approval gates.
