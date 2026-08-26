# Capstone AI Trading Coach

<!-- P1_FULL_APP_V2_AUTHORITY_BEGIN -->
> **현재 상태 (2026-08-26):** 통합 브랜치의 앱은 Docker에서 실행되지만 아직 GitHub `1.0.0`
> 최종 배포본은 아닙니다. Team A 화면 완성, Team B 실제 결과, KIS 모의투자 검증, 병합 후 재현 확인이
> 남아 있습니다. 현재 배포 계약은 `contracts/catalogs/p1-full-app-release-contract.v2.json`입니다.
<!-- P1_FULL_APP_V2_AUTHORITY_END -->

투자 원칙, 모델 예측, 백테스트, 위험 판정과 근거 자료를 한 화면에서 확인하고 한국투자증권
모의투자로 주문 흐름을 검증하는 교육용 트레이딩 코치입니다.

## 먼저 알아둘 한 가지

**프로그램을 켜 둔다고 자동으로 주문하지 않습니다.** 지금 들어 있는 예약 작업은 내부 작업 큐 처리,
상태 지표 집계, 오래된 RAG 이력 정리뿐입니다. 주문을 만드는 예약 작업은 없습니다.

- `./capstone up`: 외부 서비스와 증권계좌를 호출하지 않습니다.
- `./capstone up --models`: 위와 같고 로컬 AI 모델 두 개만 더 실행합니다.
- `./capstone up --mock`: KIS 모의투자 요청을 받을 수 있게 만들지만, 이것만으로 주문하지 않습니다.
- `./capstone mock certify ...`: 이 명령만 삼성전자 1주 모의 매수와 즉시 전량취소를 실제로 수행합니다.

완성 목표에는 자동 모의투자가 포함되지만, 24시간 내내 주문하는 방식은 아닙니다. 한국거래소 거래일과
거래시간에만 움직이는 별도 주문 스케줄러가 필요하며, Team B 실제 모델 결과와 Team A 주문 화면이
완성된 뒤 안전장치를 갖춰 구현해야 합니다. 자세한 판정은 [자동매매와 운영 경계](docs/decision-platform/P1_운영_후속_경계.md)에 있습니다.

## 처음 실행

Owner가 “통합 브랜치가 main에 병합됐다”고 안내한 뒤 아래 명령을 실행합니다. 아직 병합 안내를 받지
않았다면 `main`에는 이 실행 파일이 없을 수 있으므로 기다립니다.

준비물:

- Git
- Docker Engine 또는 Docker Desktop
- Docker Compose v2
- WSL/Linux의 Python 3와 OpenSSL
- Windows 사용자는 WSL도 설치

```bash
git switch main
git pull --ff-only origin main
./capstone doctor
./capstone up
```

Windows PowerShell에서는 저장소 폴더에서 다음처럼 실행해도 됩니다.

```powershell
.\capstone.ps1 doctor
.\capstone.ps1 up
```

첫 실행은 이미지를 만들고 공개 Seed DB를 준비하므로 시간이 걸릴 수 있습니다.
`CAPSTONE_UP=PASS`가 보이면 완료입니다.

```text
Dashboard        http://127.0.0.1:3000
Dashboard Health http://127.0.0.1:3000/healthz
API Health       http://127.0.0.1:18080/actuator/health
Swagger UI       http://127.0.0.1:18080/swagger-ui.html
```

Dashboard는 `http://localhost:3000`으로 접속해도 됩니다. 두 주소를 모두 허용합니다.

로그인 아이디는 `demo-user`입니다. 임시 비밀번호는 첫 실행 때 로컬에 생성되며 다음 파일에서만
확인합니다. 이 파일은 Git에 올라가지 않습니다.

```text
deploy/p1/.state-app/secrets/demo-user.password
```

## 자주 쓰는 명령

```bash
./capstone up             # 기본 5개 컨테이너
./capstone up --models    # 모델 포함 7개 컨테이너
./capstone status         # 현재 모드와 상태 확인
./capstone logs           # 민감값을 가린 로그 확인
./capstone smoke          # 로그인과 내부 작업 흐름 확인, 외부 호출 없음
./capstone down           # 종료하되 DB·Redis·모델 데이터는 보존
```

기본 5개는 PostgreSQL, Redis, 권한 서비스, 통합 백엔드, Dashboard입니다. 모델 모드는 공식 BAAI
BGE-M3 컨테이너와 공식 llama.cpp 기반 PaddleOCR-VL 컨테이너를 더합니다. 준비 작업은
`docker compose run --rm`으로 실행하므로 끝난 컨테이너를 남기지 않습니다. 모든 서비스 정의는
`deploy/p1/compose.yml` 하나가 기준입니다.

