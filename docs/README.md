# docs

팀 배포 기준 문서. 원본 관리처는 로컬(개인 정리 폴더)이며, 갱신 시 이 폴더에 같은 파일명으로 동기화한다.

## 현재 완료 상태

S1.6 offline Market Calendar/Event Aggregator는 PR #35, S2.1 Principle CRUD는 PR #41,
S2.2 offline evaluator는 PR #43, S2.3 Decision runtime은 PR #45로 `main`에 병합됐다.
이 S2.4 변경은 저장 observation 기반 portfolio Risk 조회, V10 DB-authoritative 전역
Kill Switch, Decision 평가 guard와 append-only 무효화를 연결한다. provider physical call과
주문 실행은 0건이며 없는 source는 nullable 값과 sanitized warning으로 남긴다. 전체 상태는
[최종_프로젝트_명세서.md](최종_프로젝트_명세서.md) 8.4.2/11장, API 경계는
[API_명세서.md](API_명세서.md) 6장, 재현 명령과 machine-readable artifact 지도는
[contracts README](../contracts/README.md#s24-risk-api와-kill-switch), 실제 운영 절차는
[Decision Platform README](../workspaces/decision-platform/README.md#s24-risk와-kill-switch)를 따른다.

| 문서 | 내용 |
|---|---|
| [최종_프로젝트_명세서.md](최종_프로젝트_명세서.md) | 프로젝트 전체 명세 — 방향, 시스템 구조, 모노레포 설계, 역할분담, 팀별 축소 계획(18.A) |
| [API_명세서.md](API_명세서.md) | Decision Platform API 전체 명세 — REST/gRPC 계약, 오류 코드, fail-closed 정책 |
| [S0_2_P0_계약_통합_필드명_결정.md](S0_2_P0_계약_통합_필드명_결정.md) | S0.2 P0 contracts 통합 기준 — order intent, signal artifact/API view, HMM regime, Return Engine export 필드명 결정 |
| [decision-platform/S1_2_OpenDART_공시위험점수_근거.md](decision-platform/S1_2_OpenDART_공시위험점수_근거.md) | S1.2 OpenDART 점수와 S1.6 offline state/quota/privacy 경계 — 공식 endpoint, 점수 한계, 신뢰성 단계 |
| [중간보고서_작성용_초기설계.md](중간보고서_작성용_초기설계.md) | 중간보고서 작성 가이드 — 문장/표/그림 초안, RISE 심사기준 대응 |
| [선물옵션_모의주문_확장_시나리오.md](선물옵션_모의주문_확장_시나리오.md) | P2 국내선물옵션 확장 설계 (v1 범위 아님, 기본 OFF) |
| [선물옵션_모의주문_확장_시나리오_API_명세서.md](선물옵션_모의주문_확장_시나리오_API_명세서.md) | P2 확장 API 계약 + KIS TR_ID 매핑 |
| [S2.2 offline 계약 변경 기록](../contracts/changes/20260724-s2-2-rule-evaluation-offline-contract.md) | 14-rule evaluator, evidence disposition, portfolio selector, bounds/hash와 S2.3 이연 경계 |
| [S2.3 Decision 계약 잠금](../contracts/changes/20260724-s2-3-decision-contract-lock.md) | 현물 OrderIntent/hash V2, stored quote/KIS_MOCK balance producer-consumer 경계, no-fake HOLD 정책 |
| [S2.4 Risk/Kill Switch 계약 잠금](../contracts/changes/20260725-s2-4-risk-kill-switch-contract.md) | owner-scoped portfolio Risk, DB singleton generation, append-only Decision 무효화와 비대칭 재가동 권한 |
| [ADR-027] | S1.4X 격리 Scala/Haskell 수치 parity Gate 0 결정 |
| `decision-platform/` | Decision Platform 공개 기술 문서 — S1.2 OpenDART 근거와 S1.6 offline 상태 경계를 포함 |

[ADR-027]: adr/ADR-027-s1-4x-isolated-numeric-parity.md
