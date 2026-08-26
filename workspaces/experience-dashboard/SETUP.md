# 환경 세팅 설명서 (처음 하는 사람용)

이 문서 하나만 따라 하면 대시보드가 화면에 뜹니다.
명령어는 **위에서부터 순서대로** 복사해서 붙여넣으세요.

두 가지 방법이 있습니다.

| 방법 | 필요한 것 | 걸리는 시간 | 언제 쓰나 |
|---|---|---|---|
| **A. 혼자 보기** | Node.js만 | 5분 | 화면만 확인할 때, 발표 자료 캡처할 때 |
| **B. 서버에 연결** | Docker, Java, Python, Node.js | 40~60분 | 실제 데이터로 동작을 확인할 때 |

처음이라면 **A를 먼저** 해보세요. 화면이 어떻게 생겼는지 보고 나면 B가 훨씬 쉬워집니다.

---

# 방법 A. 혼자 보기 (5분)

서버 없이 대시보드만 켭니다. 합성 데이터가 들어 있어서 모든 화면이 정상 동작합니다.

## A-1. Node.js 설치

이미 깔려 있는지 먼저 확인하세요. 터미널(Windows는 PowerShell)을 열고:

```bash
node -v
```

`v20.x` 이상이 나오면 다음 단계로 넘어가세요.
`command not found`가 나오면 https://nodejs.org 에서 **LTS** 버전을 받아 설치하고, **터미널을 껐다 다시 켜세요.**

> 터미널을 다시 켜지 않으면 설치해도 계속 `command not found`가 납니다. 가장 흔한 실수입니다.

## A-2. 프로젝트 폴더로 이동

압축을 푼 폴더로 들어갑니다.

```bash
cd experience-dashboard
```

폴더 이름이 다르면 실제 이름으로 바꾸세요. 경로를 모르겠으면 탐색기에서 폴더를 터미널 창으로 끌어다 놓으면 경로가 자동으로 입력됩니다.

## A-3. 설정 파일 만들기

```bash
cp .env.example .env
```

Windows PowerShell이면:

```powershell
Copy-Item .env.example .env
```

## A-4. 라이브러리 설치

```bash
npm install
```

2~3분 걸립니다. 노란색 `warn` 메시지는 무시해도 됩니다. 빨간색 `error`가 없으면 성공입니다.

## A-5. 실행

```bash
npm run dev
```

이런 줄이 보이면 성공입니다.

```
  ▲ Next.js 15.1.6
  - Local:  http://localhost:3000
```

브라우저에서 **http://localhost:3000** 을 여세요.

화면 맨 위에 `합성 fixture` 라는 노란 띠가 보이면 정상입니다. 지금 보고 있는 숫자는 가짜라는 뜻입니다.

## A-6. 둘러보기

왼쪽 메뉴를 위에서부터 눌러 보세요.

- **주문 검토** — 판정 ID 칸에 `dec_demo_warn_000001` 이 미리 들어 있습니다. `dec_demo_hold_000001`, `dec_demo_block_000001` 로 바꾸면 보류·차단 화면을 볼 수 있습니다.
- **모델 비교 / 백테스트 리포트** — 실행 ID `demo_s8_offline_0001` 이 미리 들어 있습니다.
- **금융 가이드** — 아래 예시 버튼을 눌러 보세요. "삼성전자 지금 사도 되나요?" 를 누르면 답변을 거부합니다. 그게 정상 동작입니다.

**종료할 때**는 터미널에서 `Ctrl + C` 를 누르세요.

여기까지가 방법 A입니다. 화면 확인과 발표 캡처는 이것으로 충분합니다.

---

# 방법 B. 서버에 연결 (40~60분)

실제 백엔드(Decision Platform)를 켜고 대시보드를 붙입니다.

## 전체 그림

켜야 하는 것이 **네 개**이고, 순서가 중요합니다.

```
1) 데이터베이스 (PostgreSQL + Redis)   ← Docker
2) 분석 서비스 (Python worker)          ← 터미널 1번
3) API 서버 (Spring)                    ← 터미널 2번
4) 대시보드 (Next.js)                   ← 터미널 3번
```

터미널 창을 **세 개** 열어 두고 각각 하나씩 맡깁니다. 하나라도 끄면 그 기능이 멈춥니다.

