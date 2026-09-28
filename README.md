# MARS — 투자 원칙 검증형 모의 자동매매

국내 주식과 금 ETF/ETN 후보를 검토하고, 사용자가 설정한 원칙과 위험 제약을 통과한 주문만 KIS_MOCK에서 시험하는 서비스입니다. 실계좌 주문과 전략의 수익성은 이 저장소의 공개 보고서로 입증하지 않습니다.

## 공개 최종보고서

- [PDF — 읽기](docs/01.보고서/03.최종보고서.pdf)
- [DOCX — 편집](docs/01.보고서/03.최종보고서.docx)

보고서의 화면 캡처는 기능 예시이며, 캡처 안의 수치·시각·계정 상태는 체결이나 성과의 증거가 아닙니다.

## 배포 및 실행

### 설치 절차

#### 준비 사항

| 항목 | 내용 |
|---|---|
| OS | Linux `amd64` 또는 Windows WSL2(Ubuntu). 레포와 데이터는 Linux 홈 아래에 둠 |
| 필수 도구 | Docker Engine(또는 WSL 통합이 켜진 Docker Desktop), Docker Compose v2, Git, Python 3, OpenSSL |
| 외부 키 | KIS 모의투자 App Key·Secret·계좌번호, (선택) OpenDART, Vertex AI, Voyage, Google·Kakao OAuth |

#### 환경 변수 설정

```bash
git clone https://github.com/robinhood0107/Capstone-AI-Trading-Coach.git
cd Capstone-AI-Trading-Coach
install -m 600 .env.example .env   # 값 입력 후에도 권한 0600 유지
```

| `.env` 키 | 용도 |
|---|---|
| `KIS_MOCK_APP_KEY`, `KIS_MOCK_APP_SECRET`, `KIS_MOCK_ACCOUNT_NO` | KIS 모의투자 계좌 |
| `OPENDART_API_KEY` | 공시 수집(선택) |
| `MARS_VERTEX_PROJECT_ID`, `MARS_VERTEX_MODEL_ID`, `MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64` | 금융 Agent(선택). JSON은 `python3 deploy/p1/import_vertex_service_account_to_env.py <json>`으로 가져옴 |
| `VOYAGE_API_KEY` | 검색 임베딩(선택) |
| `MARS_PUBLIC_ORIGIN`, `GOOGLE_OIDC_CLIENT_ID`, `GOOGLE_OIDC_CLIENT_SECRET`, `KAKAO_OAUTH_CLIENT_ID`, `KAKAO_OAUTH_CLIENT_SECRET` | Google·Kakao 로그인(선택). 공개 HTTPS 주소와 두 제공자가 모두 있을 때만 켜짐 |

Google·Kakao 콘솔에는 `<MARS_PUBLIC_ORIGIN>/api/v1/auth/oidc/callback/google`, `<MARS_PUBLIC_ORIGIN>/api/v1/auth/oidc/callback/kakao`를 리디렉션 URI로 등록합니다.

#### 실행

```bash
./capstone up --mock
```

- 첫 실행 때 비밀값을 생성하고 이미지를 빌드한 뒤 DB 마이그레이션까지 적용합니다. `--mock`을 빼면 자동매매가 꺼진 채로 뜹니다.
- 출력에 `CAPSTONE_UP=PASS`와 `CAPSTONE_AUTOMATION_RECONCILED=ARMED`가 모두 보이면 기동이 끝난 것입니다.
- 첫 로그인 비밀번호는 `deploy/p1/.state-app/secrets/demo-user.password`에 있습니다. 바꾸려면 `./capstone credential rotate user /절대경로/새비밀번호파일`을 실행합니다. `demo-user`는 운영자 서명 번들로 관리되어 화면에서는 바꿀 수 없습니다.
- 이메일로 가입한 계정은 **설정 → 로그인 방법 → 비밀번호 변경**에서 현재 비밀번호를 확인하고 바꿉니다. 바꾸면 다른 기기의 로그인은 모두 해제됩니다.

| 주소 | 설명 |
|---|---|
| http://127.0.0.1:3000 | 웹 화면 |
| http://127.0.0.1:18080/swagger-ui.html | API 문서 |

```bash
./capstone status    # 컨테이너 상태
./capstone logs      # 로그
./capstone down      # 종료 (데이터 볼륨은 유지)
```

