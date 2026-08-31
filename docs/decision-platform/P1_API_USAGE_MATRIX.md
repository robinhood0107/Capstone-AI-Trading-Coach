# OpenAPI 68개 사용 현황

## 한눈에 보는 결론

기준 파일은 `contracts/openapi/openapi.json`입니다. 현재 HTTP 작업은 정확히 68개이고, 아래 표에서
각 작업을 한 번씩만 분류했습니다.

| 구분 | 개수 | 뜻 |
|---|---:|---|
| 현재 화면 | 19 | Team A 코드에 호출 지점이 있음. 실제 Spring 성공 검증은 아직 전부 증명되지 않음 |
| Team A 필수 | 23 | 기존 18개와 예산·가변수량 Automation v2 다섯 개를 실제 Spring으로 검증해야 함 |
| 선택 기능 | 13 | API는 있지만 최종 명세가 Team A 필수 화면으로 배정하지 않음 |
| 운영자 전용 | 6 | 적재·작업·대사·감사용. 일반 사용자 화면에 억지로 붙이지 않음 |
| 제품 기능 아님 | 7 | Spring 공통 오류 처리 경로 |

Team B가 직접 호출할 Spring REST API는 **0개**입니다. Team B는 파일로 입력을 받고 파일로 결과를
전달합니다.

## 현재 테스트 증거의 정확한 수준