## B-1. 필요한 프로그램 설치

터미널에서 하나씩 확인하세요.

| 확인 명령 | 필요한 버전 | 없을 때 |
|---|---|---|
| `docker --version` | Docker Engine | https://docker.com 에서 Docker Desktop 설치 |
| `docker compose version` | v2 이상 | Docker Desktop에 포함됨 |
| `java -version` | **25** | https://adoptium.net 에서 Temurin 25 설치 |
| `python3 --version` | **3.13** | https://python.org |
| `uv --version` | 아무 버전 | `pip install uv` |
| `node -v` | 20 이상 | https://nodejs.org |
| `git --version` | 아무 버전 | https://git-scm.com |

**Java 25가 아니면 서버가 아예 시작되지 않습니다.** 여러 버전이 깔려 있다면 `java -version` 결과가 25인지 꼭 확인하세요.

Docker Desktop은 설치 후 **실행해 두어야** 합니다. 아이콘이 초록색이 될 때까지 기다리세요.

## B-2. 백엔드 코드 받기

대시보드 폴더와는 **다른 곳**에 받습니다.

```bash
cd ~
git clone https://github.com/robinhood0107/Capstone-AI-Trading-Coach.git
cd Capstone-AI-Trading-Coach
```

## B-3. 비밀 설정 파일 만들기

```bash
cp .env.example .env
```

`.env` 파일을 메모장이나 VS Code로 열면 빈칸이 여러 개 있습니다. 채워야 하는 것:

- PostgreSQL 역할별 비밀번호
- JWT secret / issuer / audience
- 목적별 HMAC·gRPC secret
- demo 계정 credential bundle

**비밀번호와 secret 만드는 법** (터미널에 붙여넣으면 랜덤 문자열이 나옵니다):

```bash
openssl rand -base64 32
```

Windows PowerShell이면:

```powershell
[Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Max 256 }))
```

**중요한 규칙 세 가지:**

1. secret은 **32바이트 이상**이어야 하고, **각각 서로 다른 값**이어야 합니다. 하나 만들어서 전부 붙여넣으면 서버가 거부합니다.
2. BCrypt 값에는 `$` 기호가 들어갑니다. 반드시 **작은따옴표로 감싸세요.**
   ```
   DEMO_CREDENTIAL_BUNDLE='$2b$10$...'
   ```
   큰따옴표를 쓰면 `$`가 변수로 해석돼서 값이 깨집니다.
3. **API key와 증권사 계좌번호는 절대 넣지 마세요.** 이 단계에서는 필요 없습니다.

`.env` 파일은 **절대 git에 올리면 안 됩니다.** `.gitignore`에 이미 등록돼 있으니 그냥 두면 됩니다.

## B-4. 데이터베이스 켜기

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml up -d postgres redis
```

잘 떴는지 확인:

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml ps
```

`postgres`와 `redis` 둘 다 `healthy` 라고 나와야 합니다. `starting` 이면 30초쯤 기다렸다가 다시 확인하세요.

이어서 권한 설정을 실행합니다. **이 단계를 빠뜨리면 나중에 권한 오류가 납니다.**

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml exec -T postgres \
  bash /docker-entrypoint-initdb.d/02-application-roles.sh
```

## B-5. 분석 서비스 켜기 (터미널 1번)

```bash
cd workspaces/decision-platform/python-services
uv sync --frozen
uv run --frozen python -m app.async_worker.grpc_server
```

`uv sync` 는 처음에 5~10분 걸릴 수 있습니다.

서버가 뜨면 **이 터미널은 그대로 두세요.** 커서가 멈춘 것처럼 보여도 정상입니다.

## B-6. API 서버 켜기 (터미널 2번)

새 터미널 창을 열고:

```bash
cd ~/Capstone-AI-Trading-Coach/workspaces/decision-platform/spring-api
set -a
source ../../../.env
set +a
./gradlew bootRun
```

첫 실행은 5~10분 걸립니다. Gradle이 라이브러리를 받는 중입니다.

`Started ... in N seconds` 가 보이면 성공입니다. 확인:

```bash
curl http://127.0.0.1:8080/api/v1/system/health
```

`{"success":true,...}` 로 시작하는 응답이 나오면 서버가 살아 있는 것입니다.

**이 터미널도 그대로 두세요.**

## B-7. 데모 데이터 준비 (선택이지만 권장)

이걸 하지 않으면 대시보드 화면이 전부 "데이터 없음"으로 뜹니다.

```bash
cd ~/Capstone-AI-Trading-Coach
workspaces/decision-platform/demo/s8/run-demo.sh \
  --prepare --adapter=db --brokerage-mode=INTERNAL_PAPER
