# 첫 운영자 AI 한도 1달러

사용자가 NAS 절대 상한을 `$1.00/일`로 결정했다. V203은 새 설치의 V200 초기 행
(`revision=1`, `changed_by IS NULL`, soft cap 0)만 `$1.00`로 전진 설정한다.
ADMIN이 이미 0 또는 다른 금액을 저장했다면 revision과 changed_by가 달라져
그 선택을 유지한다. 변경 가능한 값은 계속 NAS 절대 상한 이하다.

제한된 Flyway 계정이 forced RLS 아래에서 이 초기 행만 바꾸도록 임시 정책을 만들고,
같은 migration에서 제거한다. 독립 PostgreSQL 통합 테스트는 전체 Flyway 체인을
제한된 계정으로 적용한 뒤 초기 금액·revision·임시 정책 삭제를 확인한다.

초기 한도가 채워져도 Agent·매매 AI·Voyage의 모든 outbound가 공용 원장과
공급자별 제한에 연결되고 공개 제품 검증이 끝나기 전에는 과금 API를 열지 않는다.
