# automation-position.v2 — expirySession 을 nullable 로

- 날짜: 2026-09-04
- 범위: `contracts/schemas/automation-position.v2.schema.json` 과 그 Kotlin projection/DTO
- 성격: 계약을 실제 데이터에 맞춘다. 값을 새로 만들지 않는다.

## 왜

`GET /api/v2/automation/positions` 가 500 으로 닫혔다.

    java.lang.NullPointerException: getObject(...) must not be null
      JdbcAutomationRepository.readPositionPageV2$lambda$1(JdbcAutomationRepository.kt:454)

454 행은 `expirySession = row.getObject("expiry_session", LocalDate::class.java)` 이고
`AutomationPositionV2Projection.expirySession` 이 non-null 로 선언돼 있었다.

그런데 이 조회의 WHERE 절은 `max_holding_sessions IS NULL` 인 포지션만 고른다. 그리고 엔진은
`expiry_session = None if holding_sessions is None` 으로 만든다(`automation.py`). v2 정책 경로
(`p1_put_automation_policy_v2`)는 `max_holding_sessions` 를 받지 않으므로 **실제 v2 포지션은
언제나 만료가 비어 있다.** 즉 이 엔드포인트는 첫 실제 포지션이 생기는 순간 항상 깨진다.

그동안 드러나지 않은 것은 v2 형태의 실제 포지션이 없었기 때문이다. 2026-09-04 에 KIS Mock
실주문이 처음 체결되면서(006400 18주) 그 순간 대시보드의 자동운용 화면이 통째로 렌더되지
않았다 - e2e 가 조작 버튼 0개로 실패해 발견했다.

## 무엇을 바꾸는가

    expirySession   {"type":"string","format":"date"}  ->  nullable
    required        그대로 (키는 언제나 있고 값이 null 일 수 있다)

`automation-position.v3` 은 이미 nullable 이다. v2 를 그쪽에 맞춘다. Kotlin 의 projection 과
응답 DTO 도 `LocalDate?` 로 옮긴다.

## 대안을 고르지 않은 이유

정책에 `max_holding_sessions` 기본값을 넣어 만료를 만들 수도 있다. 그러나 그것은 보유기간 만료
청산이라는 **거래 동작**을 새로 켜는 변경이고, 화면이 깨진 것을 고치려고 거래 규칙을 바꾸는
것은 순서가 뒤바뀐다. 계약이 실제 데이터를 서술하도록 맞추는 쪽을 골랐다.
