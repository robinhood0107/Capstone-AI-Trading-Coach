# 문서 색인과 현재 상태

<!-- P1_FULL_APP_V3_AUTHORITY_BEGIN -->
> **1.0.0 current authority (2026-08-27):** Owner-First Phase A는 `OWNER_HANDOFF_READY=TRUE`지만
> Team A/B actual 결과와 physical/soak gate가 남아 GitHub `1.0.0` Release는 없다. 현재 hard/non-blocking gate와 증거 규칙은
> [P1 Owner-First full-app v3 권위와 게이트](decision-platform/P1_1_0_0_OWNER_FIRST_V3_권위_및_게이트.md)가 소유한다.
> 아래 과거 P1/placeholder marker는 해당 시점의 기록이며 v3 완료 증거가 아니다.
<!-- P1_FULL_APP_V3_AUTHORITY_END -->

이 파일은 공개 문서의 단일 상태 권위다. 기능의 상세 계약은
[최종 프로젝트 명세서](최종_프로젝트_명세서.md), 공개 REST/gRPC 계약은
[API 명세서](API_명세서.md), 기계 판독 계약은 [contracts](../contracts/README.md)를 따른다.
과거 시점의 SHA, 실행 영수증, 연구 결과와 `contracts/changes/**` 기록은 재현 자료이며 현재 상태를
덮어쓰지 않는다.

## 상태 용어

| 상태 | 한글 의미 |
|---|---|
| `VERIFIED_ACTIVE` | 현재 검증된 활성 기능 |
| `VERIFIED_OPTIONAL` | 검증됐지만 명시적 선택이 필요한 기능 |
| `RESEARCH_ONLY` | 연구·재현 전용이며 production 권한 없음 |
| `EXTERNAL_BLOCKED` | 외부 산출물 또는 별도 승인 대기 |
| `RETIRED` | 실행 권한이 폐기되거나 비활성화됨 |

이 다섯 값만 현재 상태 표에 사용한다. `MERGED`, `IMPLEMENTED_DRAFT`, 과거 commit SHA 같은 표현은
구현 이력이지 현재 실행 권한이 아니다.

## 현재 상태

| 범위 | 상태 | 현재 권위 |
|---|---|---|
| Principle, Decision, Risk, Kill Switch | `VERIFIED_ACTIVE` | 저장된 근거와 현재 DB identity를 사용하며 근거가 없으면 persisted `HOLD`, 권한 없는 요청은 fail-closed |
| DB async | `VERIFIED_ACTIVE` | 기본 adapter. domain row와 outbox만 한 DB transaction에 기록 |
| Kafka async | `VERIFIED_OPTIONAL` | 검증된 local opt-in 경로. production hard dependency가 아니며 자동 fallback 없음 |
| S7/S8 offline demo | `VERIFIED_ACTIVE` | synthetic projection E2E이며 실제 Return Engine 성과로 승격하지 않음 |
| Public RAG | `VERIFIED_ACTIVE` | `FULL_READY`, `voyage_context_4_1024_v1`, sources 142, chunks 7,871, document batches 63/63 |
| Owner library | `VERIFIED_OPTIONAL` | 사용자가 Voyage 또는 local BGE를 명시적으로 선택. default, 자동 판단, fallback 없음 |
| LightGBM | `RESEARCH_ONLY` | production Signal은 항상 `ABSTAIN/MISSING_EVIDENCE` |
| S1.4X | `RESEARCH_ONLY` | 격리된 Scala/Haskell 수치 parity 경로. production hot-swap이 아님 |
| S6.6/S6.7 cross-market runtime | `RETIRED` | scheduler, public endpoint, Risk/order overlay와 실행 capability 없음 |
| Core `1.0.0` release | `EXTERNAL_BLOCKED` | 최종 보안·CI·merge SHA·release 검증 전에는 공개 release로 선언하지 않음 |
| Return Engine 6종 artifact | `EXTERNAL_BLOCKED` | 실제 artifact와 독립 재계산 필요 |
| Team A Dashboard integration | `EXTERNAL_BLOCKED` | 실제 consumer 통합 필요 |
| KIS_MOCK v3 reconciliation | `EXTERNAL_BLOCKED` | fresh exact approval과 reconciliation 필요 |
| P1 전체 종결 | `EXTERNAL_BLOCKED` | Core release와 외부 산출물 종결을 모두 완료해야 함 |

