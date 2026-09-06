# Experience Dashboard

이 폴더는 Team A가 담당하는 Next.js Dashboard입니다. 전체 앱을 처음 실행하는 사람은 이 폴더에서
서버를 따로 켜지 말고 저장소 루트의 [README](../../README.md)를 따라 `./capstone up`을 실행하세요.

Team A가 완료해야 할 정확한 API 목록, 화면 순서, 테스트와 제출물은
[Team A 최종 통합 요청서](../../docs/handoff/P1_TEAM_A_최종_통합_요청서.md)에 있습니다.

## 현재 구조

- production image는 프론트 가짜 응답이 아닌 로컬 Spring API를 사용합니다.
- 브라우저는 Dashboard와 같은 주소의 `/api/...`만 호출합니다.
- Next.js server가 내부 `decision-platform:8080`으로 전달합니다.
- `/healthz`를 제공합니다.
- 컨테이너는 non-root, read-only로 실행됩니다.
- `http://localhost:3000`과 `http://127.0.0.1:3000`을 모두 사용할 수 있습니다.
- exact-33 generated typed client와 Owner backend acceptance runner가 제공됩니다.
- Python gRPC, PostgreSQL, Redis와 Team B 파일을 브라우저가 직접 읽지 않습니다.

과거 문서의 `api-edge`, 직접 `NEXT_PUBLIC_API_BASE_URL` 연결, `127.0.0.1` CORS 차단 설명은 현재
구성과 다릅니다.

## 담당 범위

| Team A가 하는 일 | Team A가 하지 않는 일 |
|---|---|
| Spring이 준 ViewModel과 판정을 읽기 쉽게 표시 | 모델 학습과 백테스트 계산 |
| 모델 비교, 백테스트, 주문 검토, RAG 출처 화면 | RiskEngine 판정 재계산 |
| KIS 모의투자 주문 검토·결과 흐름 | KIS 자격증명과 외부 호출 |
| 실제 API 연결 Playwright 테스트 | 새 backend endpoint 임의 생성 |

프로그램을 켜 둔다고 자동 주문되지 않습니다. 자동매매 예약 API도 아직 없습니다. 홈 화면과 주문
화면에서 자동 주문이 실행 중인 것처럼 표현하지 않습니다.

## 로컬 품질 검사

Node.js 22 기준입니다.

```bash
npm ci
npm run typecheck
npm run lint
npm test
npm run build
```

실제 로컬 Spring 연결 E2E는 저장소 루트에서 `./capstone up`을 먼저 실행한 뒤 진행합니다.

```bash
./capstone team-a acceptance
```

Owner runner는 실제 same-origin Spring exact-33, `skip 0`, 4xx/5xx 실패, provider call 0을 증명합니다.
JWT/password/raw response를 report/trace에 남기지 않으며 종료 시 Kill Switch와 automation을 복구합니다.
이 테스트는 backend prerequisite일 뿐 Team A production UI 완료 증거가 아닙니다.

## 붙인 것

- 원칙 만들기(`POST /principles`)와 버전 이력(`/versions`). 원칙이 없으면 주문 검토에 들어갈
  수 없는 막다른 길이었다.
- 주문 흐름 전체 — 평가(`evaluate-order`) → 확인 → 모의주문 제출(`brokerage/mock/orders`) →
  상태 조회·취소. 제출까지 여섯 관문을 순서대로 지나며, 막힌 관문은 이유와 함께 화면에 남는다
  (`src/features/order-review/orderGates.ts`).
- Kill Switch 조작. 정지는 누구나, 해제는 ADMIN 만(`KillSwitchTransitionPolicy.kt:22`).
- 자동운용 v3 — ATR 추적손절·보유 기간·모델 매도와 AI 판단 근거(인용문·출처)를 화면에 낸다.
  정책 저장도 v3 다(v2 로 저장하면 `POLICY_V3_REQUIRED` 를 풀 수 없다).
- RAG 답변 평가와 질문 기록 삭제.
- 시스템 상태(`/system/health`)를 설정 화면에.
- 로그인 전 소개 페이지(`src/features/intro/`). 인증되면 기존 대시보드가 그대로 뜬다.

## 현재 남은 핵심 작업

- `POST /api/v1/consents` — RAG v2 가 자체 동의 경로(`/api/v2/rag/consents`)를 쓰고 있어 화면에
  필요한 자리를 아직 찾지 못했다. 필요 없다고 판단되면 목록에서 뺀다.
- 자동운용 중지(disarm) — 계약 테스트와 e2e 가 그 버튼의 부재를 못박고 있다(cdd0f5b8). 확인해
  보니 우회 대상이 아니라 설계였다. 비상 정지 수단은 Kill Switch 이고 DB 가 그렇게 강제한다
  (`V93__p1_automation_pipeline_continuity.sql:128`, `V109__...:60`).
- 런타임 관찰 가능성 — `P1_AUTOMATION_RUNTIME_ENABLED=false` 로 자동매매 프로세스가 안 떠
  있어도 화면은 `ARMED` 로 보인다. 런타임 생사·다음 경계를 알려주는 엔드포인트가 백엔드에
  없어 화면에서 구분할 방법이 없다.
- 계약 드리프트 — 화면이 쓰는 5개 경로가 컨트롤러 `@Hidden` 때문에 OpenAPI 에 없다. 동작은
  정상이지만 `./capstone team-a acceptance` 가 이 5개를 대상에 넣지 못한다. 다만
  `contracts/openapi/openapi.json` 은 pre-S5 문서 진실 동결에서 **IMMUTABLE** 이므로
  (`contracts/verify_pre_s5_doc_truth_freeze.py`) 재생성은 그 동결을 먼저 푸는 결정이 필요하다.

`SETUP.md`와 `OVERVIEW.md`에는 처음 수신한 설계·개발 기록이 포함되어 있습니다. 현재 통합 실행
명령과 완료 판정은 루트 README와 Team A 완료 요청서를 우선합니다.

<!-- historical integration verifier marker; 일반 사용자에게 표시하지 않는다.
P1 full-app v2
DASHBOARD_UI=PARTIAL_TEAM_A_ACTION_REQUIRED
-->
