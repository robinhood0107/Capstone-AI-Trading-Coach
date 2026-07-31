# docs

팀 배포 기준 문서. 원본 관리처는 로컬(개인 정리 폴더)이며, 갱신 시 이 폴더에 같은 파일명으로 동기화한다.

## 현재 완료 상태

`main`에 병합된 S1~S3 범위와 S4 working-tree 초안을 구분한다.

- S1.6 offline Market Calendar/Event Aggregator, S2.1 Principle CRUD, S2.2 offline
  evaluator, S2.3 Decision runtime, S2.4 Risk/Kill Switch, S3 mock/internal-paper 흐름은
  각 세션의 merge evidence를 기준으로 판정한다.
- S4 RAG static catalog, P0 source registry 20개, owner-scoped source metadata API,
  offline safe I/O/parser/chunker, exact project source card 30개와 pinned BGE full
  generation은 각 merge evidence를 기준으로 판정한다. S4.2B local benchmark는 exact
  30/30 DB parity와 bounded admin CAS를 통과했고 provider physical call은 0이다.
- Voyage/Gemini/OpenAI 호출, `/rag/ask`, 암호화 history, retrieval/RRF/evaluator,
  S5 LightGBM은 아직 완료되지 않았다.
- S4.8A/B/C·S6.6/S6.7 교차시장 계획 타당성은 `PLAN_FEASIBILITY=GO`, 구현 상태는
  `IMPLEMENTATION=SPEC_ONLY / NOT_IMPLEMENTED / PLANNED`다. 월 데이터 비용 `0원`,
  offline fixture·지연/EOD 우선이며 기관용/실시간 feed는 P1 완주 조건이 아니다.
- 순서 0 `S4.READ`에서 관련 공개·private 명세를 EOF까지 읽고 receipt를 남긴 뒤,
  S4.8A contract-only PR을 먼저 검증·병합한다. 일곱 schema,
  catalog v2, contract-change, fixture/golden vector를 먼저 고정하기 전에는 코드·DB·API
  구현이나 focused 명령을 가용하다고 표시하지 않는다. 문서 정의 자체는 완료 증거가 아니다.
- 기존 Decision/RAG/Signal v1/v2 payload 추가 필드는 0이고 새 framework·cloud·Kafka hard
  dependency를 만들지 않는다.
- provider/live account/live order 호출은 승인 packet 없이는 항상 0이다.
- S1.3G active 뉴스 authority는 Naver provider/runtime/storage를 퇴역시키고 GDELT의
  aggregate-only synthetic fixture 계약을 사용한다. 기사 metadata 저장, RiskDecision/hash/order
  권한, S5 feature 주입은 모두 0이며 실제 GDELT 호출은 별도 승인 전까지 0이다.

S4부터 P1 종료까지의 공개 방향과 교차시장 역할·검증 순서는
[최종 프로젝트 명세서](최종_프로젝트_명세서.md),
REST/gRPC 계약은 [API 명세서](API_명세서.md), machine-readable S4 profile/policy는
[contracts README](../contracts/README.md#s4-rag-profilepolicy-catalog)를 따른다.
교차시장 문서 전용 계약 계획은
[contracts README](../contracts/README.md#s48-교차시장애널리스트-계획-계약)를 따르며,
machine-readable 계약 링크는 S4.8A contract-lock이 실제 생성된 뒤에만 추가한다.
exact 42개 integration target 행과 exact KIS 18개 allowlist는
Git으로 추적하지 않는 로컬 전용 자료수급 레지스트리가
authority이며 공개 문서에는 전체 목록을 복제하지 않는다.

| 문서 | 내용 |
|---|---|
| [최종_프로젝트_명세서.md](최종_프로젝트_명세서.md) | 프로젝트 전체 명세 — 방향, 시스템 구조, 모노레포 설계, 역할분담, 팀별 축소 계획(18.A) |
| [API_명세서.md](API_명세서.md) | Decision Platform API 전체 명세 — REST/gRPC 계약, 오류 코드, fail-closed 정책 |
| [S4 RAG profile/policy 결정 기록](../contracts/changes/20260729-s4-rag-contract-catalog.md) | 정확히 두 embedding profile, 세 policy, public model 선택 금지와 negative fixture |
| [ADR-038](adr/ADR-038-naver-retirement-gdelt-aggregate.md) | Naver active 뉴스 경계 퇴역, GDELT aggregate-only ownership와 ABSTAIN 권한 결정 |
| [S1.3G 뉴스 계약 잠금](../contracts/changes/20260731-s1-3g-naver-retirement-gdelt-aggregate-lock.md) | GDELT observation/news summary v2, 기사 metadata 저장 0, 판단 권한 없음 |
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
