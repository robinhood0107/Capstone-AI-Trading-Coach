# P1 API 사용 전수표

## 판정

권위는 `contracts/openapi/openapi.json`입니다. 총 48 operations를 정확히 한 분류에만 넣습니다.

- `A_CURRENT` 15개: Team A 현재 소스·화면 연결 후보. 이 중 9개는 현재 preview에서 live 검증됨
- `A_REQUIRED` 20개: 명세에 이미 있고 Team A가 사용자 흐름에 추가할 API
- `ADMIN_REFERENCE` 6개: 운영·대사·참조 API. Team A/B에 강제하지 않음
- `FRAMEWORK_ERROR` 7개: Spring `/error`. 제품 기능에서 제외
- Team B 직접 REST 책임: 0개

현재 단일 Compose Playwright는 로그인, system health, portfolio risk, principle preset/목록,
model-evaluation, backtest, RAG sources/ask 9개를 실제 Spring `200`으로 검증했습니다. 나머지 현재 연결
6개는 유효한 decision/principle/signal/answered-citation 식별자가 있는 fixture와 상호작용 테스트가
추가로 필요합니다. `A_REQUIRED` 20개는 Team A 후속 구현 대상입니다.

| No. | Method | Path | 분류 | 사용 주체/설명 |
|---:|---|---|---|---|
| 01 | POST | `/api/v1/auth/login` | `A_CURRENT` | Team A 로그인 |
| 02 | GET | `/api/v1/dashboard/backtests/{runId}` | `A_CURRENT` | Team A 백테스트 ViewModel |
| 03 | GET | `/api/v1/dashboard/model-evaluations/{runId}` | `A_CURRENT` | Team A 모델 평가 ViewModel |
| 04 | GET | `/api/v1/dashboard/rag-sources/{answerId}` | `A_CURRENT` | Team A RAG 출처 ViewModel |
| 05 | GET | `/api/v1/dashboard/risk-results/{decisionId}` | `A_CURRENT` | Team A 위험 판정 ViewModel |
| 06 | GET | `/api/v1/decisions/{decisionId}` | `A_CURRENT` | Team A Decision 상세 |
| 07 | GET | `/api/v1/principle-presets` | `A_CURRENT` | Team A 원칙 preset |
| 08 | GET | `/api/v1/principles` | `A_CURRENT` | Team A 원칙 목록 |
| 09 | GET | `/api/v1/principles/{principleId}` | `A_CURRENT` | Team A 원칙 상세 |
| 10 | PUT | `/api/v1/principles/{principleId}` | `A_CURRENT` | Team A 원칙 수정 |
| 11 | POST | `/api/v1/rag/ask` | `A_CURRENT` | Team A RAG 질문 |
| 12 | GET | `/api/v1/rag/sources` | `A_CURRENT` | Team A source registry |
| 13 | GET | `/api/v1/risk/portfolio` | `A_CURRENT` | Team A portfolio risk |
| 14 | GET | `/api/v1/system/health` | `A_CURRENT` | Team A 인증 health |
| 15 | GET | `/api/v2/signals/{symbol}` | `A_CURRENT` | Team A signal 조회 |
| 16 | GET | `/api/v1/brokerage/mock/accounts/{accountId}/balances` | `A_REQUIRED` | Mock balance |
| 17 | GET | `/api/v1/brokerage/mock/accounts/{accountId}/buyable` | `A_REQUIRED` | Mock buyable |
| 18 | GET | `/api/v1/brokerage/mock/accounts/{accountId}/fills` | `A_REQUIRED` | Mock fills |
| 19 | POST | `/api/v1/brokerage/mock/orders` | `A_REQUIRED` | Mock order submit |
| 20 | GET | `/api/v1/brokerage/orders/{orderId}` | `A_REQUIRED` | 공통 order 조회 |
| 21 | POST | `/api/v1/brokerage/orders/{orderId}/cancel` | `A_REQUIRED` | 공통 order 취소 |
| 22 | GET | `/api/v1/brokerage/paper/accounts/{accountId}/balances` | `A_REQUIRED` | Paper balance |
| 23 | GET | `/api/v1/brokerage/paper/accounts/{accountId}/buyable` | `A_REQUIRED` | Paper buyable |
| 24 | GET | `/api/v1/brokerage/paper/accounts/{accountId}/fills` | `A_REQUIRED` | Paper fills |
| 25 | POST | `/api/v1/brokerage/paper/orders` | `A_REQUIRED` | Paper order submit |
| 26 | POST | `/api/v1/consents` | `A_REQUIRED` | RAG 동의 기록 |
| 27 | POST | `/api/v1/rag/answers/{answerId}/feedback` | `A_REQUIRED` | RAG 답변 평가 |
| 28 | GET | `/api/v1/rag/history` | `A_REQUIRED` | RAG 이력 목록 |
| 29 | GET | `/api/v1/rag/history/{answerId}` | `A_REQUIRED` | RAG 이력 상세 |
| 30 | DELETE | `/api/v1/rag/history/{answerId}` | `A_REQUIRED` | RAG 이력 삭제 |
| 31 | POST | `/api/v1/principles` | `A_REQUIRED` | 원칙 최초 생성 |
| 32 | GET | `/api/v1/principles/{principleId}/versions` | `A_REQUIRED` | 원칙 버전 이력 |
| 33 | POST | `/api/v1/decisions/evaluate-order` | `A_REQUIRED` | 주문 사전판정 |
| 34 | GET | `/api/v1/risk/kill-switch` | `A_REQUIRED` | Kill Switch 조회 |
| 35 | POST | `/api/v1/risk/kill-switch` | `A_REQUIRED` | Kill Switch 변경 |
| 36 | GET | `/api/v1/artifacts/ingest-status` | `ADMIN_REFERENCE` | artifact ingest 참조 |
| 37 | GET | `/api/v1/async-jobs` | `ADMIN_REFERENCE` | async job 목록 |
| 38 | GET | `/api/v1/async-jobs/{jobId}` | `ADMIN_REFERENCE` | async job 상세 |
| 39 | POST | `/api/v1/brokerage/orders/{orderId}/reconcile` | `ADMIN_REFERENCE` | brokerage 대사 |
| 40 | GET | `/api/v1/decisions/{decisionId}/audit` | `ADMIN_REFERENCE` | Decision audit |
| 41 | GET | `/api/v1/stream-metrics` | `ADMIN_REFERENCE` | stream metric |
| 42 | GET | `/error` | `FRAMEWORK_ERROR` | Spring error handler |
| 43 | POST | `/error` | `FRAMEWORK_ERROR` | Spring error handler |
| 44 | PUT | `/error` | `FRAMEWORK_ERROR` | Spring error handler |
| 45 | DELETE | `/error` | `FRAMEWORK_ERROR` | Spring error handler |
| 46 | PATCH | `/error` | `FRAMEWORK_ERROR` | Spring error handler |
| 47 | HEAD | `/error` | `FRAMEWORK_ERROR` | Spring error handler |
| 48 | OPTIONS | `/error` | `FRAMEWORK_ERROR` | Spring error handler |

## Team B 파일 계약과 owner 검증

Team B 입력은 계약된 KIS 가격 snapshot/artifact, ECOS macro snapshot, 조건부 승인된
`news_sentiment_summary.v2`입니다. 출력은 LSTM/규칙 신호, 백테스트, trade/equity log, model report와
manifest입니다. owner가 실물 수신 후 signal, model-evaluation, backtest, ingest-status 네 API에서
반영을 확인합니다.

## `OWNER_API_MISSING`

온보딩, 학습일지, 시장데이터, 사용자관리, 백업, RAG 문서관리 API는 현재 OpenAPI 48개에 없습니다.
Team A/B가 임의 endpoint를 만들지 않습니다. 제품 명세상 필요하면 owner가 contract-change를 먼저
승인하고 `OWNER_API_MISSING`을 해소합니다.
