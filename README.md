# Capstone AI Trading Coach

<!-- P1_FULL_APP_V2_AUTHORITY_BEGIN -->
> **1.0.0 current authority (2026-08-26):** full-app v2는 통합 중이며 GitHub `1.0.0`
> Release는 아직 없습니다. 현재 계약은 `contracts/catalogs/p1-full-app-release-contract.v2.json`입니다.
<!-- P1_FULL_APP_V2_AUTHORITY_END -->

투자 원칙, 의사결정, 위험 점검, RAG 근거, 모델·백테스트 결과를 한 화면에서 확인하는 교육용
트레이딩 코치입니다. 기본 실행은 외부 Provider와 증권계좌를 전혀 호출하지 않습니다.

## 가장 빠른 실행

준비물은 Git, Docker Engine, Docker Compose v2입니다. Java·Node.js·Python은 Docker 이미지 안에
포함되므로 설치하지 않아도 됩니다.

```bash
git switch main
git pull --ff-only origin main
./capstone up
```

첫 실행은 이미지를 빌드하고 공개 Seed DB를 가져오므로 시간이 걸릴 수 있습니다. 완료되면 정확히
다섯 개의 장기 컨테이너가 실행됩니다.

```text
Dashboard  http://127.0.0.1:3000
API Health http://127.0.0.1:18080/actuator/health
```

Dashboard는 `http://localhost:3000`으로 접속해도 됩니다. Spring은 개발용 루프백 origin 두 개만
정확히 허용하고, 다른 origin은 허용하지 않습니다.

로그인 계정과 임시 비밀번호는 로컬에서 자동 생성됩니다. 비밀번호 파일은
`deploy/p1/.state-app/secrets/demo-user.password`이며 Git에 올라가지 않습니다.

## 자주 쓰는 명령

```bash
./capstone up             # 기본 5개: DB, Redis, 권한, 백엔드, Dashboard
./capstone up --models    # 전체 7개: 기본 + 공식 BGE-M3 + llama.cpp PaddleOCR-VL
./capstone status         # 현재 선택한 모드와 health 확인
./capstone logs           # 민감값을 마스킹한 로그
./capstone smoke          # 로그인·DB async E2E; Provider 호출 0
./capstone down           # 컨테이너만 종료, DB·Redis·모델 volume 보존
```

`./capstone up`은 DB migration·Seed·Team B preview 생성을 `docker compose run --rm`으로 처리합니다.
그래서 준비 작업이 끝난 뒤에는 종료된 one-shot 컨테이너가 남지 않습니다. 모든 서비스 정의는
`deploy/p1/compose.yml` 하나가 기준입니다.

## 모델 포함 실행

```bash
./capstone up --models
```

- BGE-M3: 공식 BAAI revision을 Hugging Face 공식 TEI 이미지로 실행합니다.
- PaddleOCR-VL: 공식 GGUF를 checksum으로 검증한 뒤 공식 llama.cpp server 이미지로 실행합니다.
- 모델 cache volume은 `./capstone down` 뒤에도 보존됩니다.

## Team B 결과 표시

현재 Team B 수신본은 포함된 CSV·PTH로 만든 `LEGACY_RECEIVED_PREVIEW`입니다. Dashboard가 read-only
artifact volume에서 JSON을 직접 읽으며, 별도 artifact HTTP 컨테이너는 없습니다.

`TEAM_B_REAL_ARTIFACT_MISSING`은 사이트가 고장 났다는 뜻이 아닙니다. Team B가 명세의 exact artifact
10개와 manifest를 아직 전달하지 않았으므로 현재 결과를 실제 Team B 통합 완료 증거로 쓰지 말라는
표시입니다.

## Owner 전용 KIS 모의투자

여기서 “계좌·주문 호출”은 한국투자증권 **모의투자**만 뜻합니다. 실계좌 주문·정정·취소 origin과
TR ID는 코드 allowlist에 없으며 환경변수로도 열 수 없습니다.

