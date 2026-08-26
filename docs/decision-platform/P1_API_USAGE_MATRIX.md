# OpenAPI 48개 사용 현황

## 한눈에 보는 결론

기준 파일은 `contracts/openapi/openapi.json`입니다. 현재 HTTP 작업은 정확히 48개이고, 아래 표에서
각 작업을 한 번씩만 분류했습니다.

| 구분 | 개수 | 뜻 |
|---|---:|---|
| 현재 화면 | 15 | Team A 코드에 호출 지점이 있음. 실제 Spring 성공 검증은 아직 전부 증명되지 않음 |
| Team A 필수 | 12 | 최종 명세의 Team A 화면에 필요하므로 추가 구현·검증해야 함 |
| 선택 기능 | 8 | API는 있지만 최종 명세가 Team A 필수 화면으로 배정하지 않음 |
| 운영자 전용 | 6 | 적재·작업·대사·감사용. 일반 사용자 화면에 억지로 붙이지 않음 |
| 제품 기능 아님 | 7 | Spring 공통 오류 처리 경로 |

Team B가 직접 호출할 Spring REST API는 **0개**입니다. Team B는 파일로 입력을 받고 파일로 결과를
전달합니다.

## 현재 테스트 증거의 정확한 수준

현재 Playwright는 로그인 성공, 주요 화면 이동, API 응답이 두 개 이상이라는 사실과 5xx가 없다는
사실만 확인합니다. 4xx도 통과할 수 있고 RAG 질문 버튼도 누르지 않으므로 “9개 API가 실제 Spring
200으로 검증됐다”고 말할 수 없습니다. Team A는 아래 `현재 화면` 15개와 `Team A 필수` 12개를
method, path, 성공 상태별로 각각 검증해야 합니다.

## 전체 목록