```

`INTERNAL_PAPER` 를 정확히 써야 합니다. 다르게 쓰면 안전장치가 실행을 막습니다.

이 명령이 만드는 실행 ID는 `demo_s8_offline_0001` 입니다. 대시보드에서 이 값을 씁니다.

## B-8. 대시보드 연결 (터미널 3번)

새 터미널을 열고 **대시보드 폴더**로 갑니다.

```bash
cd ~/experience-dashboard
cp .env.example .env
```

`.env` 파일을 열어 이렇게 고칩니다.

```
NEXT_PUBLIC_API_MODE=live
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8080
```

그리고 실행:

```bash
npm install
npm run dev
```

## B-9. 브라우저에서 열기

### 반드시 이 주소로 여세요

```
http://localhost:3000
```

**`127.0.0.1:3000` 으로 열면 안 됩니다.** 서버가 `localhost:3000` 만 허용하도록 설정돼 있어서, 주소가 다르면 데이터를 하나도 못 불러옵니다. 같은 곳을 가리키는 주소인데도 그렇습니다. 이게 가장 자주 겪는 문제입니다.

## B-10. 로그인

로그인 화면이 나옵니다.

- 아이디: `demo-user`
- 비밀번호: `.env` 에 credential bundle을 만들 때 정한 비밀번호

> 비밀번호는 저장소 어디에도 없습니다. `.env` 를 만든 사람만 알고 있습니다. 팀에서 받아 쓰세요.

로그인하면 화면이 열립니다. 위쪽 띠에 연결 주소와 자동주문 상태가 보입니다.

**새로고침하면 다시 로그인해야 합니다.** 토큰을 브라우저에 저장하지 않도록 일부러 그렇게 만들었습니다. 불편한 게 아니라 안전장치입니다.

## B-11. 화면별로 넣을 값

| 화면 | 입력 칸 | 넣을 값 |
|---|---|---|
| 주문 검토 | 판정 ID | 주문을 평가하면 나오는 `dec_...` 값 |
| 모델 비교 | 평가 실행 ID | `demo_s8_offline_0001` |
| 모델 비교 | 종목 코드 | `005930` 같은 숫자 6자리 (선택) |
| 백테스트 리포트 | 실행 ID | `demo_s8_offline_0001` |
| 금융 가이드 | 질문 | 자유롭게 |

---

# 끄는 방법

## 매일 작업이 끝났을 때

각 터미널에서 `Ctrl + C` 를 누르고, 마지막에:

```bash
cd ~/Capstone-AI-Trading-Coach
docker compose --env-file .env -f infra/docker-compose.infra.yml stop
```

`stop` 은 데이터를 지우지 않습니다. 다음에 `up -d` 로 다시 켜면 그대로 남아 있습니다.

> `docker compose down -v` 는 쓰지 마세요. 데이터베이스 내용이 전부 지워집니다.

## 다시 켤 때

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml up -d postgres redis
```

그 다음 B-5 → B-6 → B-8 순서로 각 터미널을 다시 켜면 됩니다.

---

# 문제가 생겼을 때

## 1. 화면은 뜨는데 모든 값이 "불러오기 실패"

**가장 흔한 원인:** 주소를 `127.0.0.1:3000` 으로 열었습니다.

브라우저 주소창을 `http://localhost:3000` 으로 고치세요.

확인 방법: 브라우저에서 `F12` → `Console` 탭에 `CORS` 또는 `blocked by CORS policy` 라는 빨간 글씨가 있으면 이 문제가 맞습니다.

## 2. 로그인했는데 자꾸 "로그인이 필요합니다"

새로고침(`F5`)을 눌렀거나 브라우저 탭을 다시 열었을 겁니다. 토큰을 저장하지 않도록 만들었기 때문에 다시 로그인해야 합니다. 정상 동작입니다.

## 3. "해당 자료를 찾을 수 없습니다"