```bash
./capstone mock configure
./capstone mock doctor
./capstone mock certify --symbol 005930 --quantity 1
./capstone up --models --mock
```

- `configure`는 자격증명을 로컬 owner 전용 파일에 저장하며 값을 출력하지 않습니다.
- `certify`는 clean PR HEAD·CI·일반 보안검사 증거와 거래시간을 모두 확인한 뒤에만
  `005930 / BUY / LIMIT / 1주` 모의 주문 1회와 즉시 전량취소·대사를 실행합니다.
- 거래시간 밖이거나 승인 증거가 없으면 Provider 호출 0으로 종료합니다.
- 주문·취소 write는 retry 0이며 실패 시 자동 매도나 재주문을 하지 않습니다.
- `--mock`은 PASS certification receipt가 없으면 시작되지 않으며 `INTERNAL_PAPER`로 fallback하지
  않습니다.

Owner 절차와 호출 결과 판정은 [KIS 모의투자 운영 경계](docs/decision-platform/P1_운영_후속_경계.md)를
따릅니다.

## 문제가 생기면

| 증상 | 확인할 것 |
|---|---|
| `docker` 연결 오류 | Docker Desktop/Engine을 켠 뒤 `./capstone doctor` |
| 포트 3000 또는 18080 충돌 | 해당 포트를 쓰는 기존 프로그램 종료 |
| Dashboard가 준비되지 않음 | `./capstone status`, `./capstone logs` |
| 모델 첫 실행이 오래 걸림 | `./capstone status`; 고정 revision 모델 다운로드·검증 중일 수 있음 |
| Team B real artifact 경고 | preview는 정상이며 Team B 실물 수신 전까지 의도된 표시 |
| KIS mock 명령 거부 | `./capstone mock doctor`; 거래시간·PR/CI·certification 확인 |

상세 환경·검증·복구 절차는 [동일 환경 재현 가이드](docs/decision-platform/P1_GIT_PULL_동일환경_재현_가이드.md)를
참고하세요.

자동 생성 secret의 긴 exact inventory는 [환경 변수 reference](docs/decision-platform/P1_ENV_REFERENCE.md)에
분리했습니다.

## 팀별 완료 요청

- [Team A Dashboard 완료 요청서](docs/decision-platform/P1_TEAM_A_DASHBOARD_완료_요청서.md)
- [Team B Return Engine 완료 요청서](docs/decision-platform/P1_TEAM_B_RETURN_ENGINE_완료_요청서.md)
- [두 팀 수신 후 Owner 통합 체크리스트](docs/decision-platform/P1_TEAM_A_B_수신_후_통합_체크리스트.md)
- [API 전수 사용 현황](docs/decision-platform/P1_API_USAGE_MATRIX.md)

## 현재 릴리스 상태

기본 5개와 모델 포함 7개 local Compose는 통합 대상입니다. GitHub `1.0.0` FINAL은 Team B real
artifact, Team A live API E2E, provider-read gate, 일반 보안·supply-chain gate와 clean pull 재현까지
모두 통과한 뒤에만 선언합니다.

```text
CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT
KIS_LIVE_BROKERAGE_CALLS=0
```

<!-- 보존된 Pre-S5 공개 계약 marker. 현재 주문 권한을 만들지 않는다.
PRE_S5_DOC_TRUTH_FREEZE_ADDENDUM_VERIFIED
PRE_S5_EXECUTION_OWNER=DECISION_PLATFORM
PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES
S4_8A=CONTRACT_LOCKED
S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE
-->

<!-- 역사적 source-development 회귀 marker. 일반 사용자는 위 ./capstone 명령만 사용한다.
docker compose --env-file .env -f infra/docker-compose.infra.yml run --rm role-bootstrap
./gradlew bootRun
market_quote_observations
instrument_catalog_observations
portfolio_balance_observations
portfolio_position_observations
provider를 직접 호출하지 않는다
production 운영 seed는 가짜 값으로 대체하지 않으며 증거가 없으면 HOLD
decision_fill_writer
V6/V9/V14
-->
