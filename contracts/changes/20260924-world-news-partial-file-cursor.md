# GDELT 분별 파일 PARTIAL 커서 재사용

2026-09-24 실측에서 수집기가 한 파일의 허용 문서를 DB에 원자 저장한 뒤에도 일부 행의 제외·거부로 상태를 `PARTIAL`로 남겼다. 기존 `world_news_collection_completed_v1`은 `COMPLETE`만 인정해 같은 분별 URL을 다음 주기에 다시 받고, 재실행 시 약 6.6MB를 읽어도 신규 문서가 0건이었다.

V210은 저장이 commit된 `COMPLETE`와 `PARTIAL`을 모두 처리 완료 커서로 반환한다. DB의 `PARTIAL` 상태와 사이클 출력의 제외 행 수는 그대로 남고, `COLLECTION_FAILED`와 `NOT_COLLECTED`는 완료로 취급하지 않는다. writer는 기존 `decision_market_writer`와 SECURITY DEFINER 함수만 사용한다. 문서 원문, 계좌, 주문 권한, 공개 REST 응답 형식은 바뀌지 않는다.

MARS는 이미 처리한 GDELT 분별 URL을 자동으로 다시 받지 않는 정책을 선택했다. 공급자가 같은 URL을 나중에 고치거나 파서가 바뀌었을 때의 재처리는 별도 명시적 절차가 필요하다. 이 한계는 `PARTIAL` 기록을 유지해 식별한다.

검증은 같은 cursor의 `PARTIAL` 후 물리 호출 0, DB의 `PARTIAL` 조회 true, `COLLECTION_FAILED` 조회 false를 확인한다. 원본 입력이 없는 성과·주문 숫자의 근거로 이 변경을 사용하지 않는다.
