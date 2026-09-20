# 2026-09-09 세계 뉴스 보관·자본정책 additive 변경

## 범위

- V160: 일반 뉴스 content version은 `firstSeenAt + 30일` 뒤 정리하되, 만료되지 않은 RAG 답변이
  실제 사용한 citation은 title/짧은 quote/content hash/출처를 답변 만료까지 pin한다. 삭제된 동일
  content version은 tombstone으로 재수집을 거부해 `firstSeenAt`을 새 시각으로 만들지 않는다.
- V161: 기존 automation v1~v3 정책/단일 주문 API를 보존하고 다음 XKRX 세션부터 적용되는
  `automation-capital-policy.v1`을 추가한다. 초기값은 재투자 ON, 현금 여유 100bps, 목표비중 편차
  200bps, 최소 조정 10,000원·1주, 세션 최대 주문 3건이다. 이 값은 수익성 최적값이 아니다.
- V161 주문 실행 원장은 session의 원칙/기존 정책/자본정책과 exact intent를 고정하고 한 번에
  실행 하나만 `SUBMITTING|PENDING_RECONCILIATION`일 수 있게 한다. 앞 주문 미확정이면 다음 주문은
  열리지 않고, 같은 멱등키 replay는 재제출하지 않는다.
- V162: 완료된 GQG/GEMG 분별 cursor를 provider socket 앞에서 재사용한다.
- root OpenAPI는 기존 exact-80을 보존하고 `GET|PUT /api/v4/automation/capital-policy`와
  `GET /api/v4/automation/capital-status`를 더해 exact-83이다.

## 권한과 비권한

- `decision_market_writer`는 기존 definer append와 완료 cursor 확인만 실행하며 뉴스 table 직접
  권한은 없다. retention은 `decision_worker`가 bounded definer function을 dry-run 기본으로 호출한다.
- 세계 뉴스는 조회/RAG만이며 Decision, Signal, RiskDecision, order, decision hash, VETO 권한이 없다.
- 자본 planner가 만드는 수량은 RiskEngine이 준 `riskQuantity`를 상한으로만 소비한다. AI는 수량을
  만들지 않는다. 미확정 매도대금은 `brokerBuyableCashKrw`에 실제 반영되기 전 재사용하지 않는다.
- 이 변경은 다중 주문 원장/계획의 구현이다. 기존 single-run daemon의 실제 KIS 다중 제출 소비
  연결과 운영 활성화는 별도 검증 전 완료로 표시하지 않는다.

## 외부 실행 상한

지속 최신 cycle은 GDELT 공개 HTTPS의 GQG/GEMG 두 target만 사용한다. target당 redirect 포함 최대
2 physical call, cycle 최대 4, retry 0, 압축 8MiB/해제 32MiB, 예상 비용 0달러다. raw body/header는
저장하지 않고 bounded metadata/citation과 content-free collection receipt만 저장한다. 첫 terminal
실패 뒤 남은 target 호출은 0이다. 최근 24시간/30일 복구는 상주 최신 cycle과 분리하고 one-shot의
`--max-files`/`--physical-cap`을 실행 전에 별도로 고정한다.
