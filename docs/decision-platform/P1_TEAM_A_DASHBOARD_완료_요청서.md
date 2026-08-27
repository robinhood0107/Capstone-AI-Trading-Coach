# Team A 대시보드 완료 요청

## 이번에 해주실 일

> 새 API를 임의로 만들지 말고, OpenAPI에 이미 있으며 최종 명세가 Team A 화면에 배정한 API만
> 사용해 주세요. 현재 화면 코드에 연결된 15개를 정확히 검증하고, 빠진 18개를 화면에 연결해
> 총 33개를 실제 로컬 Spring API로 확인하면 됩니다.

작업 위치는 `workspaces/experience-dashboard/`입니다. 통합 PR이 main에 병합됐다는 안내를 받은 뒤
최신 main에서 작업해 주세요. 통합 담당자가 먼저 넣은 `package-lock.json`, production Dockerfile,
`/healthz`, 브라우저의 same-origin `/api` 구조를 유지합니다.

여기서 “실제 API 연결”은 프론트의 가짜 응답이 아니라 Docker Compose 안의 로컬 Spring을 호출한다는
뜻입니다. KIS 실계좌를 뜻하지 않습니다. Team A는 KIS 자격증명을 입력하거나 모의주문 인증 명령을
실행하지 않습니다.

## 먼저 검증할 현재 15개

아래 API는 현재 소스에 호출 지점이 있습니다. 하지만 체크인된 Playwright는 로그인 하나만 경로를
정확히 확인하고, 나머지는 응답 개수와 5xx 부재만 검사합니다. 4xx도 통과할 수 있으므로 15개 전부의
method, path, 성공 상태를 다시 증명해야 합니다.

- `POST /api/v1/auth/login`
- `GET /api/v1/dashboard/model-evaluations/{runId}`
- `GET /api/v1/dashboard/backtests/{runId}`
- `GET /api/v1/dashboard/risk-results/{decisionId}`
- `GET /api/v1/dashboard/rag-sources/{answerId}`
- `GET /api/v1/decisions/{decisionId}`
- `GET /api/v1/principle-presets`
- `GET /api/v1/principles`
- `GET /api/v1/principles/{principleId}`
- `PUT /api/v1/principles/{principleId}`
- `POST /api/v1/rag/ask`
- `GET /api/v1/rag/sources`
- `GET /api/v1/risk/portfolio`
- `GET /api/v1/system/health`
- `GET /api/v2/signals/{symbol}`

상세 조회는 가짜 ID로 404를 만드는 방식이 아니라, 테스트가 생성하거나 Seed에서 읽은 유효한
`principleId`, `decisionId`, `runId`, `answerId`를 다음 요청에 이어 사용해 주세요. RAG 질문은 화면에서
실제로 제출해야 합니다.

## 추가로 화면에 연결할 18개

### KIS 모의투자 주문 검토와 결과

- `GET /api/v1/brokerage/mock/accounts/{accountId}/balances`
- `GET /api/v1/brokerage/mock/accounts/{accountId}/buyable`
- `GET /api/v1/brokerage/mock/accounts/{accountId}/fills`
- `POST /api/v1/brokerage/mock/orders`
- `GET /api/v1/brokerage/orders/{orderId}`
- `POST /api/v1/brokerage/orders/{orderId}/cancel`

화면 순서는 종목 신호 확인 → 주문 전 위험 판정 → 주문 차단 장치 확인 → 사용자의 명시적 제출 →
주문 상태/체결/취소 확인으로 구성합니다. 기본 Team A 테스트에서는 외부 KIS를 호출하지 않고 저장된
모의 데이터와 ledger 흐름을 검증합니다. 실제 KIS 모의계좌 연결은 통합 담당자만 별도로 검증합니다.

### 동의, RAG 평가와 원칙 생성

- `POST /api/v1/consents`
- `POST /api/v1/rag/answers/{answerId}/feedback`
- `POST /api/v1/principles`

### 주문 전 판정과 주문 차단 장치

- `POST /api/v1/decisions/evaluate-order`
- `GET /api/v1/risk/kill-switch`
- `POST /api/v1/risk/kill-switch`

`killSwitch()`와 `evaluateOrder()` 호출 함수는 현재 소스에 있지만 화면에서 사용되지 않습니다.
`POST kill-switch` 호출 함수와 화면 흐름도 추가해야 합니다. 일반 사용자는 주문 차단 장치를 켤 수 있지만,
해제는 관리자 권한이 필요하므로 권한별 성공·실패를 각각 테스트합니다.

### 자동운용 상태와 학습일지

- `GET /api/v1/automation/status`
- `POST /api/v1/automation/arm`
- `POST /api/v1/automation/disarm`
- `GET /api/v1/automation/runs`
- `POST /api/v1/journals`
- `GET /api/v1/journals`