Public RAG의 위 상태는 보존된 완료 결과다. 기존 Voyage 실행이나 public BGE inference를 다시 호출하지
않는다. Public BGE inference는 0이며 owner BGE만 사용자가 고른 local 실행이다.

RAG, news, analyst, MCP, LLM은 설명과 근거만 만든다. Decision, Signal, RiskDecision, order,
decision hash를 생성하거나 변경할 권한이 없다. Provider, live account, live order는 별도 승인 전까지
호출하지 않는다.

## 단순한 E2E 신뢰 경계

사용자 요청의 권한 경계는 다음 한 흐름으로 고정한다.

```text
authenticated API request
  -> API가 owner와 operation binding을 계산
  -> decision_identity authority가 현재 role/securityVersion을 조회
  -> authority가 Ed25519 capability를 서명하고 PostgreSQL에 1회 등록
  -> API는 공개키와 exact binding을 검증
  -> PostgreSQL이 같은 capability를 transaction 안에서 정확히 한 번 소비
```

권한 정책은 `OWNER`와 `ADMIN_ONLY` 두 종류만 둔다. API는 private key를 갖지 않고 authority는 domain
write를 하지 않는다. Owner는 request body에서 신뢰하지 않고 인증 identity와 등록된 capability에서만
유도한다. 외부 provider 실행은 이 actor capability와 섞지 않고 공통 `p1-approval-packet.v2` 검증과
PostgreSQL one-shot claim을 별도로 통과한다. 이 구조는 공개 HTTP/OpenAPI payload를 변경하지 않는다.

## 공개 문서 분류

| 분류 | 배치와 의미 |
|---|---|
| `PUBLIC_CANONICAL` | 이 색인, 최종 명세, API, 배포·운영 문서와 보안 정책 |
| `PUBLIC_REFERENCE` | ADR, 사용 가이드, 모듈 포인터와 설명 자료 |
| `PUBLIC_RESEARCH` | 초기설계, 실험·연구 결과. production 권위가 아님 |
| `PRIVATE_CANDIDATE` | 공개 제품에 필요 없는 내부 과정 자료. 현재 tree에서 제거 |
| `MACHINE_AUDIT` | `contracts/changes/**`, RAG source card, generated/hash-bound 감사 기록. 이동·재작성하지 않음 |

분류 원장은 Git 밖의 감사 파일로만 관리한다. 로컬 전용 경로, 개인 자료, secret, raw provider data,
계좌정보는 공개 문서와 Git history에 추가하지 않는다.

## 주요 문서

### 기준 문서

- [최종 프로젝트 명세서](최종_프로젝트_명세서.md)
- [API 명세서](API_명세서.md)
- [보안 정책](../SECURITY.md)
- [계약과 검증 방법](../contracts/README.md)

### 실행과 운영

- [P1 Owner-First full-app v3 권위와 게이트](decision-platform/P1_1_0_0_OWNER_FIRST_V3_권위_및_게이트.md)
- [1.0.0 handoff 시작](handoff/START_HERE.md)
- [Team A handoff](handoff/team-a/README.md)
- [Team B handoff](handoff/team-b/README.md)
- [Owner integration handoff](handoff/owner/README.md)
- [P1 Owner input pack·synthetic golden 운영](decision-platform/P1_OWNER_INPUT_PACK_GOLDEN_운영_가이드.md)
- [P1 artifact importer·projection 운영](decision-platform/P1_ARTIFACT_IMPORTER_PROJECTION_운영_가이드.md)
- [P1 Return inference runtime 운영](decision-platform/P1_RETURN_INFERENCE_RUNTIME_운영_가이드.md)
- [통합 담당자 선행 완료 체크리스트](decision-platform/P1_OWNER_선행_완료_체크리스트.md)
- [새 PC에서 같은 환경 실행하기](decision-platform/P1_GIT_PULL_동일환경_재현_가이드.md)
- [Team A 대시보드 완료 요청](decision-platform/P1_TEAM_A_DASHBOARD_완료_요청서.md)
- [Team B 예측·백테스트 엔진 완료 요청](decision-platform/P1_TEAM_B_RETURN_ENGINE_완료_요청서.md)
- [S7-S8/P1 구현·운영 핸드오프](decision-platform/S7_S8_P1_구현_및_운영_핸드오프.md)
- [P1 Offline Demo 배포·검증](decision-platform/P1_OFFLINE_DEMO_배포_및_검증.md)
- [S8 offline demo 시나리오](decision-platform/S8_오프라인_시연_시나리오.md)
- [S8 사용자 테스트 kit](decision-platform/s8-user-test-kit/README.md)
- [외부 artifact 수신 절차](decision-platform/P1_실물_artifact_잔여_체크리스트.md)

