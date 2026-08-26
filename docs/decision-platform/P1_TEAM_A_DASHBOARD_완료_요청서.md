# P1 Team A Dashboard 완료 요청서

## 아주 쉽게 말하면

> 새 API를 마음대로 만들라는 요청이 아닙니다. `contracts/openapi/openapi.json`에 이미 있는 API 중
> Dashboard 사용자 흐름에 필요한 20개를 실제 화면에서 사용하고, Docker Compose의 실제 Spring과
> 연결됐다는 Playwright 증거를 보내 주세요.

작업 위치는 `workspaces/experience-dashboard/`입니다. 현재 수신본 preview의 lockfile, production
Dockerfile, `/healthz`, same-origin `/api` 경계는 owner가 먼저 넣었습니다. Team A는 최신 `main`을
받고 이 경계를 유지하면서 기능 화면과 테스트를 완성합니다.

## 현재 연결 후보 15개

소스 wrapper가 있고 화면 흐름에서 사용하는 API입니다. 현재 owner preview는 이 중 9개를 실제 Spring
`200`으로 검증했습니다. 나머지 6개도 유효한 식별자를 사용한 live 테스트가 필요하며, mock 성공은
live 성공 증거가 아닙니다.

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

이 15개는 `NEXT_PUBLIC_API_MODE=live` production image와 단일 Compose에서 실제 Spring 요청을
Playwright trace 또는 request assertion으로 증명해야 합니다.

현재 live 검증이 남은 6개는 dashboard risk/RAG ViewModel, Decision 상세, Principle 상세/수정,
Signal v2입니다. 존재하지 않는 가짜 ID로 404를 만들지 말고, 테스트가 만든 유효한 ID를 이어서
조회·수정해야 합니다.

## 명세에 있으므로 추가로 사용할 20개

### KIS Mock/Paper 사용자 흐름 10개

- Mock: balance, buyable, fills, order submit 4개
- 공통 order 조회와 취소 2개
- Paper: balance, buyable, fills, order submit 4개

실계좌·실주문이 아닙니다. 화면에는 Mock/Paper 상태를 분명하게 표시합니다.

### RAG 사용자 흐름 5개

- 동의 기록 `POST /api/v1/consents`
- 답변 평가 `POST /api/v1/rag/answers/{answerId}/feedback`
- 이력 목록·상세·삭제 3개

### Principle, 주문 사전판정, Kill Switch 5개

- Principle 최초 생성과 버전 이력 2개
- `POST /api/v1/decisions/evaluate-order` 1개
- Kill Switch 조회와 변경 2개

정확한 method/path/분류는 [API 전수표](P1_API_USAGE_MATRIX.md)가 단일 확인표입니다.

## Team A에게 요구하지 않는 것

- artifact ingest status, async job, stream metric, brokerage reconcile, Decision audit는 운영·대사·참조
  API이므로 사용자 화면에 억지로 붙이지 않습니다.
- OpenAPI에 없는 온보딩·학습일지·시장데이터·사용자관리·백업·RAG 문서관리 endpoint를 임의로
  만들지 않습니다. 필요한 경우 `OWNER_API_MISSING`으로 owner에게 알려 줍니다.
- DB, 공개 Seed, provider client를 Dashboard에 넣지 않습니다.

## 완료 조건

```bash
npm ci
npm run typecheck
npm run lint
npm test
npm run build
docker build --platform linux/amd64 -t capstone-experience-dashboard:p1-local .
```

Playwright는 로그인 → 홈 → 원칙 → 주문 검토 → 모델 평가 → 백테스트 → RAG 순서로 실행하고,
mock transport가 아니라 same-origin `/api/...` 요청이 실제 Spring까지 도달했는지 확인합니다.
`.next`, `node_modules`, 개인 `.env`, credential, cache는 PR에 넣지 않습니다. Next.js 보안 패치는
lockfile과 함께 갱신합니다.

## 그대로 보내는 짧은 메시지

```text
최신 main을 받고 workspaces/experience-dashboard/에서 작업해 주세요.
새 endpoint를 만들라는 뜻이 아니라 OpenAPI에 이미 있는 Dashboard용 API 20개를 화면에서 실제로
사용해 달라는 요청입니다. 기존 15개도 mock이 아닌 live Compose 요청을 Playwright로 증명해 주세요.
자세한 목록은 docs/decision-platform/P1_API_USAGE_MATRIX.md와
P1_TEAM_A_DASHBOARD_완료_요청서.md에 있습니다.
```
