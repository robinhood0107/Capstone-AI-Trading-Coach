# MARS 사용자별 KIS_MOCK 저장 계약

V184는 암호문 저장 자리와 함수만 두었고 실제 호출자는 없었다. V199는 `account_id`와
`STORED|CONNECTED|CERTIFIED|DISCONNECTING` 상태, revision을 전진 추가하고 결속되지 않은
V184 쓰기·읽기·삭제 함수를 제거한다. 기존 암호문 행은 임의로 지우지 않지만 새 경로는
opaque account ID가 없는 행을 사용하지 않는다.

full 제품의 USER는 `PUT /api/v1/brokerage/mock/credential`에 App Key·App Secret·하이픈
없는 계좌번호 10자리만 보낸다. owner/mode/account ID는 서버가 결정한다. brokerage 전용
KEK의 32-byte 원본은 0700 디렉터리 안 0600 파일로만 읽고, 계정·모드·opaque account ID를
AES-GCM AAD에 결속한다. DB에는 암호문과 마지막 4자리만 기록하며 GET은 마스킹된 상태만
반환한다. 사용자별 actor capability를 같은 트랜잭션에서 소비하고, GUC만 설정한 요청은
DB 함수가 거부한다.

키 교체는 ARMED control, 미완료 주문, 미완료 자동운용 execution이 있으면 거부한다.
교체 뒤 상태는 STORED로 돌아간다. 저장 성공은 연결·인증·주문·체결·대사 성공을 의미하지
않는다. 다음 변경에서 사용자별 KIS_MOCK reader와 읽기 연결 확인, 모의주문 인증,
pending 복구·해제, 자동운용 결속을 연결한다. 데모와 KIS_LIVE에는 이 API가 없다.

full 전용 API schema는 `contracts/openapi/mars-full-mock-credential.v1.openapi.json`이다.
root OpenAPI의 개인 실행 계약과 V184 migration byte는 수정하지 않는다.
