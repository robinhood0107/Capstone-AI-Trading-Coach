# MARS full Google OIDC 인증 전환 계약

## 범위와 소유 관계

- `mars-full` Spring profile만 Google OIDC authorization-code callback과 세션 교환을 연다.
  `mars-demo`에는 로그인 경로가 없다. 제품 mode와 Spring profile이 다르면 기동을 거부한다.
- Spring Security가 Google 서명·issuer·audience·만료·state/nonce를 검증한 뒤, 서버가
  검증된 issuer+sub만 `decision_auth`의 V198 함수에 전달한다. 이메일은 계정 키로 쓰지 않는다.
- V198은 첫 로그인 USER를 원자 생성하고 기존 `users`·`actor_auth_session`·JWT 경계를 쓴다.
  서버가 보관한 정확한 Google subject SHA-256과 일치할 때만 ADMIN이다. 역할 변화는
  `security_version`을 올리고 이전 DB 세션을 폐기하며, 감사에는 subject 원문을 넣지 않는다.
- OAuth 왕복에는 2분 HttpOnly/Secure/SameSite=Lax 세션을 쓴다. callback은 JWT를 URL로
  보내지 않고 같은 origin `/auth/complete`로 이동한다. 브라우저는 1회 POST 교환 뒤
  Bearer를 메모리에만 보관한다. 교환은 `Origin` exact 비교와 no-store를 적용한다.
- `POST /api/v1/auth/logout`은 확인된 현재 Bearer 세션을 DB에서 폐기한다.

## 호환과 출시 게이트

프로필 전환 동안 기존 개인 실행의 password bootstrap 경로는 유지한다. 공개 full/demo
프로필에는 password controller와 MCP password login bean을 만들지 않고, full의 외부
password URL은 제품 게이트와 Dashboard edge에서 막는다. 내부 bootstrap/자동운용의
password 의존성을 대체한 뒤 이 임시 경로를 삭제해야 하며 최종 릴리스의 영구 호환 기능이
아니다. root OpenAPI는 개인 실행 계약을 유지하고, full 전용 교환·로그아웃은
`contracts/openapi/mars-full-auth.v1.openapi.json`에 둔다.
Dashboard는 FULL 빌드에서 Google 버튼과 메모리 세션·서버 로그아웃을 쓰고,
LOCAL 개인 체크아웃만 기존 password 폼과 탭 범위 저장을 유지한다. DEMO/FULL의
password URL은 Next middleware에서도 404로 거부한다. LOCAL 경로는 자동운용의
기존 내부 호출처를 바꿀 때 함께 제거할 임시 전환 경계다.

이 변경만으로 공개 실서비스가 준비된 것은 아니다. KIS 개인 키 보관·계좌 격리·운영자
AI 총량·제품별 이미지/Compose·외부 Google 설정·두 사용자 자연 세션·수용량 검증이
완료되기 전에는 full 제품 게이트의 주문/KIS/Agent API를 열거나 이미지를 발행하지 않는다.
