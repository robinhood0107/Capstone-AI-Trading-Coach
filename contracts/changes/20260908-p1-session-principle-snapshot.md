# 세션 원칙 snapshot과 현재 배치 완결성

사용자 확정 결정: 원칙 편집을 허용하고 진행 중 세션은 같은 버전을 끝까지 사용하며 다음
세션에 최신 ACTIVE 버전을 자동 적용한다. 기존 migration은 수정하지 않는다.

## V155

- claim이 control과 원칙 row를 순서대로 잠근 뒤 최신 ACTIVE 버전을 run에 저장한다.
  기존 claim 재개는 기존 버전을 유지한다. 다른 활성 세션이 있으면 새 claim을 거부한다.
- v2/v3 control의 원칙 적용 버전만 갱신한다. immutable 정책 동의 버전은 바꾸지 않는다.
  sizing·예약·submit의 기존 control 결속을 유지하며 run snapshot은 UPDATE로 변경할 수 없다.
- 내부 EVALUATE는 runId/claimTokenHash와 evaluation을 전달한다. DB가 capability, owner,
  활성 claim을 확인한 뒤 run의 원칙을 반환한다. public 평가의 최신 원칙 동작은 유지한다.
- 판단 저장은 최신 버전 또는 같은 owner의 활성 RISK_CHECKING run에 고정된 버전을 허용한다.
  후자는 KIS_MOCK과 동일 원칙 버전·정확한 주문 intent의 예약이 있어야 한다.
- read-only definer의 claim/예약 RLS만 owner scope 안에서 확장한다. 앱 테이블 DML 권한은
  추가하지 않는다. 원칙 비활성화와 owner/claim 위조는 고정 버전 허용 사유가 아니다.
- readiness의 단순 버전 drift 차단을 ACTIVE 확인으로 되돌리고 runtime은 해당 run과 control
  snapshot 일치를 확인한다. 화면의 수동 재무장 요구 안내를 제거한다.

## V156

기존 COMPLETE 행을 보존하면서 currentContractComplete를 내부 context에 추가한다.
현재 모델·source generation·정확한 producer/symbol 집합·Ridge 1/5/20 지평을 확인한다.
Python은 직전 XKRX session도 확인하고 불완전한 legacy 또는 오래된 source를 성공으로 반환하지 않는다.
legacy 보충 generation, 정정 lineage와 durable catch-up cursor는 후속 P4 작업이다.

public REST operation/DTO/OpenAPI bytes는 변경하지 않는다. 내부 브리지와 SQL·Python·Kotlin
변경은 함께 배포해야 한다. 이번 작업에서는 배포하지 않는다. 운영 08:50 적시성, 실제 주문,
모델 성능 또는 P1/P2~P7 전체 완료를 이 계약 기록만으로 주장하지 않는다.


실행 상세 API는 screening 저장 행이 없을 때 감사 기록의 ABSTAIN 및 SCREENING_ERROR/AI_DISABLED_OR_UNAVAILABLE 두 사유만 표시용으로 읽는다. 이 경로는 판단 입력이 아니며 기존 evidence를 대체하지 않는다. 공개 DTO와 enum은 기존 형식을 유지한다.
