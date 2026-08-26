# Experience Dashboard — 팀원 A workspace

`workspaces/experience-dashboard/` 에 그대로 넣을 수 있는 Next.js 프로젝트다.
백엔드 계약은 `robinhood0107/Capstone-AI-Trading-Coach` 의
`contracts/openapi/openapi.json` 과 `contracts/schemas/dashboard-*.v1.schema.json` 에 맞춰 검증했다.

처음 세팅한다면 **[SETUP.md](./SETUP.md)** 부터 읽는다.

---

## 1. 이 workspace가 책임지는 것

| 책임 | 아님 |
|---|---|
| Spring이 내려준 ViewModel을 화면으로 옮기기 | 모델 학습, 백테스트 계산 |
| ALLOW/WARN/HOLD/BLOCK과 사유를 읽을 수 있게 표시 | RiskEngine 판정 |
| 모델 비교·백테스트 시각화·RAG 출처 표시 | 신호 생성, 판정 재계산 |
| 보고서/발표 캡처 화면 | Decision·Risk·RAG 계약 변경 |

프론트는 **Spring REST만** 호출한다. Python gRPC와 artifact 파일을 직접 읽지 않는다.

## 2. 실행

```bash
cp .env.example .env
npm install
npm run dev        # http://localhost:3000
npm run typecheck
npm run build
```

`NEXT_PUBLIC_API_MODE`

- `mock` (기본) — 합성 fixture. 백엔드 없이 화면과 ViewModel을 검증한다.
- `live` — `NEXT_PUBLIC_API_BASE_URL` 의 Spring Decision Platform 호출.

두 모드는 같은 응답 envelope를 지난다. mock 전용 우회 경로가 없으므로 live 전환 시 화면 코드는 그대로다.

> **브라우저는 반드시 `http://localhost:3000` 으로 연다.**
> 서버 CORS가 이 origin만 허용한다. `127.0.0.1:3000` 으로 열면 모든 요청이 막힌다.

## 3. 화면과 소비 계약

| 경로 | 화면 | 호출하는 endpoint |
|---|---|---|
| `/` | 오늘 상태 | `GET /api/v1/risk/portfolio`, `/system/health` |
| `/principles` | 내 투자 원칙 | `GET /principle-presets`, `GET /principles`, `GET·PUT /principles/{id}` |
| `/order-review` | 주문 검토 | `GET /dashboard/risk-results/{decisionId}` + `GET /decisions/{decisionId}` |
| `/model-evaluation` | 모델 비교 | `GET /dashboard/model-evaluations/{runId}` + `GET /api/v2/signals/{symbol}` |
| `/backtest` | 백테스트 리포트 | `GET /dashboard/backtests/{runId}` |
| `/rag` | 금융 가이드 | `POST /rag/ask` → `GET /dashboard/rag-sources/{answerId}`, `GET /rag/sources` |
| `/report` | 보고서 캡처 | 위 ViewModel 재배치 |

축소안 발동 시 **최소 사수 3화면**: `/order-review`, `/model-evaluation`, `/rag`.

## 4. 계약 대조에서 고친 것

초안을 실제 OpenAPI/schema와 대조해 아래를 수정했다. 같은 실수를 반복하지 않도록 기록해 둔다.

| 항목 | 실제 계약 |
|---|---|
| Dashboard ViewModel | 서버가 이미 4종을 제공한다. 프론트에서 조립하지 않는다 |
| 요청 헤더 | CORS 허용은 `Authorization`, `Content-Type`, `X-Request-Id`, `X-Idempotency-Key` **4개뿐** |
| 판정 필드명 | `decision`이 아니라 `action` |
| 상태 판정 | `viewState`(READY/EMPTY/STALE)를 **서버가 내려준다** |
| 조회 키 | 모델·백테스트는 `runId`, 판정은 `decisionId`, RAG는 `answerId` |
| riskItem | `{metric, value, severity, source}` |
| orderIntent | `GET /decisions/{id}` 응답에 **없다** |
| preset | `nameKo` / `descriptionKo` / `defaultRules`, `disclaimer.{ko,en}` |
| principleVersionId | `PrincipleCurrent`에 **없다** |
| `/rag/sources` | 배열이 아니라 `{ items: [...] }` |
| 오류 코드 | 서버 `ErrorCode` 22종 (`INTERNAL_ERROR`, `SIGNAL_UNAVAILABLE`, `VERSION_EXHAUSTED` 포함) |
| Dashboard endpoint | query parameter를 하나라도 붙이면 `VALIDATION_ERROR` |

## 5. 명세 규칙이 코드에 반영된 지점

| 규칙 | 구현 |
|---|---|
| 4-state (loading/empty/error/stale) | `shared/lib/viewState.ts`, `shared/ui/AsyncBoundary.tsx` |
| "데이터 없음"과 "불러오기 실패" 구분 | `AsyncBoundary`의 `empty` / `error` 분기 |
| 결측을 0으로 합성 금지 | `shared/ui/Numeric.tsx` — 해치 처리한 `근거 없음` 슬롯 |
| `BLOCK > HOLD > WARN > ALLOW` | `shared/ui/Decision.tsx` — 판정 레일 |
| violations / issues / warnings / abstentions 미혼용 | 주문 검토의 4열 분리 |
| `ABSTAIN ≠ HOLD` | 모델은 `AbstainChip`, 판정은 `DecisionBadge` |
| HOLD는 오류가 아닌 정상 결과 | HOLD를 error 상태로 렌더하지 않음 |
| HTTP status 아닌 `error.code`로 분기 | `shared/api/envelope.ts`, `client.ts` |
| 서버 판정 재계산 금지 | `fromDashboard()` — `viewState`를 그대로 옮김 |
| RAG ask `X-Idempotency-Key` 필수 | `client.ts` `newIdempotencyKey()` |
| RAG는 매수/매도 지시 금지 | `BLOCKED_ADVICE` / `BLOCKED_SENSITIVE` 상태를 그대로 노출 |
| 합성 결과를 성과로 표시 금지 | `fixtureClass`·`evidenceMode` 배너 |
| 저장 실패 시 blind retry 금지 | 원칙 저장 409는 사용자에게 재조회를 요구 |
| token은 메모리에만 | `shared/api/session.ts` — localStorage/URL 저장 없음 |
| 외부 링크 scheme 검증 | `safeExternalUrl()` + `noopener noreferrer` |
| raw HTML 렌더 금지 | `dangerouslySetInnerHTML` 미사용 |
| security header | `next.config.mjs` headers |

## 6. 남은 작업

- [ ] `POST /api/v1/principles` — 원칙이 하나도 없을 때의 최초 생성 흐름
- [ ] `POST /api/v1/decisions/evaluate-order` 를 화면에서 호출 (지금은 `endpoints.ts`에만 있음)
- [ ] Kill Switch 토글 (`POST /api/v1/risk/kill-switch`)
- [ ] Playwright로 주문 검토·모델 비교·RAG 3화면 상태 스냅샷 회귀
- [ ] 학습일지 화면 — Journal 계약이 아직 openapi.json에 없다

---

## P1 full-app v2 수신 preview 경계

- production image는 `NEXT_PUBLIC_API_MODE=live`이고 브라우저는 same-origin `/api/...`만 호출한다.
- Next.js server가 내부 `api-edge:8080`으로 전달하며 `/healthz`를 제공한다.
- lockfile, non-root/read-only Docker 경계와 기본 unit/contract test는 owner preview에 포함됐다.
- 명세의 추가 20개 사용자 API와 live Playwright가 남아 있어
  `DASHBOARD_UI=PARTIAL_TEAM_A_ACTION_REQUIRED`다.