Automation은 기본 `DISARMED`이며 KIS Mock과 explicit INTERNAL_PAPER를 분리해 표시합니다. arm이
차단되면 certification, REAL_TEAM_B, release binding, Kill Switch 같은 서버 block reason을 그대로
보여 주고 client boolean으로 우회하지 않습니다. disarm 뒤에도 outstanding reconciliation은 계속
표시합니다. Journal은 실제 response의 동적 `journalId`를 사용해 생성·목록을 검증합니다.

## 이번에 억지로 붙이지 않을 10개

아래 API는 존재하지만 최종 명세가 Team A 필수 화면으로 지정하지 않았습니다. Owner가 별도로 요청하지
않는 한 구현하지 않아도 됩니다.

- `GET /api/v1/brokerage/paper/accounts/{accountId}/balances`
- `GET /api/v1/brokerage/paper/accounts/{accountId}/buyable`
- `GET /api/v1/brokerage/paper/accounts/{accountId}/fills`
- `POST /api/v1/brokerage/paper/orders`
- `GET /api/v1/rag/history`
- `GET /api/v1/rag/history/{answerId}`
- `DELETE /api/v1/rag/history/{answerId}`
- `GET /api/v1/principles/{principleId}/versions`
- `PATCH /api/v1/journals/{journalId}`
- `DELETE /api/v1/journals/{journalId}`

결과 파일 적재 상태, 내부 작업, 처리 지표, 주문 대사, 판단 감사 6개 API와 Spring `/error` 7개도
일반 사용자 화면에 붙이지 않습니다. 전체 48개 분류는 [OpenAPI 사용 현황](P1_API_USAGE_MATRIX.md)에서
확인할 수 있습니다.

## 화면 문구와 최종 목적

- 현재 홈의 “자동주문 작동 중” 표현은 실제 동작과 다릅니다. “모의주문 가능 상태” 또는
  “주문 차단 장치 꺼짐”처럼 현재 사실을 보여 주세요.
- 프로그램을 켜 두는 것과 명시적으로 arm된 자동운용을 같은 것으로 표현하지 마세요. 시작 시 기본은
  `DISARMED`이고 arm 실패를 INTERNAL_PAPER 자동 fallback으로 바꾸지 않습니다.
- KIS 모의투자, 내부 가상거래, 백테스트를 화면에서 서로 다른 모드로 분명하게 표시합니다.
- Risk 결과는 허용/경고/보류/차단과 이유를 사용자가 이해할 수 있는 말로 설명합니다.
- Team B 미리보기와 Team B 실제 결과를 같은 것으로 표시하지 않습니다.

온보딩, 학습일지, 시장데이터, 사용자관리, 백업, RAG 문서관리, 자동매매 예약처럼 OpenAPI에 없는
기능이 필요하면 구현하지 말고 PR 설명에 다음처럼 한 줄을 남겨 주세요.

```text
OWNER_API_MISSING: 주문 예약 화면 / 거래일·시간·활성 상태를 저장하고 조회할 API 필요
```

## 완료 확인

Node.js 22 기준으로 다음을 모두 통과시켜 주세요.

```bash
cd workspaces/experience-dashboard
npm ci
npm run typecheck
npm run lint
npm test
npm run build
docker build --platform linux/amd64 -t capstone-experience-dashboard:p1-local .
```

통합 앱을 `./capstone up`으로 켠 뒤 저장소 루트에서 실제 Spring 연결 E2E를 실행합니다.

```bash
cd workspaces/experience-dashboard
P1_USER_PASSWORD_FILE=../../deploy/p1/.state-app/secrets/demo-user.password \
  npm run test:e2e:live
```

Playwright 결과는 `skip 0`이어야 합니다. 총 33개 API 각각에 대해 예상 method/path와 성공 상태를
검사하고, 4xx와 5xx를 모두 실패로 처리합니다. 비밀번호, JWT, 계좌번호와 응답 원문은 trace나
리포트에 남기지 않습니다.

## 보내 주실 것

1. PR 주소와 최신 commit SHA
2. `package-lock.json` SHA-256
3. 위 명령들의 성공 결과
4. 33개 API의 method/path/성공 상태 표
5. Playwright `skip 0` HTML report 또는 민감값을 제거한 증거
6. `OWNER_API_MISSING` 목록

`.next`, `node_modules`, 개인 `.env`, credential과 cache는 PR에 넣지 않습니다.

## 그대로 보내는 짧은 메시지

```text
통합 PR이 main에 병합됐다는 안내를 받은 뒤 최신 main을 받아 주세요.
workspaces/experience-dashboard에서 현재 연결된 API 15개를 정확히 검증하고, 최종 명세가 Team A에
배정한 18개를 화면에 추가해 총 33개를 로컬 Spring과 연결해 주세요. API는 새로 만들지 말고,
OpenAPI에 없는 기능은 OWNER_API_MISSING으로 적어 주세요. 기본 DISARMED와 명시적 arm을 구분하고,
KIS 장애를 INTERNAL_PAPER 자동 fallback으로 표현하지 말고, 완료 조건과 제출물은 이 요청서를 그대로 따라 주세요.
```
