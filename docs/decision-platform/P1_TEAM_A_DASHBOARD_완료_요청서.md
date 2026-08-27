# Team A 대시보드 완료 요청

## 결론부터

Team A가 backend, OpenAPI, DB, Seed 또는 33개 API 검증기를 새로 만들 필요는 없습니다. Owner가 이미
exact-33 catalog, generated client, deterministic Seed/reset, 로그인 fixture와 실제 Spring acceptance를
완성했고 `./capstone team-a acceptance`가 33개를 모두 통과합니다.

Team A의 남은 역할은 기존 `workspaces/experience-dashboard/` 소스를 존중해 production 화면을 완성하는
것뿐입니다. 기존 페이지를 갈아엎거나 API별로 새 화면 33개를 만들지 말고, 아래 사용자 흐름에 필요한
component만 연결해 주세요.

## Owner가 이미 끝낸 것

- root OpenAPI exact-56과 Team A exact-33 subset
- `contracts/catalogs/p1-team-a-acceptance.v1.json`
- `contracts/catalogs/p1-ui-evidence-badges.v1.json`
- `src/shared/api/generated/p1-team-a-client.v1.ts`
- same-origin `/api` rewrite와 CORS exact origin
- principle, Decision, RAG, Team B, account, order, automation, Journal deterministic fixture
- 동적 ID 연결, JWT/password 비노출 reporter, finally 상태 복구
- 실제 Spring exact-33, Playwright skip 0, frontend fake response 0 검증 명령

따라서 Owner 파일을 복사하거나 별도 mock server를 만들지 않습니다. backend acceptance가 실패하면 Team A가
우회하지 말고 Owner에게 실패 출력만 전달하면 됩니다.

## Team A가 실제로 완성할 다섯 흐름

1. 로그인 후 현재 홈·원칙·Signal·위험·근거를 이해할 수 있는 흐름
2. 모의 주문의 검토, Kill Switch, 명시적 제출, 상태·취소·체결 확인 흐름
3. automation status, arm/disarm, 최근 run을 확인하는 흐름
4. RAG 동의·질문·feedback과 Journal 생성·목록 흐름
5. `SYNTHETIC_GOLDEN`/`REAL_TEAM_B`, `KIS_MOCK`/`INTERNAL_PAPER`, LightGBM
   `RESEARCH_ONLY`를 혼동하지 않는 badge와 설명

기존 화면과 component를 재사용합니다. 각 API를 별도 페이지로 만들 필요가 없고, 한 흐름에서 얻은
`principleId`, `decisionId`, `orderId`, `answerId`, `runId`, `journalId`를 다음 단계에 이어 쓰면 됩니다.
4xx/5xx는 성공처럼 삼키지 말고 사용자가 복구할 수 있는 오류로 표시합니다.

## exact-33 연결 목록

이 목록은 Team A가 API를 다시 구현하라는 뜻이 아니라 generated client 밖의 임의 transport를 만들지 않도록
고정한 목록입니다. Owner runner가 method/path/status 전체를 자동 집계하므로 Team A가 수동 33행 표를 다시
작성할 필요는 없습니다.

### 로그인·원칙·위험·Signal

- `POST /api/v1/auth/login`
- `GET /api/v1/system/health`
- `GET /api/v1/principle-presets`
- `POST /api/v1/principles`
- `GET /api/v1/principles`
- `GET /api/v1/principles/{principleId}`
- `PUT /api/v1/principles/{principleId}`
- `GET /api/v1/risk/portfolio`
- `GET /api/v1/risk/kill-switch`
- `POST /api/v1/risk/kill-switch`
- `GET /api/v2/signals/{symbol}`

### RAG·Dashboard evidence

- `GET /api/v1/rag/sources`
- `POST /api/v1/consents`
- `POST /api/v1/rag/ask`
- `POST /api/v1/rag/answers/{answerId}/feedback`
- `GET /api/v1/dashboard/rag-sources/{answerId}`
- `GET /api/v1/dashboard/model-evaluations/{runId}`
- `GET /api/v1/dashboard/backtests/{runId}`

### Decision·모의 주문

- `POST /api/v1/decisions/evaluate-order`
- `GET /api/v1/decisions/{decisionId}`
- `GET /api/v1/dashboard/risk-results/{decisionId}`
- `GET /api/v1/brokerage/mock/accounts/{accountId}/balances`
- `GET /api/v1/brokerage/mock/accounts/{accountId}/buyable`
- `POST /api/v1/brokerage/mock/orders`
- `GET /api/v1/brokerage/orders/{orderId}`
- `POST /api/v1/brokerage/orders/{orderId}/cancel`
- `GET /api/v1/brokerage/mock/accounts/{accountId}/fills`

### Automation·Journal

- `GET /api/v1/automation/status`
- `POST /api/v1/automation/arm`
- `GET /api/v1/automation/runs`
- `POST /api/v1/automation/disarm`
- `POST /api/v1/journals`
- `GET /api/v1/journals`

`getMockBuyable` query는 `symbol`, `price`만 사용합니다. KIS Mock arm이 certification, 실제 Team B pointer,
release binding 또는 Kill Switch 때문에 차단되면 서버 결과를 그대로 표시하고 client boolean이나
`INTERNAL_PAPER` 자동 fallback으로 우회하지 않습니다.

## 이번 작업에서 하지 않을 것

- backend, OpenAPI, migration, Compose, Seed, model/provider 코드 변경
- 새 API, mock server, fake production response 추가
- KIS credential 입력, certification, 실제 provider/account/order 호출
- optional history/Journal patch-delete/operator API를 필수 화면에 억지로 추가
- 기존 Team A 소스의 전면 재작성 또는 디자인 시스템 교체

실제 누락 API가 발견된 경우에만 `OWNER_API_MISSING: <한 줄>`을 PR에 남기고 임의 endpoint를 만들지 않습니다.

## 완료 확인

```bash
./capstone up
./capstone team-a acceptance
cd workspaces/experience-dashboard
npm ci
npm run typecheck
npm run lint
npm test
npm run build
npm run test:e2e:live
```

Owner의 exact-33 명령은 backend 전제 확인입니다. Team A의 `test:e2e:live`는 위 다섯 실제 화면 흐름,
오류 표시, badge와 state 복구를 검증하면 됩니다. 테스트 skip은 0이어야 합니다.

## 보내 주실 것

1. PR URL과 commit SHA
2. `package-lock.json` SHA-256
3. typecheck/lint/unit/contract/build/UI Playwright 결과
4. 변경한 사용자 흐름 다섯 개의 짧은 설명과 `OWNER_API_MISSING`이 있으면 그 목록

production image build와 digest, exact-33 재실행, Compose·security 검증은 Owner가 담당합니다. `.next`,
`node_modules`, 개인 `.env`, credential과 test cache는 제출하지 않습니다.

## 그대로 보내는 짧은 메시지

```text
최신 main에서 workspaces/experience-dashboard만 수정해 주세요. backend exact-33, generated client,
Seed/reset과 검증기는 Owner가 이미 준비했으므로 다시 만들거나 33행 표를 수동 작성할 필요가 없습니다.
기존 화면을 유지하면서 로그인·근거, 모의주문, automation, RAG·Journal, truth badge의 다섯 사용자 흐름만
production UI로 완성하고 typecheck/lint/test/build/UI Playwright 결과와 PR·lock SHA를 보내 주세요.
provider credential이나 실제 주문은 실행하지 않습니다.
```