#### 개인 서버로 옮기기

데이터와 비밀값은 레지스트리를 거치지 않고 서버로 직접 복사합니다.

```bash
# 1) 현재 PC: 스택을 내리고 DB 볼륨과 state를 묶는다
./capstone down
docker run --rm -v capstone-p1_p1-postgres-current:/from:ro -v "$PWD":/to alpine \
  tar czf /to/p1-postgres.tgz -C /from .
tar czf p1-state.tgz -C deploy/p1 .state-app
chmod 600 p1-postgres.tgz p1-state.tgz
scp p1-postgres.tgz p1-state.tgz .env <user>@<server>:~/

# 2) 서버: 같은 커밋을 받고 데이터를 복원한다
git clone https://github.com/robinhood0107/Capstone-AI-Trading-Coach.git && cd Capstone-AI-Trading-Coach
install -m 600 ~/.env .env
tar xzf ~/p1-state.tgz -C deploy/p1
docker volume create capstone-p1_p1-postgres-current
docker run --rm -v capstone-p1_p1-postgres-current:/to -v ~:/from:ro alpine \
  tar xzf /from/p1-postgres.tgz -C /to

# 3) 서버: 인증서를 두고 서버용 오버레이로 기동한다
install -m 640 fullchain.pem deploy/p1/.state-app/secrets/proxy-tls.crt
install -m 640 privkey.pem  deploy/p1/.state-app/secrets/proxy-tls.key
P1_COMPOSE_OVERLAY=deploy/p1/compose.server.yml ./capstone up --mock
```

서버에서는 `compose.server.yml`이 TLS 프록시를 443/80으로 공개하고, 앱 포트는 서버 안(127.0.0.1)에만 둡니다.

#### 개발 환경에서 API 직접 실행

인증용 DB 역할을 Flyway보다 먼저 준비한 뒤 Spring API를 실행합니다. KIS 모의 체결 관측은 `decision_fill_writer` 역할로 적재하며 관련 마이그레이션 경계는 `V6/V9/V14`입니다.

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml run --rm role-bootstrap
cd workspaces/decision-platform/spring-api && ./gradlew bootRun
```

#### 공개 체험판 이미지 (선택)

로그인 없는 금융 Agent 체험판 DEMO와 계좌 기능 FULL은 [Docker Hub](https://hub.docker.com/r/pjjpjj111/mars-full)와 [GitHub Release](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/releases/latest)의 digest 고정 Compose로도 실행할 수 있습니다. 이미지에는 코드만 들어 있고 사용자 데이터는 포함되지 않습니다. NAS에서 Portainer 스택 하나로 운영하는 방법과 기존 배포를 옮기는 절차는 [deploy/p1/portainer/README.md](deploy/p1/portainer/README.md)에 있습니다.

### 문제 해결

| 증상 | 해결 |
|---|---|
| `up`이 `CAPSTONE_UP=PASS` 없이 끝남 | 마지막 `P1_ERROR=` 값을 확인. 종료코드 0이어도 중간 실패일 수 있음 |
| `P1_ERROR=root_env_boundary` | `.env` 권한을 `chmod 600 .env`로 맞춤 |
| `P1_ERROR=secret_inventory` | `deploy/p1/.state-app/secrets`에 예상 밖 파일이 있음. 해당 파일을 secrets 밖으로 옮김 |
| `bind source path does not exist` (WSL) | Docker Desktop의 Ubuntu WSL integration 확인, 레포를 `/mnt/c`가 아닌 Linux 홈에 둠 |
| 로그인 화면에 Google·Kakao 버튼이 없음 | `.env`의 `MARS_PUBLIC_ORIGIN`(https)과 두 제공자 값을 모두 넣고 다시 `up` |
| 로그인 401 반복 | 아이디·비밀번호 확인. 반복 실패 시 잠시 후 재시도(시도 제한) |
| 주문 0건 | 후보·시세·계좌·원칙별 차단 사유 확인. 0건 자체는 장애가 아님 |
| 수집 0건 | 거래일 여부, GDELT 발행 여부, OpenDART 일일 한도 확인 |

DB 볼륨은 지우지 마세요. 종료는 `./capstone down`만 사용하고 `docker volume rm`, `down -v`는 쓰지 않습니다.