### 공개 연구·참고

- [중간보고서 작성용 초기설계](중간보고서_작성용_초기설계.md) — 역사적 초안이며 현재 상태 권위가 아님
- [S1.4X 격리 수치 parity ADR](adr/ADR-027-s1-4x-isolated-numeric-parity.md)
- [S6 금융공학 실행·검증](decision-platform/S6_금융공학_실행_및_검증.md)

## 외부 종결 순서

Core `1.0.0` 공개 뒤에도 다음 순서가 끝날 때까지 P1 전체 상태는 `EXTERNAL_BLOCKED`다.

```text
Return Engine exact 10종 artifact와 상위 manifest
  -> Team A Dashboard integration
  -> fresh KIS_MOCK v3 exact approval/reconciliation
  -> docs-only closure PR
  -> P1 overall complete
```

KIS 승인 전 provider/account/order 호출 수는 0이어야 한다.

<div hidden>

<!-- P1_HISTORICAL_COMPATIBILITY_AUDIT_BEGIN
These historical machine markers are verified for preserved Pre-S5 contracts and are not current status values.
PRE_S5_DOC_TRUTH_FREEZE_VERIFIED
| S1.3G | `OFFLINE_ONLY` |
Decision Platform existing GDELT offline aggregate producer unchanged
HTTP transport/executor/outbound 0
PRE_S5_RAG_GLOBAL_NEWS_CONTRACT_LOCKED=1
OA112_ACTIVE_CONTRACT_LOCKED
S4_7D_OA112_PHYSICAL_ACTIVATION=NOT_MATERIALIZED
| S4.7D v2 runtime | `IMPLEMENTED_DRAFT` |
S4_8A=CONTRACT_LOCKED
S4_8_CORE6_V2=CONTRACT_LOCKED
S4_8_CORE6_LOCAL_PROBE_RUNTIME=IMPLEMENTED_DRAFT
S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE
VERTEX_MODEL_ID
P1_HISTORICAL_COMPATIBILITY_AUDIT_END -->

<!-- PRE_S5_SOLO_ROLE_CATALOG_BEGIN -->
PRE_S5_DOC_TRUTH_FREEZE_ADDENDUM_VERIFIED
PRE_S5_EXECUTION_OWNER=DECISION_PLATFORM
S1_3G=OFFLINE_ONLY
NEW_TEAMMATE_IMPLEMENTATION_TASKS=0
NEW_TEAMMATE_ISSUES_OR_PRS=0
REQUIRED_TEAMMATE_ARTIFACTS_FOR_S5_ENTRY=0
TEAMMATE_WORKSPACE_DIFF=0
GDELT_MODE=DECISION_PLATFORM_OFFLINE_REFERENCE_ONLY
GDELT_EXISTING_OFFLINE_PRODUCER_UNCHANGED=1
GDELT_HTTP_TRANSPORT=NOT_ACTIVATED
GDELT_OUTBOUND_IMPLEMENTATION=0
GDELT_OUTBOUND_CALLS=0
GDELT_OFFLINE_REFERENCE_ONLY=1
NAVER_ACTIVE_PROVIDER_RUNTIME_STORAGE=RETIRED
RAG_NEWS_ANALYST_DECISION_SIGNAL_ORDER_AUTHORITY=0
STRONG_LLM_JUDGEMENT_AUTHORITY=CANDIDATE_RANK_VETO_SIZE_ONLY
PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES
HISTORICAL_TEAM_ROLE_CATALOG=TEAM_B:RETURN_ENGINE|LSTM|RULE_BASELINE|BACKTEST;TEAM_A:EXPERIENCE_DASHBOARD
HISTORICAL_TEAM_ROLE_STATUS=HISTORICAL_SUPERSEDED
TEAMMATE_ARTIFACT_ABSENCE=NOT_AVAILABLE_OR_ABSTAIN
<!-- PRE_S5_SOLO_ROLE_CATALOG_END -->

</div>