Owner backend acceptance는 versioned `p1-team-a-acceptance.v2`와 generated client로 아래 `현재 화면`
15개와 `Team A 필수` 23개를 same-origin 실제 Spring에서 검증합니다. v2 arm은 현재 qualified risk-balance
근거가 없으므로 `BLOCKED_INCOMPLETE_RISK_BALANCE` 409가 expected status이며 나머지는 성공 상태다.
Team A는 같은 exact-38 matrix와 blocker가 보이는 실제 사용자 화면을 통과시켜야 합니다.

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
| 16 | GET | `/api/v2/rag/corpus-status` | `현재 화면` | Team A RAG 코퍼스 준비 상태 |
| 17 | GET | `/api/v2/rag/consent` | `현재 화면` | Team A 외부 처리 동의 상태 |
| 18 | POST | `/api/v2/rag/consents` | `현재 화면` | Team A 외부 처리 동의 기록 |
| 19 | POST | `/api/v2/rag/ask` | `현재 화면` | Team A RAG 질문 (실제 인용) |
| 20 | GET | `/api/v1/brokerage/mock/accounts/{accountId}/balances` | `Team A 필수` | KIS 모의계좌 잔고 |
| 21 | GET | `/api/v1/brokerage/mock/accounts/{accountId}/buyable` | `Team A 필수` | KIS 모의계좌 주문 가능 금액 |
| 22 | GET | `/api/v1/brokerage/mock/accounts/{accountId}/fills` | `Team A 필수` | KIS 모의계좌 체결 내역 |
| 23 | POST | `/api/v1/brokerage/mock/orders` | `Team A 필수` | KIS 모의주문 제출 |
| 24 | GET | `/api/v1/brokerage/orders/{orderId}` | `Team A 필수` | 주문 상태 조회 |
| 25 | POST | `/api/v1/brokerage/orders/{orderId}/cancel` | `Team A 필수` | 주문 취소 |
| 26 | GET | `/api/v1/brokerage/paper/accounts/{accountId}/balances` | `선택 기능` | 내부 가상계좌 잔고 |
| 27 | GET | `/api/v1/brokerage/paper/accounts/{accountId}/buyable` | `선택 기능` | 내부 가상계좌 주문 가능 금액 |
| 28 | GET | `/api/v1/brokerage/paper/accounts/{accountId}/fills` | `선택 기능` | 내부 가상계좌 체결 내역 |
| 29 | POST | `/api/v1/brokerage/paper/orders` | `선택 기능` | 내부 가상주문 제출 |
| 30 | POST | `/api/v1/consents` | `Team A 필수` | 사용자 동의 기록 |
| 31 | POST | `/api/v1/rag/answers/{answerId}/feedback` | `Team A 필수` | RAG 답변 평가 |
| 32 | GET | `/api/v1/rag/history` | `선택 기능` | RAG 이력 목록 |
| 33 | GET | `/api/v1/rag/history/{answerId}` | `선택 기능` | RAG 이력 상세 |
| 34 | DELETE | `/api/v1/rag/history/{answerId}` | `선택 기능` | RAG 이력 삭제 |
| 35 | POST | `/api/v1/principles` | `Team A 필수` | 원칙 최초 생성 |
| 36 | GET | `/api/v1/principles/{principleId}/versions` | `선택 기능` | 원칙 버전 이력 |
| 37 | POST | `/api/v1/decisions/evaluate-order` | `Team A 필수` | 주문 전 위험 판정 |
| 38 | GET | `/api/v1/risk/kill-switch` | `Team A 필수` | 주문 차단 장치 상태 조회 |
| 39 | POST | `/api/v1/risk/kill-switch` | `Team A 필수` | 주문 차단 장치 변경 |
| 40 | GET | `/api/v1/automation/status` | `Team A 필수` | 자동운용 상태·certification·Kill Switch |
| 41 | POST | `/api/v1/automation/arm` | `Team A 필수` | 명시적 brokerage mode 자동운용 활성화 |
| 42 | POST | `/api/v1/automation/disarm` | `Team A 필수` | 신규 주문 중지, outstanding 대사 보존 |
| 43 | GET | `/api/v1/automation/runs` | `Team A 필수` | owner-scoped 최근 자동운용 실행 |
| 44 | POST | `/api/v1/journals` | `Team A 필수` | owner 학습일지 생성 |
| 45 | GET | `/api/v1/journals` | `Team A 필수` | owner 학습일지 목록 |
| 46 | GET | `/api/v2/automation/status` | `Team A 필수` | 실제 control·policy·blocker·포지션 수 |
| 47 | PUT | `/api/v2/automation/policy` | `Team A 필수` | 예산·손절·익절 CAS 저장 |
| 48 | POST | `/api/v2/automation/arm` | `Team A 필수` | 현재 risk-balance blocker 409 검증 |
| 49 | GET | `/api/v2/automation/runs` | `Team A 필수` | 가변수량·체결·exit reason 실행 이력 |
| 50 | GET | `/api/v2/automation/positions` | `Team A 필수` | active bot-owned 포지션 최대 5개 |
| 51 | PATCH | `/api/v1/journals/{journalId}` | `선택 기능` | CAS 기반 학습일지 전체 교체 |
| 52 | DELETE | `/api/v1/journals/{journalId}` | `선택 기능` | CAS 기반 학습일지 soft delete |
| 53 | GET | `/api/v2/rag/history` | `선택 기능` | RAG v2 이력 목록 |
| 54 | GET | `/api/v2/rag/history/{answerId}` | `선택 기능` | RAG v2 이력 상세 |
| 55 | DELETE | `/api/v2/rag/history/{answerId}` | `선택 기능` | RAG v2 이력 삭제 |
| 56 | GET | `/api/v1/artifacts/ingest-status` | `운영자 전용` | 결과 파일 적재 상태 |
| 57 | GET | `/api/v1/async-jobs` | `운영자 전용` | 내부 작업 목록 |
| 58 | GET | `/api/v1/async-jobs/{jobId}` | `운영자 전용` | 내부 작업 상세 |
| 59 | POST | `/api/v1/brokerage/orders/{orderId}/reconcile` | `운영자 전용` | 주문 대사 |
| 60 | GET | `/api/v1/decisions/{decisionId}/audit` | `운영자 전용` | 주문 판단 감사 |
| 61 | GET | `/api/v1/stream-metrics` | `운영자 전용` | 내부 처리 지표 |
| 62 | GET | `/error` | `제품 기능 아님` | Spring 오류 처리 |
| 63 | POST | `/error` | `제품 기능 아님` | Spring 오류 처리 |
| 64 | PUT | `/error` | `제품 기능 아님` | Spring 오류 처리 |
| 65 | DELETE | `/error` | `제품 기능 아님` | Spring 오류 처리 |
| 66 | PATCH | `/error` | `제품 기능 아님` | Spring 오류 처리 |
| 67 | HEAD | `/error` | `제품 기능 아님` | Spring 오류 처리 |
| 68 | OPTIONS | `/error` | `제품 기능 아님` | Spring 오류 처리 |
| 69 | PUT | `/api/v2/strong-llm/settings` | `Team A 필수` | Strong LLM provider·2차 provider·답변 언어·하루 호출 상한과 API 키를 화면에서 저장한다. 키는 쓰기 전용이고 응답 본문이 없다. 현재 값과 키 마지막 네 글자는 `GET /api/v2/rag/corpus-status`가 함께 돌려준다 |

historical exact-48 root에서 `getMockBuyable`의 strict `symbol`/`price` query parameter annotation이
누락돼 있다. 기존 OpenAPI 의미를 넓히지 않고 preserved exact-33/v2 exact-38 catalog의 bounded client adapter에만 두 필드를
기록하며 runtime parser의 required/unknown-field 경계를 그대로 따른다.

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

온보딩, 시장데이터, 사용자관리, 백업과 RAG 문서관리 API는 현재
OpenAPI 68개에 없습니다. Team A/B가 임의로 endpoint를 만들지 않습니다. 필요한 화면과 동작을
PR 설명에 `OWNER_API_MISSING: 화면 이름 / 필요한 기능` 형식으로 적으면 Owner가 계약을 먼저 추가합니다.
