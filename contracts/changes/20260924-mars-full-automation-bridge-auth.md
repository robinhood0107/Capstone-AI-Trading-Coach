# Full 자동운용의 비밀번호 없는 내부 bridge

`mars-full`의 자동운용 bridge는 Google OIDC가 만든 사용자 ID를 claim/run마다 전달한다.
loopback와 설치별 automation shared secret으로만 내부 요청을 인증하며, 기존 Spring bridge는
사용자가 ACTIVE인지 확인하고 actor/account-bound BrokerageService를 호출한다. Full 프로세스는
공용 password username/password를 읽거나 login API를 호출하지 않는다.

`LOCAL`은 전환 중 기존 내부 호출을 유지한다. 이 계약은 스케줄러의 단일 owner 전제를
해결하지 않는다. 사용자별 claim과 KIS quote/order/fill 대사 격리를 검증하기 전에는
공개 full automation을 Compose에서 켜지 않는다.