| No. | Method | Path | 구분 | 사용 주체/설명 |
|---:|---|---|---|---|
| 01 | POST | `/api/v1/auth/login` | `현재 화면` | Team A 로그인 |
| 02 | GET | `/api/v1/dashboard/backtests/{runId}` | `현재 화면` | Team A 백테스트 결과 |
| 03 | GET | `/api/v1/dashboard/model-evaluations/{runId}` | `현재 화면` | Team A 모델 평가 결과 |
| 04 | GET | `/api/v1/dashboard/rag-sources/{answerId}` | `현재 화면` | Team A RAG 출처 결과 |
| 05 | GET | `/api/v1/dashboard/risk-results/{decisionId}` | `현재 화면` | Team A 위험 판정 결과 |
| 06 | GET | `/api/v1/decisions/{decisionId}` | `현재 화면` | Team A 주문 판단 상세 |
| 07 | GET | `/api/v1/principle-presets` | `현재 화면` | Team A 원칙 기본값 |
| 08 | GET | `/api/v1/principles` | `현재 화면` | Team A 원칙 목록 |
| 09 | GET | `/api/v1/principles/{principleId}` | `현재 화면` | Team A 원칙 상세 |
| 10 | PUT | `/api/v1/principles/{principleId}` | `현재 화면` | Team A 원칙 수정 |
| 11 | POST | `/api/v1/rag/ask` | `현재 화면` | Team A RAG 질문 |
| 12 | GET | `/api/v1/rag/sources` | `현재 화면` | Team A 자료 출처 목록 |
| 13 | GET | `/api/v1/risk/portfolio` | `현재 화면` | Team A 포트폴리오 위험 |
| 14 | GET | `/api/v1/system/health` | `현재 화면` | Team A 로그인 후 서버 상태 |
| 15 | GET | `/api/v2/signals/{symbol}` | `현재 화면` | Team A 종목 신호 |
| 16 | GET | `/api/v1/brokerage/mock/accounts/{accountId}/balances` | `Team A 필수` | KIS 모의계좌 잔고 |
| 17 | GET | `/api/v1/brokerage/mock/accounts/{accountId}/buyable` | `Team A 필수` | KIS 모의계좌 주문 가능 금액 |
| 18 | GET | `/api/v1/brokerage/mock/accounts/{accountId}/fills` | `Team A 필수` | KIS 모의계좌 체결 내역 |
| 19 | POST | `/api/v1/brokerage/mock/orders` | `Team A 필수` | KIS 모의주문 제출 |
| 20 | GET | `/api/v1/brokerage/orders/{orderId}` | `Team A 필수` | 주문 상태 조회 |
| 21 | POST | `/api/v1/brokerage/orders/{orderId}/cancel` | `Team A 필수` | 주문 취소 |
| 22 | GET | `/api/v1/brokerage/paper/accounts/{accountId}/balances` | `선택 기능` | 내부 가상계좌 잔고 |
| 23 | GET | `/api/v1/brokerage/paper/accounts/{accountId}/buyable` | `선택 기능` | 내부 가상계좌 주문 가능 금액 |
| 24 | GET | `/api/v1/brokerage/paper/accounts/{accountId}/fills` | `선택 기능` | 내부 가상계좌 체결 내역 |
| 25 | POST | `/api/v1/brokerage/paper/orders` | `선택 기능` | 내부 가상주문 제출 |
| 26 | POST | `/api/v1/consents` | `Team A 필수` | 사용자 동의 기록 |
| 27 | POST | `/api/v1/rag/answers/{answerId}/feedback` | `Team A 필수` | RAG 답변 평가 |
| 28 | GET | `/api/v1/rag/history` | `선택 기능` | RAG 이력 목록 |
| 29 | GET | `/api/v1/rag/history/{answerId}` | `선택 기능` | RAG 이력 상세 |
| 30 | DELETE | `/api/v1/rag/history/{answerId}` | `선택 기능` | RAG 이력 삭제 |
| 31 | POST | `/api/v1/principles` | `Team A 필수` | 원칙 최초 생성 |
| 32 | GET | `/api/v1/principles/{principleId}/versions` | `선택 기능` | 원칙 버전 이력 |
| 33 | POST | `/api/v1/decisions/evaluate-order` | `Team A 필수` | 주문 전 위험 판정 |
| 34 | GET | `/api/v1/risk/kill-switch` | `Team A 필수` | 주문 차단 장치 상태 조회 |
| 35 | POST | `/api/v1/risk/kill-switch` | `Team A 필수` | 주문 차단 장치 변경 |
| 36 | GET | `/api/v1/artifacts/ingest-status` | `운영자 전용` | 결과 파일 적재 상태 |
| 37 | GET | `/api/v1/async-jobs` | `운영자 전용` | 내부 작업 목록 |
| 38 | GET | `/api/v1/async-jobs/{jobId}` | `운영자 전용` | 내부 작업 상세 |
| 39 | POST | `/api/v1/brokerage/orders/{orderId}/reconcile` | `운영자 전용` | 주문 대사 |
| 40 | GET | `/api/v1/decisions/{decisionId}/audit` | `운영자 전용` | 주문 판단 감사 |
| 41 | GET | `/api/v1/stream-metrics` | `운영자 전용` | 내부 처리 지표 |
| 42 | GET | `/error` | `제품 기능 아님` | Spring 오류 처리 |
| 43 | POST | `/error` | `제품 기능 아님` | Spring 오류 처리 |
| 44 | PUT | `/error` | `제품 기능 아님` | Spring 오류 처리 |
| 45 | DELETE | `/error` | `제품 기능 아님` | Spring 오류 처리 |
| 46 | PATCH | `/error` | `제품 기능 아님` | Spring 오류 처리 |
| 47 | HEAD | `/error` | `제품 기능 아님` | Spring 오류 처리 |
| 48 | OPTIONS | `/error` | `제품 기능 아님` | Spring 오류 처리 |

## Team B 파일 계약과 현재 빈 부분

최종 명세는 Team B가 계약된 KIS 가격 자료, ECOS 거시 자료와 별도 승인된 뉴스 감성 요약을 입력으로
받아 LSTM/규칙 신호, 백테스트, 거래·자산 로그와 모델 보고서를 만들도록 정합니다. 현재 Compose는
받은 CSV/PTH 기반 미리보기만 실행하고, Dashboard의 모델·백테스트 화면은 synthetic Seed를 사용합니다.

따라서 아래 네 API에 Team B 실제 결과를 반영하는 것은 **현재 완료된 기능이 아니라 Owner 후속 작업**입니다.

- `GET /api/v2/signals/{symbol}`
- `GET /api/v1/dashboard/model-evaluations/{runId}`
- `GET /api/v1/dashboard/backtests/{runId}`
- `GET /api/v1/artifacts/ingest-status`

현재 결과 manifest도 입력 전체를 하나의 `sourceSnapshotSha256`으로만 묶습니다. KIS·ECOS·조건부 뉴스
입력 각각의 파일명과 해시를 검증할 별도 입력 manifest가 없으므로 Owner가 계약을 먼저 보강해야 합니다.

## OpenAPI에 아직 없는 기능

온보딩, 학습일지, 시장데이터, 사용자관리, 백업, RAG 문서관리와 자동매매 예약 설정 API는 현재
OpenAPI 48개에 없습니다. Team A/B가 임의로 endpoint를 만들지 않습니다. 필요한 화면과 동작을
PR 설명에 `OWNER_API_MISSING: 화면 이름 / 필요한 기능` 형식으로 적으면 Owner가 계약을 먼저 추가합니다.
