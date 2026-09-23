# MARS 익명 데모 Agent의 예제·비용·이력 경계

데모 제품의 `POST /api/v1/demo/agent/ask`는 로그인을 받지 않고 `questionId` 한 필드만
받는다. 허용값은 `diversification`, `asset_allocation`, `past_performance` 세 개다.
추가 필드·알 수 없는 ID·JSON 중복 키를 거부한다. 사용자 자유 입력은 provider에
전달하지 않는다. 분산·자산 배분·과거 성과의 한계를 설명하는 자체 작성 한국어 예제
근거 3개만 Vertex에 전달한다. 근거 출처는 미국 SEC Investor.gov의 공개 교육 자료다.

하나의 내부 메모리 마커를 Redis의 기존 원자 RAG 제한기에 전달해 서버 전체
분당 2회로 묶는다. Redis 장애는 fail-closed한다. gRPC host는 `DEMO_AGENT`와
owner `NULL`로 V201 일일 공개가격 예산을 permit 전에 예약한다. Google Search,
웹 도구, KIS, 주문, 인증 세션은 이 API 경로에서 사용하지 않는다. 요청 ID는 서버가
새로 생성하므로 방문자가 같은 `X-Request-Id`를 반복해 다른 요청의 예산 ID를
오염시킬 수 없다.

데모의 visitor 질문·답변과 개인 식별자는 DB history/usage ledger에 남기지 않는다.
HTTP 응답은 `Cache-Control: no-store`로 보낸다. demo 화면은 질문 ID 3개를 선택하는
버튼과 답변·출처만 렌더하고 브라우저 저장소에 요청이나 답을 쓰지 않는다. Next 경계는
첫 화면과 익명 ask POST 외의 화면·API를 404로 거부하며, 로그인·계좌·주문 메뉴를
렌더하지 않는다.
V201에는 예약 ID·일자·소스·provider·최대액만 남는다. 예산 부족은 429,
provider·Redis 장애는 503으로 반환한다. 전용 DB/secret/이미지 기동과
브라우저 저장소·실제 소켓 0/1회 검증은 별도 변경에서 완료한다.