ID가 없거나, 다른 사람의 자료를 조회한 것입니다.

- B-7 데모 데이터 준비를 건너뛰었다면 먼저 실행하세요.
- ID 형식을 확인하세요. 형식이 틀리면 입력 칸이 빨간 테두리로 바뀝니다.

  | 종류 | 형식 |
  |---|---|
  | 판정 | `dec_` 로 시작 |
  | 실행 | `run_` 또는 `demo_` 로 시작 |
  | 답변 | `rag_` 로 시작 |
  | 종목 | 숫자 6자리 |

## 4. Spring 서버가 시작되자마자 꺼짐

메시지를 위로 올려 확인하세요.

| 메시지에 이런 말이 있으면 | 원인 | 해결 |
|---|---|---|
| `ASYNC_ADAPTER` | 설정값이 `db` 나 `kafka` 가 아님 | `.env` 에서 `ASYNC_ADAPTER=db` 로 |
| `secret` / `JWT` | secret이 짧거나 서로 같음 | 32바이트 이상, 전부 다른 값으로 |
| `Port 8080 was already in use` | 다른 프로그램이 8080을 씀 | 아래 5번 참고 |
| `UnsupportedClassVersionError` | Java 버전이 25가 아님 | Java 25 설치 후 터미널 재시작 |

**서버가 안 켜지는 게 정상인 경우도 있습니다.** 설정이 잘못됐을 때 조용히 넘어가지 않고 멈추도록 만들어져 있습니다.

## 5. 8080 포트가 이미 쓰이고 있음

누가 쓰는지 확인:

```bash
lsof -i :8080          # macOS / Linux
netstat -ano | findstr :8080    # Windows
```

그 프로그램을 끄거나, `.env` 에 다른 포트를 지정하세요.

```
SERVER_PORT=8081
```

이 경우 대시보드 `.env` 도 함께 바꿔야 합니다.

```
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8081
```

## 6. 권한 오류 (`permission denied for table ...`)

Docker 컨테이너를 다시 만들면 권한이 초기화됩니다. B-4의 권한 설정을 다시 실행하세요.

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml exec -T postgres \
  bash /docker-entrypoint-initdb.d/02-application-roles.sh
```

## 7. `npm install` 이 실패함

```bash
rm -rf node_modules package-lock.json
npm install
```

그래도 안 되면 Node 버전을 확인하세요 (`node -v` → 20 이상).

## 8. 화면에 "근거 없음" 이라고 빗금 친 칸이 많음

**이건 오류가 아닙니다.**

이 시스템은 값이 없을 때 0으로 채우지 않습니다. 근거가 없으면 없다고 표시합니다. 특히 VaR, CVaR, 시장 국면은 아직 만드는 단계가 운영에 없어서 계속 비어 있는 것이 정상입니다.

마찬가지로 모델이 `ABSTAIN` 으로 뜨는 것도 고장이 아니라, 그 모델이 "근거가 없어 판단하지 않겠다"고 말한 상태입니다.

---

# 자주 하는 질문

**Q. 실제 돈이 나가나요?**
아니요. 모의투자와 내부 페이퍼 모드만 켜져 있고, 실거래 기능은 기본적으로 꺼져 있습니다.

**Q. 백테스트 숫자를 보고서에 써도 되나요?**
지금은 안 됩니다. 화면에 `SYNTHETIC_FAKE_E2E` 또는 `합성 fixture` 라고 표시돼 있으면 그건 만들어낸 값입니다. 표시가 사라진 뒤에 쓰세요.

**Q. 매번 네 개를 다 켜야 하나요?**
화면만 볼 거면 방법 A로 대시보드 하나만 켜면 됩니다.

**Q. `.env` 를 팀원과 공유해도 되나요?**
파일을 그대로 주고받는 것은 피하고, 각자 만들되 값은 안전한 경로(사내 메신저 DM 등)로 전달하세요. **git에는 절대 올리지 마세요.**

**Q. 어디까지 됐는지 모르겠어요.**
아래 순서로 하나씩 확인하면 어디서 막혔는지 알 수 있습니다.

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml ps   # 1. healthy 인가
curl http://127.0.0.1:8080/api/v1/system/health                       # 2. success:true 인가
# 3. 브라우저에서 http://localhost:3000 이 열리는가
# 4. 로그인이 되는가
```