## Team B 결과가 보이는 이유

현재 화면의 Team B 결과는 받은 CSV와 PTH를 외부 네트워크 없이 실행한 미리보기입니다. 실제 Team B
완료 산출물이 아닙니다. 화면의 `TEAM_B_REAL_ARTIFACT_MISSING`은 사이트 오류가 아니라 “Team B가
고정된 결과 파일 10개를 아직 전달하지 않았다”는 뜻입니다.

## Owner만 하는 KIS 모의투자 검증

여기서 계좌와 주문은 모두 **KIS 모의투자 계좌**를 뜻합니다. 실계좌 주문·정정·취소는 코드와 설정에서
막혀 있습니다. 처음 한 번은 반드시 기본 앱을 먼저 실행합니다.

```bash
./capstone up
./capstone mock configure
./capstone mock doctor

# XKRX 거래일 09:10~15:00 KST에만 실행
./capstone mock certify --symbol 005930 --quantity 1

# 모델이 필요 없으면 5개, 필요하면 7개
./capstone up --mock
./capstone up --models --mock
```

`mock doctor`는 자격증명 파일의 형식과 권한만 확인합니다. `mock certify`는 현재 코드, 원격 브랜치,
열린 PR과 필수 CI를 확인한 뒤 현재가 1회와 모의계좌 작업 7회를 수행합니다. 주문·취소는 재시도하지
않습니다. 취소 실패나 부분체결이 나오면 자동으로 매도하거나 다시 주문하지 말고 KIS 모의투자 화면에서
직접 확인해야 합니다.

`up --mock`은 인증 요청 해시, 인증 당시 Git tree와 현재 Git tree, 현재 작업 폴더의 clean 상태를
다시 검사합니다. PR commit과 main 병합 commit의 번호가 달라도 파일 tree가 완전히 같으면 허용하고,
파일이 하나라도 바뀌면 거부합니다. 이 인증을 통과해도 자동 주문 스케줄러가 생기지는 않습니다.

## 팀원에게 보낼 문서

- [Team A Dashboard 완료 요청서](docs/decision-platform/P1_TEAM_A_DASHBOARD_완료_요청서.md)
- [Team B Return Engine 완료 요청서](docs/decision-platform/P1_TEAM_B_RETURN_ENGINE_완료_요청서.md)
- [두 팀 결과를 받은 뒤 Owner 체크리스트](docs/decision-platform/P1_TEAM_A_B_수신_후_통합_체크리스트.md)
- [OpenAPI 48개 사용 현황](docs/decision-platform/P1_API_USAGE_MATRIX.md)

Team A와 Team B는 `mock configure`나 `mock certify`를 실행하지 않습니다. 외부 서비스, 계좌와 주문
검증은 Owner가 별도로 수행합니다.

## 문제가 생기면

| 증상 | 확인할 것 |
|---|---|
| `./capstone` 파일이 없음 | 통합 PR의 main 병합 안내를 받았는지 확인 |
| Docker 연결 오류 | Docker를 켠 뒤 `./capstone doctor` |
| 포트 3000 또는 18080 충돌 | 해당 포트를 쓰는 기존 프로그램 종료 |
| Dashboard가 준비되지 않음 | `./capstone status`, `./capstone logs` |
| 모델 첫 실행이 오래 걸림 | 고정된 모델을 처음 다운로드하고 검사하는 중인지 확인 |
| Team B 실제 결과 경고 | 미리보기는 정상이며 Team B 결과 수신 전까지 경고 유지 |
| KIS 모의투자 명령 거부 | 기본 앱 실행, 자격증명, 거래일·시간, PR·CI 상태 확인 |

자세한 복구 절차는 [동일 환경 재현 가이드](docs/decision-platform/P1_GIT_PULL_동일환경_재현_가이드.md),
환경값 목록은 [환경 변수 참고 문서](docs/decision-platform/P1_ENV_REFERENCE.md)를 확인합니다.

## 최종 배포 전에 남은 일

1. Team A가 명세에 배정된 화면 API를 실제 Spring과 연결하고 성공 응답을 각각 증명합니다.
2. Team B가 계약된 입력으로 실제 모델·백테스트 결과 10개를 만들어 전달합니다.
3. Owner가 Team B 결과를 백엔드에 적재하는 변환 경로와 조회 API를 검증합니다.
4. XKRX 거래시간에 KIS 모의투자 수동 인증을 한 번 수행합니다.
5. PR 병합, main CI, 새 폴더에서 `git pull` 재현을 확인한 뒤에만 최종 배포를 승인합니다.

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
