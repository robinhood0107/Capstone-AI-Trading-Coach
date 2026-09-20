# P1 선택적 근거 실패와 장전 준비

2026-09-08 사용자 확정 결정에 따른 runtime 의미 변경이다. 역사적 계약과 migration bytes는 보존한다.

- NEWS_SCREENING의 ABSTAIN/수집·AI 오류는 후보 제외 사유가 아니다. 근거가 검증된
  AVAILABLE/VETO_BUY만 제외한다. 실패 사유는 SCREENING_ERROR/ABSTAIN으로 구분한다.
- AI_JUDGING 오류/응답 부적합은 기존 deterministic 후보 순위를 사용한다. 앞단에서 제외한
  VETO 후보는 다시 추가하지 않는다. NEWS_CHECKING 오류도 ABSTAIN으로 진행한다.
- AI OFF이면 선택적 AI transport 호출을 생략한다. 가격·잔고·owner·RiskEngine·주문 멱등성은 유지한다.
- 공시 뉴스용 reader는 기존 projection과 일일 collector 지원집합을 사용한다. 전체 위험 점수용
  reader의 required operations는 유지한다. COMPLETE_EMPTY, PARTIAL, COLLECTION_FAILED,
  NOT_COLLECTED, UNMAPPED를 구분하며 READ_FAILED를 정상 empty로 표시하지 않는다.
- 기존 runtime에서 08:30 장전 준비를 시작하고 08:50 완료를 목표로 한다. 현재 target을 과거
  catch-up보다 먼저 처리한다. 주문 claim은 09:30 이후다. 추론의 불가 반환은 준비 성공이 아니다.

public REST operation/DTO와 생성 OpenAPI bytes 변경은 없다. 원칙 snapshot, Strong LLM 통합,
뉴스 v2 및 화면 상태 확장은 별도 후속 구현이다. focused 검증은 보고서에 기록한다.
08:30 시작은 실제 inference p95 검증 전의 구현값이며 운영 적시성·배포 완료를 뜻하지 않는다.
