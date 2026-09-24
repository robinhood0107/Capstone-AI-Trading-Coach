# Google·Kakao 단일 로그인 및 자동 계정 생성

## 계약 변경

- `mars-full` 로그인 화면은 Google과 Kakao 두 제공자만 표시한다. 별도 회원가입·비밀번호 입력·비밀번호 재설정 API는 추가하지 않는다. 어느 제공자든 첫 인증 성공으로 USER를 생성하고 바로 로그인한다.
- Google은 Spring Security OIDC 검증 결과의 `https://accounts.google.com` + `sub`를 사용한다. Kakao는 authorization-code 교환 후 고정 Kakao 사용자 정보 API에서 받은 숫자형 회원번호를 `https://kauth.kakao.com` + subject로 기록한다.
- 계정 키는 `(issuer, subject)`이며 이메일은 저장·연결 키로 사용하지 않는다. 같은 이메일이라도 Google/Kakao는 자동 병합하지 않는다. 계정 연결 기능은 추가하지 않는다.
- ADMIN allowlist는 기존 Google subject SHA-256만 유지한다. Kakao에서 생성된 identity는 항상 USER다. password 로그인과 DEMO 공개면은 닫힌 상태를 유지한다.
- 기존 `google_oidc_identities` 데이터와 세션을 보존하며 V211에서 `social_login_identities`로 forward rename한다. provider 별 사용자 기록은 하나의 테이블·세션 발급 함수로 처리한다.
- Spring 세션의 state/nonce 검증과 one-use same-origin handoff, 브라우저 메모리의 Bearer 보관, DB 세션 폐기를 유지한다. OAuth secret은 API 컨테이너만 읽고 프런트엔드 번들에는 넣지 않는다.

## 설정

- Google callback: `https://mars.royaljellynas.org/api/v1/auth/oidc/callback/google`
- Kakao callback: `https://mars.royaljellynas.org/api/v1/auth/oidc/callback/kakao`
- Kakao `client_id`에는 Kakao Developers REST API key를 사용하며, secret 기능이 켜져 있으면 `client_secret_post`로 token endpoint에 보낸다. Kakao 계정 이메일 scope는 요청하지 않는다.
- FULL secret assembly 입력에 두 Kakao OAuth 값이 필요하며 결과는 기존 `mars-public-full.env`에만 저장한다. DEMO는 두 OAuth provider secret을 받지 않는다.

## 검증 기준

- 오프라인 integration test에서 두 authorization endpoint의 state 생성, FULL callback allowlist, DEMO 차단, password login 차단을 확인한다.
- DB test에서 기존 Google 행 이전, 동시 첫 로그인 단일 계정, 동일 subject의 Google/Kakao 분리, Kakao ADMIN 거부, 세션 회수와 일반 USER 생성을 확인한다.
- 실제 Google/Kakao 사용자 동의 callback은 비밀값을 로컬 FULL secret에 설정한 뒤 운영자가 직접 확인한다. 그 전에는 provider 연결 완료로 표시하지 않는다.
