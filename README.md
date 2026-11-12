# MARS — 투자 원칙 검증형 AI 모의 자동매매

국내 주식, 금 ETF의 시세, 공시, 뉴스, 모델 신호를 근거로 후보를 만들고 사용자가 정한 투자 원칙과 위험 검사를 통과한 주문만 **본인의 한국투자증권(KIS) 모의투자 계좌**에서 실행하는 서비스입니다. 후보 생성, 판단, 주문 접수, 체결, 잔고 대사를 서로 다른 기록으로 남겨 왜 그 주문이 나갔는지(또는 막혔는지)를 끝까지 추적할 수 있습니다.

## 목차

- [프로젝트 소개와 문제](#프로젝트-소개와-문제)
- [주요 기능과 사용 흐름](#주요-기능과-사용-흐름)
- [화면과 시연 영상](#화면과-시연-영상)
- [시스템 구조와 구성 요소](#시스템-구조와-구성-요소)
- [기술 스택](#기술-스택)
- [빠른 시작](#빠른-시작)
- [저장소 구조](#저장소-구조)
- [개발과 검증, 주요 판단과 한계](#개발과-검증-주요-판단과-한계)
- [팀과 기여](#팀과-기여)
- [문서와 라이선스](#문서와-라이선스)

## 프로젝트 소개와 문제

### 국내외 시장 현황 및 문제점

- 개인 투자자가 쓸 수 있는 데이터 원천은 [KIS Developers](https://apiportal.koreainvestment.com/apiservice-summary)의 시세, 주문 API, [OpenDART](https://opendart.fss.or.kr/)의 공시, [GDELT](https://gdeltproject.org/data.html)의 세계 사건 데이터처럼 이미 다양합니다.
- 이 프로젝트는 수집 시각, 결측과 미발행, 종목 연결, 주문 직전 계좌 상태의 불일치를 다룹니다.
- 모델 신호와 후보, 주문 접수, 체결은 서로 다른 사건으로 구분해 기록합니다.

### 필요성과 기대효과

- 초보 투자자가 **왜 후보가 생겼는지 → 어떤 원칙을 통과했는지 → 모의주문과 대사가 어떻게 끝났는지**를 단계별로 확인하며 판단 과정을 학습할 수 있습니다.
- 근거가 부족하면 값을 꾸미지 않고 `HOLD`, `ABSTAIN`, 차단 사유를 남기므로 사용자가 시스템의 한계를 그대로 볼 수 있습니다.
- 실계좌 자금을 투입하지 않고 모의계좌에서 자동운용 흐름을 확인할 수 있습니다.

### 개발 목표

#### 목표 및 세부 내용

| 목표 | 세부 내용 |
|---|---|
| 근거 있는 데이터 | 시세, 공시, 뉴스마다 출처, 기준 시각, 품질 상태를 함께 저장 |
| 비교 가능한 신호 | 규칙, LSTM, 회귀 모델을 같은 계약으로 비교. 모델에는 주문 권한을 주지 않음 |
| 결정적 위험 통제 | 사용자 원칙과 RiskEngine을 거쳐 후보, 수량, 허용 여부를 분리 |
| 본인 계좌 자동운용 | 사용자별 KIS 모의투자 키를 암호화 보관하고 본인 계좌에만 주문 |
| 추적 가능한 기록 | 후보 → 판단 → 주문 접수 → 체결 → 잔고 대사를 별도 기록으로 연결 |
| 계정 | 아이디, 이메일 비밀번호 로그인, 최소 회원가입, 로그인 후 Google, Kakao 직접 연결 |

#### 기존 서비스 대비 차별성

| 구분 | 일반 자동매매, 추천 서비스 | MARS |
|---|---|---|
| 모델 신호 | BUY 신호를 곧 주문으로 표현 | 신호, 원칙 판정, 주문 접수, 체결을 분리해 기록 |
| 근거 부족 시 | 추정값으로 채움 | `HOLD`, `ABSTAIN`과 차단 사유를 그대로 표시 |
| 위험 통제 | 모델 출력에 의존 | 결정적 RiskEngine이 최종 수량과 허용을 결정 |
| 성과 표기 | 백테스트 수익률 중심 | 기준선 대비 판정(`BELOW_BASELINE` 등)을 함께 공개 |
| AI 설명 | 주문을 결정하기도 함 | 출처를 단 설명만 제공하고 주문 권한은 없음 |

#### 사회적 가치 도입 계획

- **금융 교육**: 모의투자와 출처, 위험 근거를 통해 투자 판단 과정을 안전하게 학습합니다.
- **투명성**: 기준에 못 미친 모델도 `BELOW_BASELINE`으로 공개해 과장된 성과 표현을 막습니다.
- **개인정보 보호**: 비밀번호는 해시로만, 증권사 키는 전용 키로 암호화해 저장하고 운영 데이터는 사용자 본인 서버에만 둡니다.

## 주요 기능과 사용 흐름

```mermaid
flowchart TD
  S1[수집·품질 검사<br/>일봉·공시·세계 뉴스] --> S2[기준 시각 feature 생성]
  S2 --> S3[규칙 · LSTM · 회귀 신호]
  S3 --> S4[후보 생성]
  S4 --> S5{사용자 원칙<br/>+ RiskEngine}
  S5 -->|통과| S6[본인 KIS 모의주문 접수]
  S5 -->|차단| S9[차단 사유 기록]
  S6 --> S7[체결·잔고 대사]
  S7 --> S8[화면·기록]
  S9 --> S8
```

- 세계 뉴스는 화면에 보여 주는 **참고 자료**이며 종목 판단, 주문의 직접 입력으로 쓰지 않습니다.
- GDELT 파일 미발행, OpenDART 일일 호출 한도 초과는 우회하지 않고 별도 상태로 기록합니다.
- 자동운용은 장 시작 전 예약된 일정에 따라 돌며 무장(ARM), 해제(DISARM), Kill Switch로 사용자가 언제든 멈출 수 있습니다.

### 기능 설명 및 주요 기능 명세서

| 기능 | 입력 | 출력 | 설명 |
|---|---|---|---|
| FULL 로그인 | 아이디 또는 이메일, 비밀번호 | Bearer 세션 | 실패 사유를 구분하지 않는 동일한 401, 시도 횟수 제한. 공개 DEMO는 자격 증명을 요구하지 않음 |
| 회원가입 | 이메일, 비밀번호(15~64자) | 새 USER 계정 | 정규화한 이메일과 BCrypt 해시만 저장 |
| 로그인 방법 연결 | 로그인 상태에서 Google, Kakao 인증 | 같은 계정에 제공자 연결 | 이메일이 같아도 자동 병합하지 않음. 마지막 로그인 수단은 해제 불가 |
| 투자 원칙 | 프리셋, 매수, 매도 조건, 위험 한도 | 버전이 있는 원칙 | 자동운용과 주문 검토가 이 원칙으로 판정 |
| 자동운용 | 원칙, 자본 정책, 후보, 시세 | 실행 기록, 허용 주문 또는 차단 사유 | 예약 일정, 무장 상태, 대사 결과를 함께 표시 |
| KIS 모의 주문, 대사 | 허용된 주문 의도 | 주문 접수, 체결, 잔고 대사 | 사용자별 계좌에만 주문. 호출 유량(모의 1건/초) 준수 |
| 수집기 | 일봉, 공시, GDELT 파일 | 품질 상태가 붙은 데이터 | 시각, 결측, 미발행을 구분해 저장 |
| 예측, 모델 비교 | 완료된 feature | 규칙, LSTM, 회귀 신호 비교 | 모델은 주문 권한 없음 |
| 금융 Agent | 질문 | 출처를 단 답변 또는 근거 부족 응답 | 공시, 뉴스, 시세 근거 검색(RAG) |
| 주문 검토 | 후보, 원칙 | 통과, 차단 판정과 이유 | 사람이 주문 전에 판정 근거 확인 |

#### FULL 계좌, 자동운용 준비 상태

- KIS 정보를 저장하면 암호화하고 같은 계좌를 다시 저장해도 전체 계좌번호의 KEK 기반 지문으로 내부 계좌 ID를 이어 갑니다. 끝 4자리만으로 다른 계좌를 합치지 않습니다.
- 연결 확인은 주문하지 않고 완전한 KIS 잔고를 한 번 조회합니다. 이 읽기 증명이 현재 계좌, 자격 증명 버전에 묶이면 FULL 자동운용을 시작할 수 있습니다. 1주 모의주문 시험은 선택 사항이며 이 확인만으로 CERTIFIED 상태를 만들지 않습니다.
- 열린 포지션 수는 현재 KIS 계좌에 연결된 자동운용 포지션만 셉니다. 내부 모의 계좌 이력(오프라인 재생과 Team A 수용 테스트)은 별도 계정으로 연결해 원래 OPEN/CLOSED 상태와 손익을 보존하고 현재 KIS 보유와 분리해 화면에 표시합니다. 이 이력은 현재 계좌의 보유 수, 손익 재투자, 자동 매도에 합산하지 않습니다. 다른 계좌의 미대사 KIS 주문, 실행은 확인될 때까지 시작을 막고 개수를 보여 줍니다.
- 자동운용 정지는 제어 상태와 미래 예약을 함께 해제하고 기록을 남깁니다. 정책을 정지 중 저장하면 다음 시작이 저장된 새 버전을 사용합니다. 시작은 예약만 만들며 런타임이 꺼져 있으면 실행, 주문은 일어나지 않습니다.
- 각 버튼의 API, DB 효과와 내부 모의 계좌, 과거 paper 기록 귀속은 [FULL 상태 전이와 DB 검증표](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/blob/main/docs/FULL_상태전이_DB_검증표.md)에 정리했습니다.

#### Vertex 키와 Agent 사용량

- 사용자는 본인 Vertex 키를 등록, 삭제할 수 있습니다. 키는 암호화해 저장하고 다시 표시하지 않습니다. 자동운용 AI 검토와 금융 Agent는 본인 키를 먼저 쓰고 관리자가 허용한 경우에만 공용 키를 대체 경로로 씁니다.
- 관리자는 공용 키의 설정 상태와 사용자별 사용량만 확인할 수 있습니다. 사용자 키의 비밀값은 조회할 수 없으며 사용량은 개인 키, 공용 키로 나눠 기록합니다.

### 화면 사용 흐름

1. 로그인 후 **내 원칙**에서 프리셋과 위험 한도를 정합니다.
2. 모델 비교와 백테스트에서 신호와 결과를 살펴보고 **주문 검토**에서 원칙과 RiskEngine의 판정 근거를 확인합니다.
3. **자동운용**에서 준비 상태와 실행 이력, 차단 사유를 살펴봅니다. 주문 접수와 체결은 서로 다른 상태입니다.
4. **금융 Agent**에서 질문하고 출처를 확인한 뒤 필요한 내용을 학습일지에 남깁니다.

## 화면과 시연 영상

원본 README에 실린 모의 시연 화면입니다. 화면에 표시된 잔고와 백테스트 수치는 실제 투자 수익이나 검증된 성과의 근거가 아닙니다.

| 자동운용 | 주문 검토 |
|---|---|
| <img src="docs/readme-media/automation.png" width="420" alt="자동운용 화면"> | <img src="docs/readme-media/order-review.png" width="420" alt="주문 검토 화면"> |
| **백테스트** | **금융 Agent** |
| <img src="docs/readme-media/backtest.png" width="420" alt="백테스트 화면"> | <img src="docs/readme-media/agent-answers.png" width="420" alt="금융 Agent 답변 화면"> |


### 시연 영상

[![MARS 시연 영상 썸네일](https://img.youtube.com/vi/CU7284u1rk0/0.jpg)](https://www.youtube.com/watch?v=CU7284u1rk0)

[YouTube에서 시연 영상 보기](https://www.youtube.com/watch?v=CU7284u1rk0)

다음 영상은 제품 탐색을 기록한 로컬 WebM 파일입니다. 링크에서 파일을 열거나 내려받아 재생할 수 있습니다. 최초 실행 영상은 API 미기동 상태의 부분 프런트 탐색이며 어느 영상도 촬영만으로 전체 기능 동작을 검증하지 않습니다.

- demo desktop 탐색 영상: [navigation-5cde37df75297008bb7a.webm](docs/readme-media/navigation-5cde37df75297008bb7a.webm)
- demo desktop 탐색 영상: [navigation-3b26eccda4e09af7b4b1.webm](docs/readme-media/navigation-3b26eccda4e09af7b4b1.webm)
- demo mobile 탐색 영상: [navigation-4c7b53fbe4d9063fddc9.webm](docs/readme-media/navigation-4c7b53fbe4d9063fddc9.webm)
- demo mobile 탐색 영상: [navigation-25b132d5a9fc3b27660a.webm](docs/readme-media/navigation-25b132d5a9fc3b27660a.webm)
- experience-dashboard desktop 탐색 영상: [navigation-ad17ae9fcd2bf11bd830.webm](docs/readme-media/navigation-ad17ae9fcd2bf11bd830.webm)
- experience-dashboard mobile 탐색 영상: [navigation-0623ef5d1f3fc316e76b.webm](docs/readme-media/navigation-0623ef5d1f3fc316e76b.webm)
- initial desktop 탐색 영상: [navigation-c977158a33572c2b4a90.webm](docs/readme-media/navigation-c977158a33572c2b4a90.webm)
- initial mobile 탐색 영상: [navigation-86e613c9e66c70eebf51.webm](docs/readme-media/navigation-86e613c9e66c70eebf51.webm)

## 시스템 구조와 구성 요소

### 시스템 구성도

```mermaid
flowchart LR
  U[사용자 브라우저] -->|HTTPS| T[TLS 리버스 프록시]
  T --> W[Experience Dashboard<br/>Next.js]
  T --> A[Decision Platform API<br/>Spring Boot]
  W --> A
  A --> AA[Actor Authority<br/>내부 권한 발급]
  A <--> P[(PostgreSQL + pgvector)]
  A <--> R[(Redis)]
  A <--> PY[Python 서비스<br/>gRPC: 공시·RAG·예측·주문]
  PY --> K[KIS 모의투자 API]
  PY --> V[Vertex AI · Voyage<br/>금융 Agent·검색]
  C[수집기<br/>일봉·세계 뉴스·공시] --> P
  RT[자동운용 런타임] --> A
  A -. 선택 .-> O[Google · Kakao OAuth]
```

| 구성 요소 | 소스 경로 | 포트 | 주요 역할 |
|---|---|---|---|
| Experience Dashboard | `workspaces/experience-dashboard` | 127.0.0.1:3000 | 로그인, 원칙, 자동운용, 주문 검토, 금융 Agent 화면 |
| Decision Platform API | `workspaces/decision-platform/spring-api` | 127.0.0.1:18080 | 인증, 원칙, 위험 판정, 자동운용, 주문, 대사 API |
| Python 서비스 | `workspaces/decision-platform/python-services` | 내부 gRPC | 수집기, 공시, RAG, 예측 추론, KIS 주문 어댑터 |
| Return Engine | `workspaces/return-engine` | 내부 | LSTM, 규칙 기준선, 백테스트 산출물 |
| TLS 프록시 | `deploy/p1/docker/tls-proxy.nginx.conf` | 443 / 80 (서버) | 외부 HTTPS 단일 입구 |
| PostgreSQL, Redis | `deploy/p1/docker` | 내부 | 운영 데이터, 세션, 호출 제한, 작업 상태 |

**계정 보안 규칙**

1. 비밀번호는 15~64자, 최대 72 UTF-8 바이트이며 BCrypt(강도 12)로만 저장합니다.
2. 비밀번호, 제공자 식별자, 토큰은 응답, 로그, 감사 기록, URL에 남기지 않습니다.
3. 없는 계정과 틀린 비밀번호는 같은 401을 돌려주고 로그인, 가입은 공유 저장소 기반 시도 제한을 거칩니다.
4. 비밀번호 표는 행 수준 보안(RLS)을 강제하고 인증 전용 DB 역할의 보안 함수로만 읽고 씁니다.
5. 제공자 연결은 로그인한 본인만, 같은 출처(Origin)에서, 5분짜리 HttpOnly, Secure 세션으로만 시작할 수 있습니다.
6. 모든 요청은 DB의 활성 상태, 역할, 보안 버전을 다시 확인합니다. 관리자 계정에는 일반 제공자를 연결할 수 없습니다.
7. JWT는 브라우저 메모리(탭 세션)에만 두고 OAuth 콜백은 토큰을 URL에 싣지 않고 1회용 교환으로 넘깁니다.

#### API와 저장 경계

이전 판본은 인증, 투자 원칙, 주문 평가, RAG 질문과 이력, 계좌 잔고, 주문 대사, 체결 조회, 자동운용 상태와 학습일지를 별도 API 영역으로 나눴습니다. 현행 API의 경로와 요청 형식은 실행 중 Swagger와 API 명세에서 확인합니다. PostgreSQL에는 원칙 버전과 실행, 주문, 체결, 계좌 대사 등 서로 다른 사건을 저장하고 Redis는 세션과 호출 제한, 작업 상태에 사용합니다. 내부 모의 원장과 현재 KIS 계좌 기록을 합치지 않는 것이 계좌 귀속의 기준입니다.

## 기술 스택

| 구분 | 기술 |
|---|---|
| 프론트엔드 | TypeScript, React, Next.js, Tailwind CSS |
| 백엔드 | Java 25, Kotlin, Spring Boot, Spring Security(OAuth2 Client), Flyway, gRPC |
| 데이터, 분석 | Python 3.12, uv, pandas, NumPy, PyTorch(LSTM), LangGraph |
| 저장소 | PostgreSQL 16 + pgvector, Redis |
| 외부 API | KIS Developers(모의투자), OpenDART, GDELT, Google Vertex AI, Voyage AI, Google, Kakao 로그인 |
| 배포, 운영 | Docker Compose, nginx(TLS), GitHub Actions, Docker Hub |

## 빠른 시작

아래 실행 명령은 이번에 재검증하지 않았습니다.

### 설치절차 및 실행 방법

#### 준비 사항

| 항목 | 내용 |
|---|---|
| OS | Linux `amd64` 또는 Windows WSL2(Ubuntu). 레포와 데이터는 Linux 홈 아래에 둠 |
| 필수 도구 | Docker Engine(또는 WSL 통합이 켜진 Docker Desktop), Docker Compose v2, Git, Python 3, OpenSSL |
| 외부 키 | KIS 모의투자 App Key, Secret, 계좌번호, (선택) OpenDART, Vertex AI, Voyage, Google, Kakao OAuth |

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
| `MARS_PUBLIC_ORIGIN`, `GOOGLE_OIDC_CLIENT_ID`, `GOOGLE_OIDC_CLIENT_SECRET`, `KAKAO_OAUTH_CLIENT_ID`, `KAKAO_OAUTH_CLIENT_SECRET` | Google, Kakao 로그인(선택). 공개 HTTPS 주소와 두 제공자가 모두 있을 때만 켜짐 |

Google, Kakao 콘솔에는 `<MARS_PUBLIC_ORIGIN>/api/v1/auth/oidc/callback/google`, `<MARS_PUBLIC_ORIGIN>/api/v1/auth/oidc/callback/kakao`를 리디렉션 URI로 등록합니다.

#### 실행

```bash
./capstone up --mock
```

- 첫 실행 때 비밀값을 생성하고 이미지를 빌드한 뒤 DB 마이그레이션까지 적용합니다. `--mock`을 빼면 자동매매가 꺼진 채로 뜹니다.
- 출력에 `CAPSTONE_UP=PASS`와 `CAPSTONE_AUTOMATION_RECONCILED=ARMED`가 모두 보이면 기동이 끝난 것입니다.
- FULL 내장 계정은 배포 운영자가 host-only secret 절차로 발급, 회전합니다. 실제 아이디, 비밀번호나 secret 경로를 공개 문서에 적지 않습니다. 공개 DEMO는 원클릭 방문자 세션을 쓰며 FULL 계정에 연결하지 않습니다.
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

데이터와 비밀값은 레지스트리를 거치지 않고 서버로 직접 복사합니다. 현재 PC에서 출력한 커밋 SHA를 기록한 뒤 서버 명령의 `기록한_SHA`를 그 값으로 바꿉니다.

```bash
# 1) 현재 PC: 커밋 SHA를 기록하고 스택을 내린 뒤 DB 볼륨과 state를 묶는다
git rev-parse HEAD
./capstone down
docker run --rm -v capstone-p1_p1-postgres-current:/from:ro -v "$PWD":/to alpine \
  tar czf /to/p1-postgres.tgz -C /from .
tar czf p1-state.tgz -C deploy/p1 .state-app
chmod 600 p1-postgres.tgz p1-state.tgz
scp p1-postgres.tgz p1-state.tgz .env <user>@<server>:~/

# 2) 서버: 같은 커밋을 받고 데이터를 복원한다
git clone https://github.com/robinhood0107/Capstone-AI-Trading-Coach.git && cd Capstone-AI-Trading-Coach
git checkout --detach "기록한_SHA"
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

서버에서는 `compose.server.yml`이 TLS 프록시를 443/80으로 공개하고 앱 포트는 서버 안(127.0.0.1)에만 둡니다.

#### 개발 환경에서 API 직접 실행

인증용 DB 역할을 Flyway보다 먼저 준비한 뒤 Spring API를 실행합니다. KIS 모의 체결 관측은 `decision_fill_writer` 역할로 적재하며 관련 마이그레이션 경계는 `V6/V9/V14`입니다.

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml run --rm role-bootstrap
cd workspaces/decision-platform/spring-api && ./gradlew bootRun
```

#### 공개 체험판 이미지 (선택)

로그인 없는 금융 Agent 체험판 DEMO와 계좌 기능 FULL은 [Docker Hub](https://hub.docker.com/r/pjjpjj111/mars-full)와 [GitHub Release](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/releases/latest)의 digest 고정 Compose로도 실행할 수 있습니다. 이미지에는 코드만 들어 있고 사용자 데이터는 포함되지 않습니다. NAS에서 Portainer 스택 하나로 운영하는 방법과 기존 배포를 옮기는 절차는 [deploy/p1/portainer/README.md](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/blob/main/deploy/p1/portainer/README.md)에 있습니다.

### 오류 발생 시 해결 방법

| 증상 | 해결 |
|---|---|
| `up`이 `CAPSTONE_UP=PASS` 없이 끝남 | 마지막 `P1_ERROR=` 값을 확인. 종료코드 0이어도 중간 실패일 수 있음 |
| `P1_ERROR=root_env_boundary` | `.env` 권한을 `chmod 600 .env`로 맞춤 |
| `P1_ERROR=secret_inventory` | `deploy/p1/.state-app/secrets`에 예상 밖 파일이 있음. 해당 파일을 secrets 밖으로 옮김 |
| `bind source path does not exist` (WSL) | Docker Desktop의 Ubuntu WSL integration 확인, 레포를 `/mnt/c`가 아닌 Linux 홈에 둠 |
| 로그인 화면에 Google, Kakao 버튼이 없음 | `.env`의 `MARS_PUBLIC_ORIGIN`(https)과 두 제공자 값을 모두 넣고 다시 `up` |
| 로그인 401 반복 | 아이디, 비밀번호 확인. 반복 실패 시 잠시 후 재시도(시도 제한) |
| 주문 0건 | 후보, 시세, 계좌, 원칙, RiskEngine 단계별 차단 사유 확인. 0건 자체는 장애가 아님 |
| 수집 0건 | 거래일 여부, GDELT 발행 여부, OpenDART 일일 한도 확인 |

DB 볼륨은 지우지 마세요. 종료는 `./capstone down`만 사용하고 `docker volume rm`, `down -v`는 쓰지 않습니다.

## 저장소 구조

```text
Capstone-AI-Trading-Coach/
├── capstone                          # 로컬·서버 실행 진입점 (./capstone up --mock)
├── contracts/                        # 영역 간 API·데이터 계약과 변경 기록
│   ├── openapi/                      # OpenAPI 명세
│   └── changes/                      # 계약 변경 사유와 영향 범위
├── deploy/p1/                        # Compose, 이미지, 배포 스크립트
│   ├── compose.yml                   # 전체 스택
│   ├── compose.server.yml            # 개인 서버용 오버레이 (TLS, 재시작 정책)
│   ├── compose.release.yml           # 소스 없이 이미지로 실행하는 생성본
│   └── full-appctl, p1ctl            # 초기화·기동·점검
├── docs/                             # 프로젝트·API 명세, 화면 캡처
├── workspaces/
│   ├── decision-platform/
│   │   ├── spring-api/               # 인증, 원칙, RiskEngine, 자동운용, 주문 API
│   │   ├── python-services/          # 수집기, 공시, RAG, 예측, KIS 어댑터
│   │   └── research/                 # 규칙·결합 평가, 수익성 검증
│   ├── return-engine/                # LSTM·규칙 기준선·백테스트
│   └── experience-dashboard/         # Next.js 사용자 화면
└── infra/                            # 개발용 인프라 Compose, systemd 단위
```

## 개발과 검증, 주요 판단과 한계

### 산업체 멘토링 의견 및 반영 사항

산업체 멘토가 중간보고서를 서면으로 검토했습니다(2026-08-05). 원문은 [자문의견서](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/blob/main/docs/04.자문의견서/자문의견서.pdf)에 있습니다.

| 멘토 의견 | 반영 사항 |
|---|---|
| 핵심 가치는 높은 수익률이 아니라, 자신의 원칙을 지키며 판단 근거와 위험을 확인하는 데 있다 | AI 권한을 후보 재순위와 매수 거부 두 가지로만 제한하고, 수량 결정이나 새 종목 선택 권한은 두지 않음. 소개 화면과 발표도 수익률 대신 "원칙이 모델을 이긴 순간"을 중심으로 구성 |
| 기술 범위가 넓으므로 원칙 설정 → 모델 신호 → RiskEngine 판단 → 주문 검토 → 결과 확인의 대표 시나리오를 먼저 완성 | 이 흐름을 내 원칙, 자동운용, 주문 검토, 학습일지 화면으로 끝까지 연결하고, 실제 KIS 모의계좌로 자동운용이 도는 상태까지 완성 |
| 설계 설명과 실제 구현 결과를 구분하고, 화면, 판정 사례, 근거, 백테스트를 순서대로 연결 | 보고서 캡처 화면에서 보고서의 그림, 표 번호와 실제 화면을 1:1로 연결. 판정 레일(ALLOW, WARN, HOLD, BLOCK), 넘어선 원칙, 백테스트를 한 화면에서 확인 |
| 입력 데이터 시점, 모델 버전, 원칙 설정값, RiskEngine 판단 사유를 함께 기록 | 자동운용 실행마다 원칙 버전, 단계별 판단 사유, AI 검토 결과를 저장하고 "판단 근거 보기"로 다시 확인. 원칙은 저장할 때마다 새 버전으로 남고 이전 버전은 지우지 않음 |
| Baseline, Guide, Strict 비교와 함께 원칙 위반, 차단 횟수를 보고, 정상 주문을 과도하게 막지 않았는지도 확인 | 백테스트 리포트에 MDD, CVaR 개선율과 함께 차단한 원칙 위반 건수, 안전장치를 켰을 때의 CAGR 차이를 나란히 표시해 개선과 대가를 함께 보도록 함 |

### 연구 결과와 해석

LSTM walk-forward 평가에서는 0 예측 기준보다 RMSE가 높아 `BELOW_BASELINE`으로 기록했습니다. 이는 당시 평가 조건에서 측정한 모델 성능이며 투자 수익을 보장하지 않습니다. 규칙과 모델의 후보 결합 연구도 후보 발생 빈도를 다룬 것으로 수익성 개선을 입증하지 않습니다. 연구와 검증은 팀이 함께 진행했습니다.

### 한계 및 향후 과제

| 항목 | 현재 상태 | 향후 과제 |
|---|---|---|
| 개인 서버 운영 | 서버용 오버레이와 데이터 이전 절차 준비 | 도메인, TLS 인증서를 갖춘 서버로 이전하고 상시 운영 |
| Google, Kakao 로그인 | 연결, 해제 기능과 보안 규칙 구현 | 공개 HTTPS 주소에서 제공자 콘솔 등록 후 실제 동의, 콜백 검증 |
| 계정 복구 | 이메일 인증, 비밀번호 재설정 메일 미제공 | 메일 발송 경로를 갖춘 뒤 인증, 재설정 추가 |
| 수용 인원 | 다중 사용자 격리 구조 구현 | 동시 사용자 10, 50, 100명 부하 측정과 서버 사양 산정 |
| 수익성 | LSTM은 기준선 미달, 규칙, RiskEngine 중심 운용 | 장기 모의운용 기록으로 전략별 성과 재평가 |

### 이전 판본에서 이어진 설계와 주요 결정

초기 판본부터 원칙 설정, 모델 비교, 위험 판정, 주문과 대사, 학습일지를 한 흐름으로 연결했습니다. 과거 설명에는 Mock과 Live를 같은 어댑터에서 구분하는 설계가 있었지만 이 문서의 현행 사용 경로는 KIS 모의투자입니다. 두 판본의 계좌 인증과 시작 명령은 서로 호환된다고 가정하지 않습니다.

AI는 후보를 재정렬하거나 매수를 거부할 수 있지만 새 종목과 주문 수량을 직접 정하지 않습니다. 사용자가 정한 원칙과 결정적 RiskEngine을 두어 모델 출력과 주문 권한을 분리했습니다. 부분 체결과 미확정 주문을 대사하고 계좌 상태가 맞지 않으면 중지하는 것도 이 경계의 일부입니다.

예전 문서에서는 투자 원칙을 적용하지 않은 Baseline과 Guide, Strict를 비교했습니다. 전략의 위험 지표와 수익 변화는 같은 구간과 조건에서 함께 봐야 하며 짧은 표본을 실전 성과로 일반화하지 않습니다.

개발 중 인용 생성부와 소비부의 길이 규격이 달라 소비 오류가 발생했습니다. 선택한 경계 입력 네 개를 전후 대조해 오류가 난 입력이 2개에서 0개로 바뀐 것을 확인했습니다. 이 결과는 해당 입력의 기술 검증이며 서비스 전체 오류율을 뜻하지 않습니다.

## 팀과 기여

3인 졸업과제입니다. 저는 PM과 백엔드 Decision Platform을 맡아 원칙과 위험 판정, 자동운용과 모의주문 연동을 다뤘습니다. 모델과 백테스트는 모델 담당 팀원이, 화면과 사용자 경험은 프론트엔드 담당 팀원이 맡았습니다. 측정과 시험, 검토는 팀이 함께했습니다. AI 도구를 코드와 명세 검토에 활용했으며 사람의 요청과 판단, 도구의 제안과 구현을 구분합니다.

## 문서와 라이선스

### 프로젝트 소개 자료

| 자료 | 위치 |
|---|---|
| 프로젝트 명세서 | [docs/최종_프로젝트_명세서.md](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/blob/main/docs/최종_프로젝트_명세서.md) |
| API 명세서 | [docs/API_명세서.md](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/blob/main/docs/API_명세서.md) |
| 금융공학, 자동매매 로직 설명서 | [docs/금융공학_공식_및_자동매매_로직_설명서.md](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/blob/main/docs/금융공학_공식_및_자동매매_로직_설명서.md) |
| 2026 최종보고서 | [DOCX](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/blob/main/docs/01.보고서/03.최종보고서.docx), [PDF](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/blob/main/docs/01.보고서/03.최종보고서.pdf) |
| 최종 발표자료 | [docs/03.발표자료/발표자료.pptx](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/blob/main/docs/03.발표자료/발표자료.pptx) |
| 산업체 자문의견서 | [docs/04.자문의견서/자문의견서.pdf](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/blob/main/docs/04.자문의견서/자문의견서.pdf) |



저장소 라이선스는 [GNU AGPL-3.0](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/blob/main/LICENSE)입니다. 외부 모델과 서비스의 이용 조건은 별도로 확인해야 합니다.

### 참고 문헌 및 출처

1. 한국투자증권, [KIS Developers API](https://apiportal.koreainvestment.com/apiservice-summary)
2. 금융감독원, [OpenDART](https://opendart.fss.or.kr/)
3. The GDELT Project, [GDELT Data](https://gdeltproject.org/data.html)
4. Google, [OAuth 2.0 for Web Server Applications](https://developers.google.com/identity/protocols/oauth2/web-server), [Sign in with Google 브랜딩 가이드](https://developers.google.com/identity/branding-guidelines)
5. Kakao, [카카오 로그인 REST API](https://developers.kakao.com/docs/ko/kakaologin/rest-api), [디자인 가이드](https://developers.kakao.com/docs/ko/kakaologin/design-guide)
6. Newey, W. K., & West, K. D. (1987). [A Simple, Positive Semi-Definite, Heteroskedasticity and Autocorrelation Consistent Covariance Matrix](https://doi.org/10.2307/1913610). *Econometrica*, 55(3), 703–708.
7. OWASP, [Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)


검토에서 확인한 계좌 식별자는 공개용 이미지와 탐색 영상에서 불투명하게 가렸습니다. 원본은 별도로 보존했으며, 그 밖의 화면 내용은 바꾸지 않았습니다.

<!-- MARS_CAPTURE_GALLERY_BEGIN -->
<details>
<summary>실행 화면 전체 갤러리</summary>

화면은 각 실행에서 촬영한 UI 기록입니다. 최초 실행은 API 미기동으로 제품 흐름 검증에 실패했습니다. 다른 실행도 화면 촬영만으로 기능 검증을 뜻하지 않습니다. 화면 수치는 모의 시연값이며 실제 수익 성과의 근거가 아닙니다.

**demo / desktop**

![demo / desktop](docs/readme-media/capture-c68f9a51a756715999dd.png)

**demo / desktop / /principles / page**

![demo / desktop / /principles / page](docs/readme-media/capture-13e06e9fef231d4b9419.png)

**demo / desktop**

![demo / desktop](docs/readme-media/capture-c1c06458598fc1cbad03.png)

**demo / desktop / /principles / ctl_밝게**

![demo / desktop / /principles / ctl_밝게](docs/readme-media/capture-0d0b7f10198bedcd8592.png)

**demo / desktop / /principles / ctl_어둡게**

![demo / desktop / /principles / ctl_어둡게](docs/readme-media/capture-334f6f438846ce4202aa.png)

**demo / desktop**

![demo / desktop](docs/readme-media/capture-f31568b03d2b2036bae1.png)

**demo / desktop**

![demo / desktop](docs/readme-media/capture-aae2917487cf0f8215f4.png)

**demo / desktop / /strategy / page**

![demo / desktop / /strategy / page](docs/readme-media/capture-9785f709c716ffa72cb9.png)

**demo / desktop / /strategy / ctl_밝게**

![demo / desktop / /strategy / ctl_밝게](docs/readme-media/capture-f9783c1e6d723615cd69.png)

**demo / desktop / /strategy / ctl_어둡게**

![demo / desktop / /strategy / ctl_어둡게](docs/readme-media/capture-f3a54a0d800c00283238.png)

**demo / desktop / /strategy / ctl_백테스트리포트**

![demo / desktop / /strategy / ctl_백테스트리포트](docs/readme-media/capture-14042ab6e3f3c29b0b52.png)

**demo / desktop / /strategy / ctl_CAGR**

![demo / desktop / /strategy / ctl_CAGR](docs/readme-media/capture-9e84f738261214824b55.png)

**demo / desktop / /strategy / ctl_MDD**

![demo / desktop / /strategy / ctl_MDD](docs/readme-media/capture-554f9ab570ef7c283cef.png)

**demo / desktop / /strategy / ctl_Sharpe**

![demo / desktop / /strategy / ctl_Sharpe](docs/readme-media/capture-a7d32e7c7a3736416acd.png)

**demo / desktop / /backtest / ctl_밝게**

![demo / desktop / /backtest / ctl_밝게](docs/readme-media/capture-6ab9ce8bb30764f3117b.png)

**demo / desktop / /backtest / ctl_어둡게**

![demo / desktop / /backtest / ctl_어둡게](docs/readme-media/capture-dcc754c86434234cadf4.png)

**demo / desktop / /backtest / ctl_Sharpe**

![demo / desktop / /backtest / ctl_Sharpe](docs/readme-media/capture-9aa9cfd5f831302c62d9.png)

**demo / desktop / /model-evaluation / ctl_CAGR**

![demo / desktop / /model-evaluation / ctl_CAGR](docs/readme-media/capture-959ac9d8fe6213ce83e9.png)

**demo / desktop / /rag / page**

![demo / desktop / /rag / page](docs/readme-media/capture-d64228d1455349b4fcfd.png)

**demo / desktop / /rag / ctl_밝게**

![demo / desktop / /rag / ctl_밝게](docs/readme-media/capture-7ebabc838ae9c0354978.png)

**demo / desktop / /rag / ctl_어둡게**

![demo / desktop / /rag / ctl_어둡게](docs/readme-media/capture-3544b08095f30d983bde.png)

**demo / desktop / /rag / ctl_동의**

![demo / desktop / /rag / ctl_동의](docs/readme-media/capture-7773da7ebad7c19134ef.png)

**demo / desktop / /rag / ctl_짧게**

![demo / desktop / /rag / ctl_짧게](docs/readme-media/capture-76ed693c5d5fa0051f78.png)

**demo / desktop / /rag / ctl_자세히**

![demo / desktop / /rag / ctl_자세히](docs/readme-media/capture-5be01a0ba88a3ccc58b4.png)

**demo / desktop / /rag / ctl_연구근거1개더보기**

![demo / desktop / /rag / ctl_연구근거1개더보기](docs/readme-media/capture-14d8adedac0253b0e40d.png)

**demo / desktop / /automation / page**

![demo / desktop / /automation / page](docs/readme-media/capture-face7043cf106e530de7.png)

**demo / desktop / /automation / ctl_밝게**

![demo / desktop / /automation / ctl_밝게](docs/readme-media/capture-bf00b98d081d3a57b35c.png)

**demo / desktop / /automation / ctl_어둡게**

![demo / desktop / /automation / ctl_어둡게](docs/readme-media/capture-b3e1171bf06e25e18b17.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-d4f261b2788e3d50f744.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-80cd36335a471f9d3068.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-b804dd39a96f37fd04bf.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-8eb54abe926ede10b68a.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-5637d1304a00674fa1d0.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-435d9527e330e1acb4bc.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-6bd70484fe329cc2f10b.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-a42fb1954134c08b3003.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-ada3bf26a556d8ba2fb8.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-8b3cf071c64a00e08c88.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-033507d89112024840e4.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-a363f6259b3665d19de7.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-8ac906cb5fe8a2146a31.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-98ec8bac6eb82b22b44a.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-012d972adead9ce1447c.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-c10b185383f12c049973.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-3f09425445794b346aa2.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-33f3d4dc4b602bc1ab57.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-081f4359e799178a5a4b.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-efd870c99a348aa46c26.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-96d1e2b3ad49fb3ca438.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-bf22b72f3877a276736e.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-238ed2762f2f321f3654.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-9da386de036b6c1569fd.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-d02704678c929cbd943b.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-a9fd2763bc7e1f947f83.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-c78e36ece6633832c529.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-c3e9f03b4aeec4437cc6.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-444401befc2260ac45ba.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-141e9935517914721070.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-8f754ed4f61e918bad3d.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-63848cf58c1dcf430234.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-35b0f09d7dfb59123447.png)

**demo / desktop / /automation / ctl_판단근거보기**

![demo / desktop / /automation / ctl_판단근거보기](docs/readme-media/capture-e5703ff717192c67f725.png)

**demo / desktop / /automation / ctl_지정가**

![demo / desktop / /automation / ctl_지정가](docs/readme-media/capture-0825040de491ac1d4dc6.png)

**demo / desktop / /journal / page**

![demo / desktop / /journal / page](docs/readme-media/capture-76fedc51e2969c13fc10.png)

**demo / desktop / /journal / ctl_밝게**

![demo / desktop / /journal / ctl_밝게](docs/readme-media/capture-1ed11faed4541ecd53f8.png)

**demo / desktop / /journal / ctl_어둡게**

![demo / desktop / /journal / ctl_어둡게](docs/readme-media/capture-eb4f64f2246124d0956a.png)

**demo / desktop / /report / page**

![demo / desktop / /report / page](docs/readme-media/capture-032e9e87f48e3245aede.png)

**demo / desktop / /report / ctl_밝게**

![demo / desktop / /report / ctl_밝게](docs/readme-media/capture-a50ce54cf77fd5352303.png)

**demo / desktop / /report / ctl_어둡게**

![demo / desktop / /report / ctl_어둡게](docs/readme-media/capture-af3a82abacd6cd9bf559.png)

**demo / desktop / /settings / page**

![demo / desktop / /settings / page](docs/readme-media/capture-6ff68adc8cb6dc3936fb.png)

**demo / desktop / /settings / ctl_밝게**

![demo / desktop / /settings / ctl_밝게](docs/readme-media/capture-434ea76325cab4039405.png)

**demo / desktop / /settings / ctl_어둡게**

![demo / desktop / /settings / ctl_어둡게](docs/readme-media/capture-a34466b841b2508ff1f4.png)

**demo / desktop / /settings / ctl_MDD최대낙폭**

![demo / desktop / /settings / ctl_MDD최대낙폭](docs/readme-media/capture-2547a5160ecf56a007ef.png)

**demo / desktop / /settings / ctl_Sharpe지수**

![demo / desktop / /settings / ctl_Sharpe지수](docs/readme-media/capture-77e8b1d48c62ba177b10.png)

**demo / desktop / /settings / ctl_ATR**

![demo / desktop / /settings / ctl_ATR](docs/readme-media/capture-4fda9d6c1cea38f354c2.png)

**demo / desktop / /admin / page**

![demo / desktop / /admin / page](docs/readme-media/capture-f2b6223289349d60f947.png)

**demo / desktop / /admin / ctl_밝게**

![demo / desktop / /admin / ctl_밝게](docs/readme-media/capture-2a6b4a278cee47db41fa.png)

**demo / desktop / /admin / ctl_어둡게**

![demo / desktop / /admin / ctl_어둡게](docs/readme-media/capture-7e94a5379b212f836fdf.png)

**demo / desktop / /admin / ctl_검색**

![demo / desktop / /admin / ctl_검색](docs/readme-media/capture-f407398ab96be48c62cc.png)

**demo / desktop / / / page**

![demo / desktop / / / page](docs/readme-media/capture-d77c67326680c68ede1d.png)

**demo / desktop / / / ctl_밝게**

![demo / desktop / / / ctl_밝게](docs/readme-media/capture-9b098805a0aeb4a4a4c7.png)

**demo / desktop / / / ctl_어둡게**

![demo / desktop / / / ctl_어둡게](docs/readme-media/capture-cb26fa8498d955e25f37.png)

**demo / mobile**

![demo / mobile](docs/readme-media/capture-a91c4fe5d1abdc4852be.png)

**demo / mobile**

![demo / mobile](docs/readme-media/capture-17f5dec69964f39192e2.png)

**demo / mobile**

![demo / mobile](docs/readme-media/capture-d79fa57c99238dec8e28.png)

**demo / mobile / /principles / page**

![demo / mobile / /principles / page](docs/readme-media/capture-c5b9544896602c4feb8d.png)

**demo / mobile / /principles / ctl_밝게**

![demo / mobile / /principles / ctl_밝게](docs/readme-media/capture-38b629cc218ff9f5fc4b.png)

**demo / mobile / /principles / ctl_어둡게**

![demo / mobile / /principles / ctl_어둡게](docs/readme-media/capture-e5ccf639a6090004ba7a.png)

**demo / mobile / /strategy / page**

![demo / mobile / /strategy / page](docs/readme-media/capture-1b7c635f78aabef68bdb.png)

**demo / mobile / /strategy / ctl_밝게**

![demo / mobile / /strategy / ctl_밝게](docs/readme-media/capture-f89900a28764f35c3e55.png)

**demo / mobile / /strategy / ctl_어둡게**

![demo / mobile / /strategy / ctl_어둡게](docs/readme-media/capture-53577b420bce31fbba5a.png)

**demo / mobile / /strategy / ctl_백테스트리포트**

![demo / mobile / /strategy / ctl_백테스트리포트](docs/readme-media/capture-d9ffa3ff7f4a8d0cc4ae.png)

**demo / mobile / /strategy / ctl_CAGR**

![demo / mobile / /strategy / ctl_CAGR](docs/readme-media/capture-715bc299162358263601.png)

**demo / mobile / /strategy / ctl_MDD**

![demo / mobile / /strategy / ctl_MDD](docs/readme-media/capture-197568c1149cedd5fbb3.png)

**demo / mobile / /strategy / ctl_Sharpe**

![demo / mobile / /strategy / ctl_Sharpe](docs/readme-media/capture-f858d0b596e06bbe9e2d.png)

**demo / mobile / /backtest / ctl_밝게**

![demo / mobile / /backtest / ctl_밝게](docs/readme-media/capture-17a2966fcbdcada7a377.png)

**demo / mobile / /backtest / ctl_어둡게**

![demo / mobile / /backtest / ctl_어둡게](docs/readme-media/capture-ac2f4502fec97086e066.png)

**demo / mobile / /backtest / ctl_CAGR**

![demo / mobile / /backtest / ctl_CAGR](docs/readme-media/capture-727063599ad0b96d3448.png)

**demo / mobile / /backtest / ctl_MDD**

![demo / mobile / /backtest / ctl_MDD](docs/readme-media/capture-33c9ba265671231afb79.png)

**demo / mobile / /backtest / ctl_Sharpe**

![demo / mobile / /backtest / ctl_Sharpe](docs/readme-media/capture-49f0c6bc3cc400c85e5b.png)

**demo / mobile / /model-evaluation / ctl_Sharpe**

![demo / mobile / /model-evaluation / ctl_Sharpe](docs/readme-media/capture-2b89c75329757b3dbaf5.png)

**demo / mobile / /rag / page**

![demo / mobile / /rag / page](docs/readme-media/capture-18b462d6bd629b4d8a81.png)

**demo / mobile / /rag / ctl_밝게**

![demo / mobile / /rag / ctl_밝게](docs/readme-media/capture-ba66cf62cff30211fd4b.png)

**demo / mobile / /rag / ctl_어둡게**

![demo / mobile / /rag / ctl_어둡게](docs/readme-media/capture-00b27fd4ccbf50f1eb4b.png)

**demo / mobile / /rag / ctl_동의**

![demo / mobile / /rag / ctl_동의](docs/readme-media/capture-0e7819abad3d8c2a6331.png)

**demo / mobile / /rag / ctl_철회**

![demo / mobile / /rag / ctl_철회](docs/readme-media/capture-899a1a2cac690a889c6a.png)

**demo / mobile / /rag / ctl_짧게**

![demo / mobile / /rag / ctl_짧게](docs/readme-media/capture-81e1b8a1a9fd5d985177.png)

**demo / mobile / /rag / ctl_자세히**

![demo / mobile / /rag / ctl_자세히](docs/readme-media/capture-8778375ba5cee384abef.png)

**demo / mobile / /rag / ctl_연구근거1개더보기**

![demo / mobile / /rag / ctl_연구근거1개더보기](docs/readme-media/capture-af90c558f56dd6bf393e.png)

**demo / mobile / /automation / page**

![demo / mobile / /automation / page](docs/readme-media/capture-1782ba429ef2be79b67f.png)

**demo / mobile / /automation / ctl_밝게**

![demo / mobile / /automation / ctl_밝게](docs/readme-media/capture-bc252e036a6294515204.png)

**demo / mobile / /automation / ctl_어둡게**

![demo / mobile / /automation / ctl_어둡게](docs/readme-media/capture-1f7c433b41683c5de14e.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-ac437ae92b5f8850f34f.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-a12523f75c1f9bd3afe6.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-2871df25b0d07ae4ebcd.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-9c37b4ae4ab9e46c4fb5.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-0f1917acd0a6e6118f91.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-b3bb8d6ab1b7535257cc.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-ee569bd82fb2dffde545.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-272971d1ae5d2182f594.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-f4d2734be168008beed4.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-733359f369a55a6fa47c.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-29be5f4b0642762d89a5.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-b27ea805b6c63fa5f49c.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-993c54fda390182eee97.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-c3232bece50fcbe0260b.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-8cd3cfc466ae57165dc3.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-a69f631ee5c601d6b84c.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-d76756119f7eecf093a6.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-258bb79df99b8368218e.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-12dda13c664ed8c1601d.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-db534bcfa1db90dec996.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-d1b22799d55eadcce3a2.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-e627cd4de1bf48eb0346.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-56f811c1fadb35dbd19b.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-da5674b048cab7eb5b2a.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-7018573907d02813c7a6.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-7beb5ce25d1747715963.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-2f62614468254fd46f15.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-c27cbb3ec7d8bd482fb7.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-e227a4281d84589876ad.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-fb1e22d3f9ff6430a12e.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-189f131a90c50364d4c5.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-ead470124f74ed333f4d.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-cb2aaa1d40581c3b93c4.png)

**demo / mobile / /automation / ctl_판단근거보기**

![demo / mobile / /automation / ctl_판단근거보기](docs/readme-media/capture-c96fd203074f734f2878.png)

**demo / mobile / /automation / ctl_지정가**

![demo / mobile / /automation / ctl_지정가](docs/readme-media/capture-2673531bf2e1f710f5d6.png)

**demo / mobile / /journal / page**

![demo / mobile / /journal / page](docs/readme-media/capture-05355541eab332040939.png)

**demo / mobile / /journal / ctl_밝게**

![demo / mobile / /journal / ctl_밝게](docs/readme-media/capture-46763071cd8717cf3413.png)

**demo / mobile / /journal / ctl_어둡게**

![demo / mobile / /journal / ctl_어둡게](docs/readme-media/capture-5c4a8c2989a9ceb62d13.png)

**demo / mobile / /report / page**

![demo / mobile / /report / page](docs/readme-media/capture-48d7c53090e00e62156d.png)

**demo / mobile / /report / ctl_밝게**

![demo / mobile / /report / ctl_밝게](docs/readme-media/capture-c7f6da82a1f0d27e2df1.png)

**demo / mobile / /report / ctl_어둡게**

![demo / mobile / /report / ctl_어둡게](docs/readme-media/capture-91b40512c39fd7ffe8e6.png)

**demo / mobile / /settings / page**

![demo / mobile / /settings / page](docs/readme-media/capture-c66ebcf66b5bccc14e23.png)

**demo / mobile / /settings / ctl_밝게**

![demo / mobile / /settings / ctl_밝게](docs/readme-media/capture-5739361bf2d7108d3925.png)

**demo / mobile / /settings / ctl_어둡게**

![demo / mobile / /settings / ctl_어둡게](docs/readme-media/capture-b92f8528bf4eeeccbeec.png)

**demo / mobile / /settings / ctl_MDD최대낙폭**

![demo / mobile / /settings / ctl_MDD최대낙폭](docs/readme-media/capture-1b229fd0d66e0b29c4ec.png)

**demo / mobile / /settings / ctl_Sharpe지수**

![demo / mobile / /settings / ctl_Sharpe지수](docs/readme-media/capture-5f6ef6fe46e36cfe4052.png)

**demo / mobile / /settings / ctl_ATR**

![demo / mobile / /settings / ctl_ATR](docs/readme-media/capture-74cca872f26000c56db8.png)

**demo / mobile / /admin / page**

![demo / mobile / /admin / page](docs/readme-media/capture-821decc7510e6d06a22f.png)

**demo / mobile / /admin / ctl_밝게**

![demo / mobile / /admin / ctl_밝게](docs/readme-media/capture-d4629c906ffa0a166204.png)

**demo / mobile / /admin / ctl_어둡게**

![demo / mobile / /admin / ctl_어둡게](docs/readme-media/capture-68c6f32645e0cc1da72f.png)

**demo / mobile / /admin / ctl_검색**

![demo / mobile / /admin / ctl_검색](docs/readme-media/capture-9e15de4f5b8093b3cae0.png)

**demo / mobile / / / ctl_밝게**

![demo / mobile / / / ctl_밝게](docs/readme-media/capture-54580950a2beab1b8fe5.png)

**initial / desktop / / / page**

![initial / desktop / / / page](docs/readme-media/capture-afdb7f79cb5be9ba9e36.png)

**initial / desktop / / / ctl_로그인**

![initial / desktop / / / ctl_로그인](docs/readme-media/capture-377090df5364d6036316.png)

**initial / desktop / / / ctl_회원가입**

![initial / desktop / / / ctl_회원가입](docs/readme-media/capture-e7afd5dbd17e85326bf1.png)

**initial / desktop / / / ctl_로그인**

![initial / desktop / / / ctl_로그인](docs/readme-media/capture-6c4a93eaf81ad7dd4795.png)

**initial / desktop / / / ctl_테마**

![initial / desktop / / / ctl_테마](docs/readme-media/capture-fd0175ed84893d129397.png)

**initial / desktop / / / ctl_시작하기**

![initial / desktop / / / ctl_시작하기](docs/readme-media/capture-0bb39751cac286cc9151.png)

**initial / desktop / / / ctl_OpenNextjsDevTools**

![initial / desktop / / / ctl_OpenNextjsDevTools](docs/readme-media/capture-045d2fefc626933c63a1.png)

**experience-dashboard / desktop / /intro / page**

![experience-dashboard / desktop / /intro / page](docs/readme-media/capture-bc036eb30adaabcf47c4.png)

**experience-dashboard / desktop / /intro / ctl_테마**

![experience-dashboard / desktop / /intro / ctl_테마](docs/readme-media/capture-af0aad12dc4d6cc9fb47.png)

**experience-dashboard / desktop / /intro / ctl_대시보드**

![experience-dashboard / desktop / /intro / ctl_대시보드](docs/readme-media/capture-fb7dfb79ed3da25f49fb.png)

**experience-dashboard / desktop / /intro / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /intro / ctl_OpenNextjsDevTools](docs/readme-media/capture-5c462fcf21df6d9fc1d6.png)

**experience-dashboard / desktop / /principles / page**

![experience-dashboard / desktop / /principles / page](docs/readme-media/capture-cd0e0439698bab980e32.png)

**experience-dashboard / desktop / /principles / ctl_로그인**

![experience-dashboard / desktop / /principles / ctl_로그인](docs/readme-media/capture-dd377461106d27e8d787.png)

**experience-dashboard / desktop / /principles / ctl_회원가입**

![experience-dashboard / desktop / /principles / ctl_회원가입](docs/readme-media/capture-8a308908ebd53b7ed553.png)

**experience-dashboard / desktop / /principles / ctl_로그인**

![experience-dashboard / desktop / /principles / ctl_로그인](docs/readme-media/capture-2e9d985ae9d43c50f55e.png)

**experience-dashboard / desktop / /principles / ctl_테마**

![experience-dashboard / desktop / /principles / ctl_테마](docs/readme-media/capture-3f9ab0959fa008e20ca5.png)

**experience-dashboard / desktop / /principles / ctl_시작하기**

![experience-dashboard / desktop / /principles / ctl_시작하기](docs/readme-media/capture-2ac277752e1d0979cec3.png)

**experience-dashboard / desktop / /principles / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /principles / ctl_OpenNextjsDevTools](docs/readme-media/capture-8571fe44b8a0ca7c88c9.png)

**experience-dashboard / desktop / /strategy / page**

![experience-dashboard / desktop / /strategy / page](docs/readme-media/capture-37cf0cff290435e0316f.png)

**experience-dashboard / desktop / /strategy / ctl_로그인**

![experience-dashboard / desktop / /strategy / ctl_로그인](docs/readme-media/capture-f9ba79735908aed934c1.png)

**experience-dashboard / desktop / /strategy / ctl_회원가입**

![experience-dashboard / desktop / /strategy / ctl_회원가입](docs/readme-media/capture-0200080b8f38cb06c99d.png)

**experience-dashboard / desktop / /strategy / ctl_로그인**

![experience-dashboard / desktop / /strategy / ctl_로그인](docs/readme-media/capture-e03fd632c2a2ac73e4aa.png)

**experience-dashboard / desktop / /strategy / ctl_테마**

![experience-dashboard / desktop / /strategy / ctl_테마](docs/readme-media/capture-84a7ba15599cb868bc95.png)

**experience-dashboard / desktop / /strategy / ctl_시작하기**

![experience-dashboard / desktop / /strategy / ctl_시작하기](docs/readme-media/capture-a1e73cb95482259dd89a.png)

**experience-dashboard / desktop / /strategy / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /strategy / ctl_OpenNextjsDevTools](docs/readme-media/capture-78765b59c24a2dc284cf.png)

**experience-dashboard / desktop / /backtest / page**

![experience-dashboard / desktop / /backtest / page](docs/readme-media/capture-b40b81ce904f56584e98.png)

**experience-dashboard / desktop / /backtest / ctl_로그인**

![experience-dashboard / desktop / /backtest / ctl_로그인](docs/readme-media/capture-29179a51962226cae9e4.png)

**experience-dashboard / desktop / /backtest / ctl_회원가입**

![experience-dashboard / desktop / /backtest / ctl_회원가입](docs/readme-media/capture-e3d8c9e00bda51e7aa83.png)

**experience-dashboard / desktop / /backtest / ctl_로그인**

![experience-dashboard / desktop / /backtest / ctl_로그인](docs/readme-media/capture-8695809c70ae3a7c0471.png)

**experience-dashboard / desktop / /backtest / ctl_테마**

![experience-dashboard / desktop / /backtest / ctl_테마](docs/readme-media/capture-4b1be9ce3ca67638922f.png)

**experience-dashboard / desktop / /backtest / ctl_시작하기**

![experience-dashboard / desktop / /backtest / ctl_시작하기](docs/readme-media/capture-7f249d07efd837bba2d0.png)

**experience-dashboard / desktop / /backtest / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /backtest / ctl_OpenNextjsDevTools](docs/readme-media/capture-f86d1e77632d340ea168.png)

**experience-dashboard / desktop / /model-evaluation / page**

![experience-dashboard / desktop / /model-evaluation / page](docs/readme-media/capture-1231c398b83cca33bd85.png)

**experience-dashboard / desktop / /model-evaluation / ctl_로그인**

![experience-dashboard / desktop / /model-evaluation / ctl_로그인](docs/readme-media/capture-962f5b3d65096764af36.png)

**experience-dashboard / desktop / /model-evaluation / ctl_회원가입**

![experience-dashboard / desktop / /model-evaluation / ctl_회원가입](docs/readme-media/capture-24c7528fb8595f5fa882.png)

**experience-dashboard / desktop / /model-evaluation / ctl_로그인**

![experience-dashboard / desktop / /model-evaluation / ctl_로그인](docs/readme-media/capture-3367f939ac1ffb89e0bc.png)

**experience-dashboard / desktop / /model-evaluation / ctl_테마**

![experience-dashboard / desktop / /model-evaluation / ctl_테마](docs/readme-media/capture-5f0bc418dc1066786130.png)

**experience-dashboard / desktop / /model-evaluation / ctl_시작하기**

![experience-dashboard / desktop / /model-evaluation / ctl_시작하기](docs/readme-media/capture-2eefa29cbab699ccb4d2.png)

**experience-dashboard / desktop / /model-evaluation / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /model-evaluation / ctl_OpenNextjsDevTools](docs/readme-media/capture-adc3efa8d0fc3c726138.png)

**experience-dashboard / desktop / /rag / page**

![experience-dashboard / desktop / /rag / page](docs/readme-media/capture-f81a2affbbfa74681a15.png)

**experience-dashboard / desktop / /rag / ctl_로그인**

![experience-dashboard / desktop / /rag / ctl_로그인](docs/readme-media/capture-123336626cbe4d5c0208.png)

**experience-dashboard / desktop / /rag / ctl_회원가입**

![experience-dashboard / desktop / /rag / ctl_회원가입](docs/readme-media/capture-98de25a3c8b7d41a1e9b.png)

**experience-dashboard / desktop / /rag / ctl_로그인**

![experience-dashboard / desktop / /rag / ctl_로그인](docs/readme-media/capture-643d640476f27c6c6616.png)

**experience-dashboard / desktop / /rag / ctl_테마**

![experience-dashboard / desktop / /rag / ctl_테마](docs/readme-media/capture-65f83d03fc3d2e3c8f8a.png)

**experience-dashboard / desktop / /rag / ctl_시작하기**

![experience-dashboard / desktop / /rag / ctl_시작하기](docs/readme-media/capture-41a5b881958324802c5d.png)

**experience-dashboard / desktop / /rag / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /rag / ctl_OpenNextjsDevTools](docs/readme-media/capture-2ee30b1b2c9efacc325b.png)

**experience-dashboard / desktop / /automation / page**

![experience-dashboard / desktop / /automation / page](docs/readme-media/capture-72fc7f4589d33d460983.png)

**experience-dashboard / desktop / /automation / ctl_로그인**

![experience-dashboard / desktop / /automation / ctl_로그인](docs/readme-media/capture-6e79c48d67dfa3cf3d2e.png)

**experience-dashboard / desktop / /automation / ctl_회원가입**

![experience-dashboard / desktop / /automation / ctl_회원가입](docs/readme-media/capture-2c7a1f94c6420cc23994.png)

**experience-dashboard / desktop / /automation / ctl_로그인**

![experience-dashboard / desktop / /automation / ctl_로그인](docs/readme-media/capture-2361fb5c300216b101df.png)

**experience-dashboard / desktop / /automation / ctl_테마**

![experience-dashboard / desktop / /automation / ctl_테마](docs/readme-media/capture-6d4a8ba48e982ba505f2.png)

**experience-dashboard / desktop / /automation / ctl_시작하기**

![experience-dashboard / desktop / /automation / ctl_시작하기](docs/readme-media/capture-9123381338310fedf55a.png)

**experience-dashboard / desktop / /automation / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /automation / ctl_OpenNextjsDevTools](docs/readme-media/capture-1bcd26120d5e3ed024dc.png)

**experience-dashboard / desktop / /journal / page**

![experience-dashboard / desktop / /journal / page](docs/readme-media/capture-185f512735aaa545596d.png)

**experience-dashboard / desktop / /journal / ctl_로그인**

![experience-dashboard / desktop / /journal / ctl_로그인](docs/readme-media/capture-e7c76bbe759603be512f.png)

**experience-dashboard / desktop / /journal / ctl_회원가입**

![experience-dashboard / desktop / /journal / ctl_회원가입](docs/readme-media/capture-bb116f62304d98af936e.png)

**experience-dashboard / desktop / /journal / ctl_로그인**

![experience-dashboard / desktop / /journal / ctl_로그인](docs/readme-media/capture-e3e6000e6740863bebed.png)

**experience-dashboard / desktop / /journal / ctl_테마**

![experience-dashboard / desktop / /journal / ctl_테마](docs/readme-media/capture-c8e47dd57b6af88ff63f.png)

**experience-dashboard / desktop / /journal / ctl_시작하기**

![experience-dashboard / desktop / /journal / ctl_시작하기](docs/readme-media/capture-f54bf702dcdec34cc028.png)

**experience-dashboard / desktop / /journal / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /journal / ctl_OpenNextjsDevTools](docs/readme-media/capture-8e41dd0ab490cadd7934.png)

**experience-dashboard / desktop / /report / page**

![experience-dashboard / desktop / /report / page](docs/readme-media/capture-c994760e2198d165b70a.png)

**experience-dashboard / desktop / /report / ctl_로그인**

![experience-dashboard / desktop / /report / ctl_로그인](docs/readme-media/capture-bd7249afa671a1c753e6.png)

**experience-dashboard / desktop / /report / ctl_회원가입**

![experience-dashboard / desktop / /report / ctl_회원가입](docs/readme-media/capture-8dc0527f23b05abd47e2.png)

**experience-dashboard / desktop / /report / ctl_로그인**

![experience-dashboard / desktop / /report / ctl_로그인](docs/readme-media/capture-418fafe64e6e3250941f.png)

**experience-dashboard / desktop / /report / ctl_테마**

![experience-dashboard / desktop / /report / ctl_테마](docs/readme-media/capture-ecc710fa4e5e8c16b371.png)

**experience-dashboard / desktop / /report / ctl_시작하기**

![experience-dashboard / desktop / /report / ctl_시작하기](docs/readme-media/capture-963b9826ba6ef41895b9.png)

**experience-dashboard / desktop / /report / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /report / ctl_OpenNextjsDevTools](docs/readme-media/capture-390aef2a187bf82cc243.png)

**experience-dashboard / desktop / /settings / page**

![experience-dashboard / desktop / /settings / page](docs/readme-media/capture-e9cf4be55b17ff91ccff.png)

**experience-dashboard / desktop / /settings / ctl_로그인**

![experience-dashboard / desktop / /settings / ctl_로그인](docs/readme-media/capture-773b4f9d6987b5c91d9d.png)

**experience-dashboard / desktop / /settings / ctl_로그인**

![experience-dashboard / desktop / /settings / ctl_로그인](docs/readme-media/capture-acf4227f8afe82497ffb.png)

**experience-dashboard / desktop / /settings / ctl_테마**

![experience-dashboard / desktop / /settings / ctl_테마](docs/readme-media/capture-bc1911279e416ea886f1.png)

**experience-dashboard / desktop / /settings / ctl_시작하기**

![experience-dashboard / desktop / /settings / ctl_시작하기](docs/readme-media/capture-e16f01175b1f1f256618.png)

**experience-dashboard / desktop / /admin / page**

![experience-dashboard / desktop / /admin / page](docs/readme-media/capture-bfdfa3ebd3ef184d732f.png)

**experience-dashboard / desktop / /admin / ctl_로그인**

![experience-dashboard / desktop / /admin / ctl_로그인](docs/readme-media/capture-dfeb9f5f92e28233e0b0.png)

**experience-dashboard / desktop / /admin / ctl_회원가입**

![experience-dashboard / desktop / /admin / ctl_회원가입](docs/readme-media/capture-b6206e88d70e2c907b5b.png)

**experience-dashboard / desktop / /admin / ctl_로그인**

![experience-dashboard / desktop / /admin / ctl_로그인](docs/readme-media/capture-12d2b6fd794642170f2b.png)

**experience-dashboard / desktop / /admin / ctl_테마**

![experience-dashboard / desktop / /admin / ctl_테마](docs/readme-media/capture-b1d76d571c8b02fb3da2.png)

**experience-dashboard / desktop / /admin / ctl_시작하기**

![experience-dashboard / desktop / /admin / ctl_시작하기](docs/readme-media/capture-195deee343c7c8a69375.png)

**experience-dashboard / desktop / /admin / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / /admin / ctl_OpenNextjsDevTools](docs/readme-media/capture-0815a85ea0f2bf524cc3.png)

**experience-dashboard / desktop / / / page**

![experience-dashboard / desktop / / / page](docs/readme-media/capture-8c75db6848c827acfb86.png)

**experience-dashboard / desktop / / / ctl_회원가입**

![experience-dashboard / desktop / / / ctl_회원가입](docs/readme-media/capture-de9a75e97603e6b389ab.png)

**experience-dashboard / desktop / / / ctl_로그인**

![experience-dashboard / desktop / / / ctl_로그인](docs/readme-media/capture-d0e70c7cd8e20942ac1a.png)

**experience-dashboard / desktop / / / ctl_테마**

![experience-dashboard / desktop / / / ctl_테마](docs/readme-media/capture-a71f13669be1b03c6a04.png)

**experience-dashboard / desktop / / / ctl_시작하기**

![experience-dashboard / desktop / / / ctl_시작하기](docs/readme-media/capture-ccb5fb4ecf9694ca5446.png)

**experience-dashboard / desktop / / / ctl_OpenNextjsDevTools**

![experience-dashboard / desktop / / / ctl_OpenNextjsDevTools](docs/readme-media/capture-091514c21751974f65dd.png)

**experience-dashboard / mobile / /intro / page**

![experience-dashboard / mobile / /intro / page](docs/readme-media/capture-da9acad91642d55a4edb.png)

**experience-dashboard / mobile / /intro / ctl_테마**

![experience-dashboard / mobile / /intro / ctl_테마](docs/readme-media/capture-db417fe21385837bbea1.png)

**experience-dashboard / mobile / /intro / ctl_대시보드**

![experience-dashboard / mobile / /intro / ctl_대시보드](docs/readme-media/capture-450217a0bea2d0fa3f2a.png)

**experience-dashboard / mobile / /intro / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /intro / ctl_OpenNextjsDevTools](docs/readme-media/capture-f7ab7810ffd1e38af9d3.png)

**experience-dashboard / mobile / /principles / page**

![experience-dashboard / mobile / /principles / page](docs/readme-media/capture-c2caaec30df0051fbb67.png)

**experience-dashboard / mobile / /principles / ctl_로그인**

![experience-dashboard / mobile / /principles / ctl_로그인](docs/readme-media/capture-240b465487b00e393254.png)

**experience-dashboard / mobile / /principles / ctl_회원가입**

![experience-dashboard / mobile / /principles / ctl_회원가입](docs/readme-media/capture-252a264dd6342fa7c5aa.png)

**experience-dashboard / mobile / /principles / ctl_로그인**

![experience-dashboard / mobile / /principles / ctl_로그인](docs/readme-media/capture-ea8e886b0f5482ff2558.png)

**experience-dashboard / mobile / /principles / ctl_테마**

![experience-dashboard / mobile / /principles / ctl_테마](docs/readme-media/capture-166b499d0feb99b79746.png)

**experience-dashboard / mobile / /principles / ctl_시작하기**

![experience-dashboard / mobile / /principles / ctl_시작하기](docs/readme-media/capture-302e388089fa22288d33.png)

**experience-dashboard / mobile / /principles / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /principles / ctl_OpenNextjsDevTools](docs/readme-media/capture-92e26269ad9ca49ba9ff.png)

**experience-dashboard / mobile / /strategy / page**

![experience-dashboard / mobile / /strategy / page](docs/readme-media/capture-70b43244c8d4b4d8848d.png)

**experience-dashboard / mobile / /strategy / ctl_로그인**

![experience-dashboard / mobile / /strategy / ctl_로그인](docs/readme-media/capture-fea0fcf2619a72d94e97.png)

**experience-dashboard / mobile / /strategy / ctl_회원가입**

![experience-dashboard / mobile / /strategy / ctl_회원가입](docs/readme-media/capture-8b05c4b01aa9f5b8f345.png)

**experience-dashboard / mobile / /strategy / ctl_로그인**

![experience-dashboard / mobile / /strategy / ctl_로그인](docs/readme-media/capture-14de6572d2b24c3e5f9c.png)

**experience-dashboard / mobile / /strategy / ctl_테마**

![experience-dashboard / mobile / /strategy / ctl_테마](docs/readme-media/capture-ce546f213ce9f525cffb.png)

**experience-dashboard / mobile / /strategy / ctl_시작하기**

![experience-dashboard / mobile / /strategy / ctl_시작하기](docs/readme-media/capture-3354db3603fe1a8232a6.png)

**experience-dashboard / mobile / /strategy / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /strategy / ctl_OpenNextjsDevTools](docs/readme-media/capture-fc7e60a5a4febcc2f795.png)

**experience-dashboard / mobile / /backtest / page**

![experience-dashboard / mobile / /backtest / page](docs/readme-media/capture-6b58f3b1e090892729fa.png)

**experience-dashboard / mobile / /backtest / ctl_로그인**

![experience-dashboard / mobile / /backtest / ctl_로그인](docs/readme-media/capture-a415f3a645104e4c813b.png)

**experience-dashboard / mobile / /backtest / ctl_회원가입**

![experience-dashboard / mobile / /backtest / ctl_회원가입](docs/readme-media/capture-253fcc1ed3ed4274e44f.png)

**experience-dashboard / mobile / /backtest / ctl_로그인**

![experience-dashboard / mobile / /backtest / ctl_로그인](docs/readme-media/capture-03f83eb02c6debcb029a.png)

**experience-dashboard / mobile / /backtest / ctl_테마**

![experience-dashboard / mobile / /backtest / ctl_테마](docs/readme-media/capture-a07e9aec5e97c1d09334.png)

**experience-dashboard / mobile / /backtest / ctl_시작하기**

![experience-dashboard / mobile / /backtest / ctl_시작하기](docs/readme-media/capture-bf320cee1789bed401a5.png)

**experience-dashboard / mobile / /backtest / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /backtest / ctl_OpenNextjsDevTools](docs/readme-media/capture-7c213e3a957e9fe46b92.png)

**experience-dashboard / mobile / /model-evaluation / page**

![experience-dashboard / mobile / /model-evaluation / page](docs/readme-media/capture-2534351b405a561a862f.png)

**experience-dashboard / mobile / /model-evaluation / ctl_로그인**

![experience-dashboard / mobile / /model-evaluation / ctl_로그인](docs/readme-media/capture-cf170b58d6c6a66da0d0.png)

**experience-dashboard / mobile / /model-evaluation / ctl_회원가입**

![experience-dashboard / mobile / /model-evaluation / ctl_회원가입](docs/readme-media/capture-5112eba83932ddb856ed.png)

**experience-dashboard / mobile / /model-evaluation / ctl_로그인**

![experience-dashboard / mobile / /model-evaluation / ctl_로그인](docs/readme-media/capture-1469bcdb5019b8d9e789.png)

**experience-dashboard / mobile / /model-evaluation / ctl_테마**

![experience-dashboard / mobile / /model-evaluation / ctl_테마](docs/readme-media/capture-46abaae7962e819d76c0.png)

**experience-dashboard / mobile / /model-evaluation / ctl_시작하기**

![experience-dashboard / mobile / /model-evaluation / ctl_시작하기](docs/readme-media/capture-bd3e18b0aaa71c14c5fe.png)

**experience-dashboard / mobile / /model-evaluation / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /model-evaluation / ctl_OpenNextjsDevTools](docs/readme-media/capture-7576ad7c7b705088b589.png)

**experience-dashboard / mobile / /rag / page**

![experience-dashboard / mobile / /rag / page](docs/readme-media/capture-7a5e054214daef1aad42.png)

**experience-dashboard / mobile / /rag / ctl_로그인**

![experience-dashboard / mobile / /rag / ctl_로그인](docs/readme-media/capture-2998211a6ef1c08f5593.png)

**experience-dashboard / mobile / /rag / ctl_회원가입**

![experience-dashboard / mobile / /rag / ctl_회원가입](docs/readme-media/capture-c2fbe3b2b42122b3ac5b.png)

**experience-dashboard / mobile / /rag / ctl_로그인**

![experience-dashboard / mobile / /rag / ctl_로그인](docs/readme-media/capture-398e7dddb682ff449318.png)

**experience-dashboard / mobile / /rag / ctl_테마**

![experience-dashboard / mobile / /rag / ctl_테마](docs/readme-media/capture-678f2cdee243d41d89cb.png)

**experience-dashboard / mobile / /rag / ctl_시작하기**

![experience-dashboard / mobile / /rag / ctl_시작하기](docs/readme-media/capture-52f0f88d361f23db5e6c.png)

**experience-dashboard / mobile / /rag / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /rag / ctl_OpenNextjsDevTools](docs/readme-media/capture-159b21ab575ad025ab5d.png)

**experience-dashboard / mobile / /automation / page**

![experience-dashboard / mobile / /automation / page](docs/readme-media/capture-d6e8fdaf659bc671066e.png)

**experience-dashboard / mobile / /automation / ctl_로그인**

![experience-dashboard / mobile / /automation / ctl_로그인](docs/readme-media/capture-737e49141338a4ce3d73.png)

**experience-dashboard / mobile / /automation / ctl_회원가입**

![experience-dashboard / mobile / /automation / ctl_회원가입](docs/readme-media/capture-919750267a94a9c5dfeb.png)

**experience-dashboard / mobile / /automation / ctl_로그인**

![experience-dashboard / mobile / /automation / ctl_로그인](docs/readme-media/capture-c22f47a0a3a3e23693c5.png)

**experience-dashboard / mobile / /automation / ctl_테마**

![experience-dashboard / mobile / /automation / ctl_테마](docs/readme-media/capture-9837507dad91022a570d.png)

**experience-dashboard / mobile / /automation / ctl_시작하기**

![experience-dashboard / mobile / /automation / ctl_시작하기](docs/readme-media/capture-eab8923a3858813cf4e1.png)

**experience-dashboard / mobile / /automation / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /automation / ctl_OpenNextjsDevTools](docs/readme-media/capture-2a9ab99c2acb55ac4b5d.png)

**experience-dashboard / mobile / /journal / page**

![experience-dashboard / mobile / /journal / page](docs/readme-media/capture-da69e815c5e773c6a2cb.png)

**experience-dashboard / mobile / /journal / ctl_로그인**

![experience-dashboard / mobile / /journal / ctl_로그인](docs/readme-media/capture-bc194fae813d30689469.png)

**experience-dashboard / mobile / /journal / ctl_회원가입**

![experience-dashboard / mobile / /journal / ctl_회원가입](docs/readme-media/capture-97cecbaf448fad01f596.png)

**experience-dashboard / mobile / /journal / ctl_로그인**

![experience-dashboard / mobile / /journal / ctl_로그인](docs/readme-media/capture-aa31b0e1ea53eaa08401.png)

**experience-dashboard / mobile / /journal / ctl_테마**

![experience-dashboard / mobile / /journal / ctl_테마](docs/readme-media/capture-bdc746ab539230d1b472.png)

**experience-dashboard / mobile / /journal / ctl_시작하기**

![experience-dashboard / mobile / /journal / ctl_시작하기](docs/readme-media/capture-3b1f67df2f8cdcc1c983.png)

**experience-dashboard / mobile / /journal / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /journal / ctl_OpenNextjsDevTools](docs/readme-media/capture-af9db45ef4067f9d62a1.png)

**experience-dashboard / mobile / /report / page**

![experience-dashboard / mobile / /report / page](docs/readme-media/capture-1372d16d79f100a63f27.png)

**experience-dashboard / mobile / /report / ctl_로그인**

![experience-dashboard / mobile / /report / ctl_로그인](docs/readme-media/capture-08a3e480876b38e91a99.png)

**experience-dashboard / mobile / /report / ctl_회원가입**

![experience-dashboard / mobile / /report / ctl_회원가입](docs/readme-media/capture-14baf5e1a9413cd23812.png)

**experience-dashboard / mobile / /report / ctl_로그인**

![experience-dashboard / mobile / /report / ctl_로그인](docs/readme-media/capture-67356cbeea01ac89992b.png)

**experience-dashboard / mobile / /report / ctl_테마**

![experience-dashboard / mobile / /report / ctl_테마](docs/readme-media/capture-86f6e64676a77aad77d0.png)

**experience-dashboard / mobile / /report / ctl_시작하기**

![experience-dashboard / mobile / /report / ctl_시작하기](docs/readme-media/capture-ec86286a7808e9d30305.png)

**experience-dashboard / mobile / /report / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /report / ctl_OpenNextjsDevTools](docs/readme-media/capture-73e23b15ec35f515ec04.png)

**experience-dashboard / mobile / /settings / page**

![experience-dashboard / mobile / /settings / page](docs/readme-media/capture-27d146b201c7658e1843.png)

**experience-dashboard / mobile / /settings / ctl_로그인**

![experience-dashboard / mobile / /settings / ctl_로그인](docs/readme-media/capture-143f7e17dd861f232b9f.png)

**experience-dashboard / mobile / /settings / ctl_회원가입**

![experience-dashboard / mobile / /settings / ctl_회원가입](docs/readme-media/capture-f9bb1139361e43aae7ba.png)

**experience-dashboard / mobile / /settings / ctl_로그인**

![experience-dashboard / mobile / /settings / ctl_로그인](docs/readme-media/capture-cace540a4e9bb9a7e8a3.png)

**experience-dashboard / mobile / /settings / ctl_테마**

![experience-dashboard / mobile / /settings / ctl_테마](docs/readme-media/capture-1bf07fb6179c2bd75928.png)

**experience-dashboard / mobile / /settings / ctl_시작하기**

![experience-dashboard / mobile / /settings / ctl_시작하기](docs/readme-media/capture-04d9addcc79f5595d42c.png)

**experience-dashboard / mobile / /settings / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / /settings / ctl_OpenNextjsDevTools](docs/readme-media/capture-2a8f0dce55d36c29e4b6.png)

**experience-dashboard / mobile / /admin / page**

![experience-dashboard / mobile / /admin / page](docs/readme-media/capture-09cdab641ead02996522.png)

**experience-dashboard / mobile / /admin / ctl_로그인**

![experience-dashboard / mobile / /admin / ctl_로그인](docs/readme-media/capture-f5bbdffa2c7a212f39f5.png)

**experience-dashboard / mobile / /admin / ctl_회원가입**

![experience-dashboard / mobile / /admin / ctl_회원가입](docs/readme-media/capture-f842984ac2709fcc76c8.png)

**experience-dashboard / mobile / /admin / ctl_로그인**

![experience-dashboard / mobile / /admin / ctl_로그인](docs/readme-media/capture-444dd60d03469bfee671.png)

**experience-dashboard / mobile / /admin / ctl_테마**

![experience-dashboard / mobile / /admin / ctl_테마](docs/readme-media/capture-158ba95b555398f8bf1f.png)

**experience-dashboard / mobile / /admin / ctl_시작하기**

![experience-dashboard / mobile / /admin / ctl_시작하기](docs/readme-media/capture-549b43d5250e85a0053b.png)

**experience-dashboard / mobile / / / page**

![experience-dashboard / mobile / / / page](docs/readme-media/capture-0d168b3e2c8c04949284.png)

**experience-dashboard / mobile / / / ctl_로그인**

![experience-dashboard / mobile / / / ctl_로그인](docs/readme-media/capture-71f870ae37f2d7e4ccab.png)

**experience-dashboard / mobile / / / ctl_회원가입**

![experience-dashboard / mobile / / / ctl_회원가입](docs/readme-media/capture-5629e885f653ea5b61d2.png)

**experience-dashboard / mobile / / / ctl_로그인**

![experience-dashboard / mobile / / / ctl_로그인](docs/readme-media/capture-2d99723410708c5a3bea.png)

**experience-dashboard / mobile / / / ctl_테마**

![experience-dashboard / mobile / / / ctl_테마](docs/readme-media/capture-9c4237689b57d8f20987.png)

**experience-dashboard / mobile / / / ctl_시작하기**

![experience-dashboard / mobile / / / ctl_시작하기](docs/readme-media/capture-2e3c64fd2a35db6fc502.png)

**experience-dashboard / mobile / / / ctl_OpenNextjsDevTools**

![experience-dashboard / mobile / / / ctl_OpenNextjsDevTools](docs/readme-media/capture-afe8311d6b38443bcbeb.png)

**initial / mobile / / / page**

![initial / mobile / / / page](docs/readme-media/capture-947179ba5694b1242de0.png)

**initial / mobile / / / ctl_로그인**

![initial / mobile / / / ctl_로그인](docs/readme-media/capture-1a6e27046c9dd6d8e2f3.png)

**initial / mobile / / / ctl_회원가입**

![initial / mobile / / / ctl_회원가입](docs/readme-media/capture-46413f49454207071fe0.png)

**initial / mobile / / / ctl_로그인**

![initial / mobile / / / ctl_로그인](docs/readme-media/capture-aded85210db5b8019b55.png)

**initial / mobile / / / ctl_테마**

![initial / mobile / / / ctl_테마](docs/readme-media/capture-b7f2dd16c8f599418559.png)

**initial / mobile / / / ctl_시작하기**

![initial / mobile / / / ctl_시작하기](docs/readme-media/capture-af120cd2851d68f23fde.png)

**initial / mobile / / / ctl_OpenNextjsDevTools**

![initial / mobile / / / ctl_OpenNextjsDevTools](docs/readme-media/capture-6ae3e49bacb268c446a3.png)

</details>
<!-- MARS_CAPTURE_GALLERY_END -->
