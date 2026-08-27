# P1 Team A exact-33 backend acceptance

## 결정

root exact-56에서 Team A에 배정된 current-screen 15개와 required 18개를 합친 exact-33 subset을
`p1-team-a-acceptance.v1`로 고정한다. Owner harness는 Team A production UI를 대신 구현하지 않고,
same-origin Dashboard `/api` rewrite를 통해 실제 Spring 성공 상태를 검증한다.

## 산출물

- `contracts/catalogs/p1-team-a-acceptance.v1.json`: method/path/operationId/status exact-33
- `contracts/catalogs/p1-ui-evidence-badges.v1.json`: synthetic/REAL_TEAM_B, LightGBM, brokerage mode 의미
- generated TypeScript client: request/response/path/query/idempotency type
- provider-free demo DB reset/seed와 one-shot Compose service
- JWT/password/response 원문을 출력하지 않는 Playwright reporter
- `./capstone team-a acceptance` 단일 실행 명령

## 결정적 clock

daily order-count의 `coveredThrough == evaluationAsOf` PIT 계약을 완화하지 않는다. acceptance 실행에서만
`P1_OFFLINE_DEMO=true`, explicit enable flag와 현재 UTC 5분 이내 fixed instant를 함께 요구해 Spring
evaluation clock과 fixture observation clock을 일치시킨다. 실행 후 일반 system clock container로
강제 재생성한다. production 기본값과 gate 의미는 변하지 않는다.

## 저장·복구

Seed는 local offline demo의 bounded mutable owner rows만 reset한다. Automation event와 RAG consent/usage/
claim transition 같은 append-only ledger는 삭제하지 않는다. Playwright `finally`와 shell fallback은
automation을 DISARMED로 되돌리고 Kill Switch를 초기 상태로 복구한다. volume과 table은 삭제하지 않는다.

## 안전 경계

- KIS/Vertex/GDELT/provider/account/order physical call 0
- KIS_LIVE 0, external credential 불필요
- synthetic golden을 REAL_TEAM_B로 승격하지 않음
- frontend fake production response 0
- password/JWT/raw response report 0
- CORS origin은 localhost/127.0.0.1:3000 정확히 두 개

## 기존 계약 영향

root OpenAPI operation과 기존 path/component bytes는 변경하지 않는다. historical exact-48에서 누락된
`getMockBuyable`의 strict `symbol`/`price` query는 root를 느슨하게 고치지 않고 acceptance catalog의
bounded client adapter fact로만 기록한다. Team B와 Signal/RiskDecision/order authority는 변하지 않는다.

## 판정

```text
OWNER_TEAM_A_BACKEND_PREREQUISITES=PASS
OWNER_POST_TEAM_A_BACKEND_CODE_REQUIRED=0
TEAM_A_ACCEPTANCE_OPERATION_COUNT=33
PLAYWRIGHT_SKIP=0
FRONTEND_FAKE_PRODUCTION_RESPONSE=0
TEAM_A_REAL_UI=PENDING_EXTERNAL_TEAM
PROVIDER_CALLS=0
```
