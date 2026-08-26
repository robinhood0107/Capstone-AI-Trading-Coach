# Experience Dashboard

이 폴더는 Team A가 담당하는 Next.js Dashboard입니다. 전체 앱을 처음 실행하는 사람은 이 폴더에서
서버를 따로 켜지 말고 저장소 루트의 [README](../../README.md)를 따라 `./capstone up`을 실행하세요.

Team A가 완료해야 할 정확한 API 목록, 화면 순서, 테스트와 제출물은
[Team A 대시보드 완료 요청](../../docs/decision-platform/P1_TEAM_A_DASHBOARD_완료_요청서.md)에 있습니다.

## 현재 구조

- production image는 프론트 가짜 응답이 아닌 로컬 Spring API를 사용합니다.
- 브라우저는 Dashboard와 같은 주소의 `/api/...`만 호출합니다.
- Next.js server가 내부 `decision-platform:8080`으로 전달합니다.
- `/healthz`를 제공합니다.
- 컨테이너는 non-root, read-only로 실행됩니다.
- `http://localhost:3000`과 `http://127.0.0.1:3000`을 모두 사용할 수 있습니다.
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
P1_USER_PASSWORD_FILE=../../deploy/p1/.state-app/secrets/demo-user.password \
  npm run test:e2e:live
```

Playwright는 `skip 0`이어야 하며 4xx와 5xx를 모두 실패로 처리해야 합니다. 현재 체크인된 테스트는
로그인 외의 API를 개별 증명하지 못하므로 완료 요청서에 적힌 27개 method/path/성공 상태 검증을
추가해야 합니다.

## 현재 남은 핵심 작업

- 기존 화면 API 15개의 정확한 성공 검증
- 명세가 Team A에 배정한 추가 API 12개의 화면 연결
- 주문 전 판정 → 주문 차단 확인 → 명시적 모의주문 → 조회·취소 흐름
- “자동주문 작동 중”처럼 실제와 다른 문구 수정
- Team B 미리보기와 실제 결과 구분
- OpenAPI에 없는 온보딩·학습일지·자동매매 예약 기능은 `OWNER_API_MISSING`으로 보고

`SETUP.md`와 `OVERVIEW.md`에는 처음 수신한 설계·개발 기록이 포함되어 있습니다. 현재 통합 실행
명령과 완료 판정은 루트 README와 Team A 완료 요청서를 우선합니다.

<!-- historical integration verifier marker; 일반 사용자에게 표시하지 않는다.
P1 full-app v2
DASHBOARD_UI=PARTIAL_TEAM_A_ACTION_REQUIRED
-->
