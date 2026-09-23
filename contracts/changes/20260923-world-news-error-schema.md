# 세계 뉴스 오류 schema의 forward 수정

`WorldNewsV2Error` 생성 함수가 속성만 선언하고 반환하지 않아 overlay와 현재 root OpenAPI의
component 값이 `null`이었다. 조회 route의 400/401/503 응답은 이 component를 참조한다.
OpenAPI 생성 과정에서 `null` component가 생략되면 참조가 깨져 검증이 실패한다.

현재 overlay와 root에 `code`, `message`, `requestId`를 필수로 하는 object schema를
추가한다. 이전 root snapshot은 수정하지 않는다. HTTP route, 응답 상태, 런타임 오류
본문, provider 호출은 바뀌지 않는다. 생성 함수와 현재 산출물을 함께 고치고 참조가
실제 schema로 해석되는 회귀를 둔다.
