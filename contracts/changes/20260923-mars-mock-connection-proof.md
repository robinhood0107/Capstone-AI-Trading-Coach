# KIS_MOCK 읽기 전용 연결 확인

사용자가 `POST /api/v1/brokerage/mock/credential/connect`를 누르면, 현재 Bearer의
owner와 저장된 opaque account ID·revision을 서버가 읽는다. 기존 Spring→Python
brokerage gRPC는 그 owner-bound 암호문만 받아 KIS_MOCK의 안전한 잔고 조회 한 건을
수행한다. 잔고 원문·계좌번호·키는 응답·로그·감사에 남기지 않는다.
V204는 provider 호출 전에 같은 owner·account·revision의 시도를 원자 예약하고
60초 안의 중복 시도를 거부한다. 실패해도 시도 간격은 유지한다.

provider 조회가 성공한 뒤 V204는 계좌·revision을 다시 비교하고 STORED만
CONNECTED로 바꾼다. 키 교체와 같은 owner 잠금을 공유하므로 오래된 성공 응답은 새
키를 연결하지 못한다. CERTIFIED는 재확인해도 유지하고 DISCONNECTING은 거부한다.
연결 성공은 본인 모의계좌 읽기만 증명하며 주문·체결·대사·자동운용 동의는 별도다.

공개 API는 본문·query 없이 204를 반환한다. 실패는 원문 없이 409 또는 503으로
수렴한다. DEMO와 KIS_LIVE에는 이 경로가 없다. 장중 bounded 모의주문 인증과
arm 상태 전이는 후속 구현 전까지 닫는다.
