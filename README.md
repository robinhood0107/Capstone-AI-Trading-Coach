# Capstone AI Trading Coach

<!-- P1_FULL_APP_V2_AUTHORITY_BEGIN -->
> **1.0.0 current authority (2026-08-26):** Core는 `main=232f54ce…`에 병합됐지만 full-app v2는
> 통합 중이며 GitHub `1.0.0` Release는 아직 없다. 현재 계약은
> `contracts/catalogs/p1-full-app-release-contract.v2.json`이고 기존 offline v1은 역사적 회귀다.
> Team B real artifact, Seed, owner E2E, CPU/Intel, provider read, security·supply-chain, Compose E2E가
> 모두 PASS하기 전에는 tag와 Release를 만들지 않는다.
<!-- P1_FULL_APP_V2_AUTHORITY_END -->

투자 원칙을 먼저 고정하고, 저장된 시장·포트폴리오 근거로 위험을 설명하는 교육용 트레이딩 코치다.
현재 구현은 Decision Platform이 중심이며 실제 주문이나 수익 보장을 목표로 하지 않는다.

## 프로그램이 하는 일

사용자는 원칙을 만들고, 저장된 근거로 결정과 위험 결과를 확인하며, RAG 출처와 비동기 작업 상태를
조회할 수 있다. S7부터 RAG 색인·artifact ingest 같은 무거운 작업은 DB outbox를 기본으로 처리하고,
로컬에서 명시적으로 선택한 경우에만 Kafka KRaft 경로를 사용한다. S8은 네 Dashboard ViewModel과
provider가 없는 synthetic demo를 제공한다.

```text
authenticated client
  └─ Spring REST API
       ├─ Principle / Decision / Risk / RAG
       ├─ Admin async status / stream metrics
       └─ Dashboard ViewModel
            │
            ├─ PostgreSQL domain row + async_job + event_outbox
            └─ DB dispatcher 또는 Kafka publisher
                         └─ Python async worker
                              └─ processed_event + materialization + completion outbox
```

DB 저장과 Kafka publish는 하나의 분산 transaction이 아니다. domain row와 outbox insert만 같은 DB
transaction에 속하며, 이후 처리는 at-least-once와 `processed_event` 멱등성으로 안전하게 복구한다.

## 현재 안전 경계

- 기본 adapter는 `db`다. `ASYNC_ADAPTER=kafka`를 명시하기 전에는 Kafka를 사용하지 않는다.
- 애플리케이션 자체의 polling/worker 기본값은 안전하게 OFF다. `.env`에서 두 값을 명시적으로 true로
  설정한 로컬 runtime만 worker를 연다.
- `docker compose --profile kafka`는 인프라만 켠다. adapter를 자동 전환하지 않는다.
- 알 수 없는 adapter와 준비되지 않은 Kafka는 fail-closed다. 현재 build는 Kafka를 local numeric-loopback
  PLAINTEXT로만 지원하며 non-loopback/deploy 설정은 TLS 값을 넣어도 모두 거부한다.
- provider, live account, live order는 기본 OFF다. 외부 장애가 `INTERNAL_PAPER`로 자동 전환하지 않는다.
- S6.6/S6.7 cross-market runtime과 공개 API는 퇴역했다. scheduler, `WARN_ONLY` overlay, 주문 권한이 없다.
- LightGBM은 연구·재현 전용이며 Signal v2에서는 근거가 없으면 `ABSTAIN/MISSING_EVIDENCE`다.
- S1.4 production 기준선은 Python/NumPy다. Scala/Haskell은 격리 연구이며 runtime 교체 기능이 아니다.

### 보존 계약 호환성 marker

아래 값은 이미 동결된 Pre-S5 계약을 현재 문서에서도 검증하기 위한 marker다. S7/S8 runtime을
확장하거나 퇴역한 cross-market capability를 다시 활성화하는 권한이 아니다.

```text
PRE_S5_DOC_TRUTH_FREEZE_ADDENDUM_VERIFIED
PRE_S5_EXECUTION_OWNER=DECISION_PLATFORM
PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES
S4_8A=CONTRACT_LOCKED
S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE
```

## 준비물

- Java 25 LTS
- Docker Engine과 Docker Compose v2
- source 개발에는 Python 3.13 이상과 `uv` (P1 amd64 runtime image는 보안 스캔된 Wolfi Python 3.14.7)
- Git

환경 파일은 커밋하지 않는다. 처음 한 번 다음처럼 준비한다.

| 실행 경로 | 사용자가 `.env` 작성 | 권위 |
|---|---:|---|
| source 개발 | 필요 | 루트 `.env` |
| S8 demo | 불필요 | Gradle이 생성한 mode 0600 `demo.env` |
| P1 DB/Kafka bundle | 불필요 | `p1ctl init` 생성 파일 |
| 공식 offline release | 불필요 | digest-pinned `release.env` |
| GHCR pull | 금지 | 현재 shell의 `GHCR_READ_TOKEN` |
| KIS_MOCK v3 | 별도 필요 | owner-only mode 0600 루트 `.env`와 exact packet |

```bash
cp .env.example .env
```

`.env`에는 PostgreSQL 역할별 비밀번호, JWT secret/issuer/audience, 목적별 HMAC·gRPC secret,
demo credential bundle을 채운다. secret은 32 bytes 이상의 서로 다른 랜덤 값으로 만들고, BCrypt의
`$`가 포함된 credential bundle은 예시처럼 single quote로 감싼다. API key와 계좌 식별자는 넣지 않는다.
`CHANGE_ME_*` 값은 복사 후 반드시 모두 서로 다른 값으로 교체한다. 예를 들어
`openssl rand -hex 32`로 password/HMAC/gRPC secret을 만들 수 있다. capability digest와 DSN은 원본
값에서 파생한다.

```bash
openssl rand -hex 32 | sha256sum # historical migration placeholder; runtime bearer is retired
printf 'ASYNC_WORKER_DATABASE_DSN=postgresql://decision_worker:%s@127.0.0.1:5432/trading?sslmode=disable\n' \
  "$POSTGRES_WORKER_PASSWORD"
```

account binding, approval ID/SHA, provider credential은 승인된 authoring 절차가 만든 실제 값만 넣고 빈
예시를 임의로 채우지 않는다. `DEMO_*_CREDENTIAL_BUNDLE`은
`./gradlew prepareOpenApiFixtureEnv` 또는 `p1ctl init`의 기존 generator로만 만들며 수동 문자열 조립을
하지 않는다.

### P1 자동 생성 환경 inventory

아래 block은 `p1ctl` 및 secret entrypoint와 회귀 테스트로 exact 일치시킨다.

<!-- P1_RUNTIME_ENV_KEYS_BEGIN -->
```text
P1_SECRETS_DIR
P1_SECRET_GID
P1_API_PORT
P1_SPRING_IMAGE
P1_PYTHON_IMAGE
P1_POSTGRES_IMAGE
P1_KAFKA_IMAGE
```
<!-- P1_RUNTIME_ENV_KEYS_END -->

<!-- P1_RELEASE_ENV_KEYS_BEGIN -->
```text
P1_SPRING_IMAGE
P1_PYTHON_IMAGE
P1_POSTGRES_IMAGE
P1_KAFKA_IMAGE
```
<!-- P1_RELEASE_ENV_KEYS_END -->

<!-- P1_POSTGRES_ENV_KEYS_BEGIN -->
```text
POSTGRES_PASSWORD
POSTGRES_APP_PASSWORD
POSTGRES_MIGRATION_PASSWORD
POSTGRES_COLLECTOR_PASSWORD
POSTGRES_DISCLOSURE_READER_PASSWORD
POSTGRES_MARKET_WRITER_PASSWORD
POSTGRES_PORTFOLIO_WRITER_PASSWORD
POSTGRES_RISK_WRITER_PASSWORD
POSTGRES_FILL_WRITER_PASSWORD
POSTGRES_RAG_WRITER_PASSWORD
POSTGRES_RAG_ADMIN_PASSWORD
POSTGRES_RAG_QUERY_PASSWORD
POSTGRES_SIGNAL_WRITER_PASSWORD
POSTGRES_SIGNAL_SCHEDULER_PASSWORD
POSTGRES_SIGNAL_ADMIN_PASSWORD
POSTGRES_WORKER_PASSWORD
POSTGRES_OUTBOX_PUBLISHER_PASSWORD
POSTGRES_POISON_RECORDER_PASSWORD
POSTGRES_REPLAY_PASSWORD
POSTGRES_IDENTITY_PASSWORD
POSTGRES_AUTH_PASSWORD
POSTGRES_REPLAY_AUTHORIZER_PASSWORD
POSTGRES_DEMO_PASSWORD
```
<!-- P1_POSTGRES_ENV_KEYS_END -->

<!-- P1_ROLE_BOOTSTRAP_ENV_KEYS_BEGIN -->
```text
POSTGRES_ADMIN_USER
POSTGRES_PASSWORD
POSTGRES_AUTH_PASSWORD
POSTGRES_OUTBOX_PUBLISHER_PASSWORD
POSTGRES_POISON_RECORDER_PASSWORD
```
<!-- P1_ROLE_BOOTSTRAP_ENV_KEYS_END -->

<!-- P1_SPRING_ENV_KEYS_BEGIN -->
```text
POSTGRES_APP_PASSWORD
POSTGRES_WORKER_PASSWORD
POSTGRES_AUTH_PASSWORD
ACTOR_CAPABILITY_SHARED_SECRET
ACTOR_CAPABILITY_PUBLIC_KEY
REDIS_PASSWORD
JWT_SECRET
JWT_ISSUER
JWT_AUDIENCE
LOGIN_SCOPE_HMAC_KEY
PRINCIPLE_CURSOR_HMAC_KEY
DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY
DECISION_GRPC_SHARED_SECRET
BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY
RAG_IDEMPOTENCY_SCOPE_HMAC_KEY
RAG_REQUEST_FINGERPRINT_HMAC_KEY
RAG_PROVIDER_USAGE_HMAC_KEY
RAG_RATE_LIMIT_HMAC_KEY
RAG_HISTORY_CURSOR_HMAC_KEY
DEMO_CREDENTIAL_SEPARATION_KEY
DEMO_USER_CREDENTIAL_BUNDLE
DEMO_ADMIN_CREDENTIAL_BUNDLE
ASYNC_CURSOR_HMAC_KEY
ASYNC_PARTITION_HMAC_KEY
ASYNC_WORKER_GRPC_SHARED_SECRET
```
<!-- P1_SPRING_ENV_KEYS_END -->

<!-- P1_AUTHORITY_ENV_KEYS_BEGIN -->
```text
POSTGRES_IDENTITY_PASSWORD
ACTOR_CAPABILITY_SHARED_SECRET
ACTOR_CAPABILITY_PRIVATE_KEY
ACTOR_CAPABILITY_PUBLIC_KEY
```
<!-- P1_AUTHORITY_ENV_KEYS_END -->

<!-- P1_KAFKA_PUBLISHER_ENV_KEYS_BEGIN -->
```text
OUTBOX_PUBLISHER_DATABASE_DSN
KAFKA_SASL_USERNAME
KAFKA_SASL_PASSWORD
KAFKA_ENVELOPE_PRIVATE_KEY
```
<!-- P1_KAFKA_PUBLISHER_ENV_KEYS_END -->

<!-- P1_POISON_RECORDER_ENV_KEYS_BEGIN -->
```text
POISON_RECORDER_DATABASE_DSN
POISON_RECORDER_SHARED_SECRET
```
<!-- P1_POISON_RECORDER_ENV_KEYS_END -->

<!-- P1_KAFKA_ADMIN_ENV_KEYS_BEGIN -->
```text
KAFKA_SASL_USERNAME
KAFKA_SASL_PASSWORD
```
<!-- P1_KAFKA_ADMIN_ENV_KEYS_END -->

<!-- P1_MIGRATION_ENV_KEYS_BEGIN -->
```text
POSTGRES_MIGRATION_PASSWORD
DEMO_CREDENTIAL_SEPARATION_KEY
DEMO_USER_CREDENTIAL_BUNDLE
DEMO_ADMIN_CREDENTIAL_BUNDLE
BROKERAGE_DB_CAPABILITY_TOKEN_SHA256
```
<!-- P1_MIGRATION_ENV_KEYS_END -->

<!-- P1_SEED_IMPORT_ENV_KEYS_BEGIN -->
```text
P1_SEED_DATABASE_DSN
```
<!-- P1_SEED_IMPORT_ENV_KEYS_END -->

<!-- P1_BOOTSTRAP_ENV_KEYS_BEGIN -->
```text
POSTGRES_MIGRATION_PASSWORD
DEMO_CREDENTIAL_SEPARATION_KEY
DEMO_USER_CREDENTIAL_BUNDLE
DEMO_ADMIN_CREDENTIAL_BUNDLE
```
<!-- P1_BOOTSTRAP_ENV_KEYS_END -->

<!-- P1_PYTHON_ENV_KEYS_BEGIN -->
```text
ASYNC_WORKER_DATABASE_DSN
ASYNC_PARTITION_HMAC_KEY
ASYNC_WORKER_GRPC_SHARED_SECRET
KAFKA_SASL_USERNAME
KAFKA_SASL_PASSWORD
KAFKA_ENVELOPE_PUBLIC_KEY
POISON_RECORDER_URL
POISON_RECORDER_SHARED_SECRET
```
<!-- P1_PYTHON_ENV_KEYS_END -->

<!-- P1_DEMO_ENV_KEYS_BEGIN -->
```text
P1_DEMO_DATABASE_DSN
ASYNC_PARTITION_HMAC_KEY
```
<!-- P1_DEMO_ENV_KEYS_END -->

<!-- P1_REDIS_ENV_KEYS_BEGIN -->
```text
REDIS_PASSWORD
```
<!-- P1_REDIS_ENV_KEYS_END -->

mounted RAG KEK에서 `RAG_HISTORY_SECRET_DIRECTORY`와
`RAG_HISTORY_CURRENT_KEK_VERSION`을 파생한다. 운영자 override는 다음 exact 목록이다.

<!-- P1_OPERATOR_OVERRIDE_KEYS_BEGIN -->
```text
P1_STATE_DIR
P1_PROJECT_NAME
P1_API_PORT
P1_BACKUP_FILE
P1_SPRING_IMAGE
P1_PYTHON_IMAGE
P1_POSTGRES_IMAGE
P1_KAFKA_IMAGE
P1_RELEASE_STAGE
P1_RELEASE_EVIDENCE_DIR
```
<!-- P1_OPERATOR_OVERRIDE_KEYS_END -->

`P1_RELEASE_STAGE`, `P1_RELEASE_EVIDENCE_DIR`은 release maintainer 전용이다.
`deploy/p1/demo.env.example`은 복사하거나 source하는 입력이 아니라 generator 결과의 참고 형식이다.

P1 bundle은 다음 값을 항상 OFF로 고정하며 source `.env`나 operator override로 열 수 없다.

<!-- P1_FIXED_OFF_KEYS_BEGIN -->
```text
PROVIDER_LIVE_CALLS_ENABLED=false
KIS_MODE=mock
KIS_OFFLINE=1
KIS_MOCK_BROKERAGE_ONLINE_ENABLED=false
BROKERAGE_GRPC_ENABLED=false
RAG_GRPC_ENABLED=false
RAG_V2_GRPC_ENABLED=false
RAG_V2_VERTEX_ENABLED=false
RAG_WEB_ENABLED=false
S4_9_STRONG_LLM_ENABLED=false
S4_9_MCP_ENABLED=false
FINANCIAL_ENGINEERING_GRPC_ENABLED=false
```
<!-- P1_FIXED_OFF_KEYS_END -->

private GHCR에는 Spring, Python, PostgreSQL pgvector 세 package를 게시한다.

```text
ghcr.io/robinhood0107/capstone-spring-api
ghcr.io/robinhood0107/capstone-python-services
ghcr.io/robinhood0107/capstone-postgres-pgvector
```

`GHCR_READ_TOKEN`은 `.env`나 파일에 저장하지 않고 현재 shell에서만 받아 stdin으로 전달한다. tag가
아닌 검증된 digest reference로 세 package를 pull한다.

```bash
printf '%s' "$GHCR_READ_TOKEN" | docker login ghcr.io -u '<GITHUB_USER>' --password-stdin
docker pull 'ghcr.io/robinhood0107/capstone-spring-api@sha256:<DIGEST>'
docker pull 'ghcr.io/robinhood0107/capstone-python-services@sha256:<DIGEST>'
docker pull 'ghcr.io/robinhood0107/capstone-postgres-pgvector@sha256:<DIGEST>'
unset GHCR_READ_TOKEN
```

## 5분 P1 DB bundle 실행

최종 `P1_OFFLINE_DEMO` bundle에서는 repository-wide `.env`를 직접 조립하지 않는다. 신뢰하는 host에서
checksum과 attestation을 먼저 확인한 뒤 전용 운영 도구를 사용한다.

```bash
cd deploy/p1
./verify-release
./p1ctl init
./p1ctl verify
./p1ctl up db
./p1ctl smoke
```

`p1ctl init`은 application image가 준비된 새 state에서만 동작하며 secret directory는 0700, 각
secret file은 0640으로 생성한다. 서비스마다 필요한 파일만 mount하고, 현재 host group만 보조 group으로
부여하므로 container는 root로 전환하지 않는다.
기존 state나 volume을 reset하거나 덮어쓰지 않는다. 개발용 source 실행이 필요한 경우에만 아래 절차를
사용한다.

## P1 full-app v2 공통 설치 진입점

Linux/WSL은 repository root의 `./capstone`, Windows PowerShell은 `capstone.ps1`을 사용한다. 두
진입점은 `install`, `start`, `stop`, `status`, `doctor`, `backup`, `restore`, `verify`를 같은 의미로
전달한다. 기본 lane은 full-app이며 공개 Seed 조각 또는 Team B real artifact가 없으면 중단
단계를 0600 local state에 기록하고 fail-closed한다. BGE-M3는 공식 Hugging Face TEI CPU
이미지에서 `BAAI/bge-m3` exact revision을, PaddleOCR-VL은 공식 `llama.cpp` server 이미지에서
PaddlePaddle의 공식 GGUF/mmproj를 받아 named volume에 cache한다. 모델 서비스 host port는 0개다.
현재 누락 artifact를 우회해 full
release로 판정하는 옵션은 없다.

```bash
./capstone doctor
./capstone install
./capstone start
```

명시적인 `--degraded`는 기존 provider-free Core 회귀만 설치·실행한다. 이 모드는 full-app hard gate나
release authority를 만들지 않는다. Seed용 migration DSN은 별도 Docker secret으로 생성되며 실제 full
lane의 one-shot 의존성은 `migrate -> seed-import -> identity-bootstrap` 순서를 강제한다. `restore`는
G7의 원자적 교체 구현 전까지 명시적으로 차단된다.

## 개발용 DB 기본 실행

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml up -d --wait postgres redis
docker compose --env-file .env -f infra/docker-compose.infra.yml run --rm role-bootstrap

cd workspaces/decision-platform/python-services
uv sync --frozen
uv run --frozen python -m app.async_worker.grpc_server
```

다른 터미널에서 Spring API를 시작한다.

```bash
cd workspaces/decision-platform/spring-api
set -a
source ../../../.env
set +a
./gradlew bootRun
```

정상 기대 결과는 PostgreSQL/Redis가 healthy이고 Spring이 V1부터 현재 migration까지 적용된 뒤
`ASYNC_ADAPTER=db`, 명시적 polling/worker 설정으로 기동하는 것이다. 기존 volume에서는 init script가
자동 재실행되지 않으므로 migration 전후 역할 bootstrap을 다시 실행해 exact privilege를 복구한다.
이 bootstrap은 기존 `V6/V9/V14` 경계와 sanitized fill append 전용 `decision_fill_writer`를 포함하며,
해당 역할에 주문·사용자·Flyway DML 권한을 부여하지 않는다.

## Kafka 선택 실행과 DB 복귀

P1 bundle에서는 adapter 전환을 다음처럼 수행한다.

```bash
cd deploy/p1
./p1ctl stop
./p1ctl up kafka
./p1ctl smoke

./p1ctl stop
./p1ctl up db
./p1ctl smoke
```

DB/Kafka bundle은 동일한 Spring/Python image digest를 사용한다. Compose의 digest reference가 실행
권위이며 tag만으로 배포하지 않는다. 아래 명령은 개발용 source Compose 설명이다.

Kafka는 현재 build에서 로컬 numeric-loopback PLAINTEXT만 지원한다. TLS/service identity/topic·group ACL
구현은 별도 승인된 deploy 변경으로 남아 있으므로 `deploy`, non-loopback, `SSL`/`SASL_SSL` 설정은 시작 전에 거부된다.

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml \
  --profile kafka up -d kafka kafka-topic-init
```

topic initializer가 성공한 뒤 Spring을 `ASYNC_ADAPTER=kafka`로 재기동한다. producer/consumer를 먼저
quiesce하고 RUNNING lease와 unacked 작업이 없는지 확인한 뒤 정확히 한 adapter만 활성화한다. silent
fallback과 DB/Kafka dual-active는 금지된다.

DB로 돌아갈 때는 같은 순서로 quiesce한 뒤 `ASYNC_ADAPTER=db`로 재기동한다. Kafka profile이 떠 있어도
DB adapter 선택에는 영향을 주지 않는다.

아래 Kafka UI는 개발용 infra profile이다. P1 offline bundle에는 현재 후보 이미지의 High 취약점 때문에
포함하지 않으며 `P1_KAFKA_UI=DEFERRED_SECURITY_GATE`로 둔다. 개발용 UI도 `127.0.0.1` bind와
login-form credential이 필수다.

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml \
  --profile kafka --profile kafka-ui up -d kafka-ui
```

## 로그인과 토큰

demo 계정의 plaintext 비밀번호는 repository에 없다. `.env`의 attested credential bundle을 만든
운영자만 해당 비밀번호를 사용한다.

```bash
curl -sS -X POST http://127.0.0.1:8080/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo-user","password":"<LOCAL_PASSWORD>"}'
```

응답의 access token은 shell history나 파일에 저장하지 말고 현재 terminal의 짧은 수명 변수로만
사용한다. Admin status API는 JWT의 ADMIN claim만 믿지 않고 DB의 현재 `ACTIVE`, role,
`securityVersion`을 다시 확인한다.

## S0부터 P1까지 무엇을 쓰는가

| 단계 | 기능 | 대표 확인 방법 | 현재 제한 |
|---|---|---|---|
| S0 | health, envelope, JWT, Flyway | `./gradlew test` | 운영 배포 아님 |
| S1 | 시장자료·금융공학·품질 | Python 전체 pytest | provider 실행은 별도 승인 |
| S2 | Principle, Decision, Risk, Kill Switch | `/api/v1/principles`, `/api/v1/decisions`, `/api/v1/risk` | 저장 근거가 없으면 HOLD |
| S3 | KIS_MOCK/INTERNAL_PAPER ledger | mock brokerage API | live order 0 |
| S4 | owner-scoped RAG | `/api/v1/rag/ask`, history | public corpus 재색인 금지 |
| S5 | Signal v2와 연구 model | `GET /api/v2/signals/{symbol}` | LightGBM production 비활성 |
| S6 | 금융공학 리포트 | 저장된 S6 projection | cross-market runtime 퇴역 |
| S7 | DB/Kafka async, metrics, replay | Admin async/metric API와 통합 테스트 | Kafka는 선택 사항 |
| S8 | synthetic E2E, ViewModel, offline demo | 아래 demo runner | 실제 Return Engine artifact 없음 |
| P1 | Core release와 외부 종결 | [공개 상태 권위](docs/README.md#현재-상태) | Core와 전체 P1 상태를 분리 |

## 15분 offline demo

demo는 기존 개발 DB/RAG namespace를 건드리지 않고 `capstone-s8-demo` Compose project와 synthetic
ID만 사용한다. `INTERNAL_PAPER`를 정확히 선택하지 않으면 시작 전 거부한다.

```bash
workspaces/decision-platform/demo/s8/run-demo.sh \
  --prepare --adapter=db --brokerage-mode=INTERNAL_PAPER

workspaces/decision-platform/demo/s8/run-demo.sh \
  --prepare --adapter=kafka --brokerage-mode=INTERNAL_PAPER
```

중지는 volume을 보존한다.

```bash
workspaces/decision-platform/demo/s8/run-demo.sh \
  --stop --adapter=db --brokerage-mode=INTERNAL_PAPER
```

seed는 같은 bytes면 no-op, 다른 bytes면 conflict다. 결과는 `SYNTHETIC_FAKE_E2E`이고 실제 팀원 B
artifact나 투자 성과가 아니다.

## 자주 쓰는 API

```text
POST /api/v1/auth/login
GET  /api/v1/async-jobs
GET  /api/v1/async-jobs/{jobId}
GET  /api/v1/stream-metrics
GET  /api/v1/artifacts/ingest-status
GET  /api/v1/dashboard/model-evaluations/{runId}
GET  /api/v1/dashboard/backtests/{runId}
GET  /api/v1/dashboard/risk-results/{decisionId}
GET  /api/v1/dashboard/rag-sources/{answerId}
```

async/metric/artifact status는 ADMIN 전용이다. Dashboard risk/RAG는 owner scope를 적용하고 foreign ID는
404다. model/backtest demo projection은 인증된 demo namespace만 읽는다. cross-market endpoint는 없다.
정확한 request/response와 error envelope는 `contracts/openapi/openapi.json`을 따른다.

## 종료, 재시작, 문제 해결

P1 bundle의 정상 중지는 volume을 보존한다.

```bash
cd deploy/p1
./p1ctl status
./p1ctl logs --redacted
./p1ctl backup
./p1ctl restore-test
./p1ctl stop
```

`reset`, volume delete, credential rotation은 일반 명령으로 제공하지 않는다.

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml stop
docker compose --env-file .env -f infra/docker-compose.infra.yml ps
```

- `ASYNC_ADAPTER`가 잘못되면 startup failure가 정상이다.
- Kafka 선택 후 topic initializer가 실패하면 DB로 자동 fallback하지 않는다.
- async worker 연결 실패는 bounded retry 뒤 FAILED/NEEDS_REVIEW로 남는다.
- container 재기동 뒤 역할 권한이 다르면 `02-application-roles.sh`를 재실행한다.
- Testcontainers의 일시 연결 거부는 실패 클래스를 먼저 재실행하고 전체 gate로 다시 확인한다.
- volume 삭제, `git clean`, 강제 reset은 이 runbook에 포함되지 않는다.

## 현재 상태

현재 상태와 상태 용어의 단일 권위는 [문서 색인과 현재 상태](docs/README.md)다. Core `1.0.0`은
최종 보안·CI·merge·release 검증 전까지 `EXTERNAL_BLOCKED`이며, Core가 공개된 뒤에도 실제 Return
Engine artifact, Team A integration, fresh KIS_MOCK v3 reconciliation과 closure PR이 끝날 때까지 P1
전체는 `EXTERNAL_BLOCKED`다. provider/account/order 호출은 별도 승인 전까지 0이다.

## 상세 문서

- [최종 프로젝트 명세서](docs/최종_프로젝트_명세서.md)
- [API 명세서](docs/API_명세서.md)
- [S7–S8/P1 구현·운영 handoff](docs/decision-platform/S7_S8_P1_구현_및_운영_핸드오프.md)
- [P1 실물 artifact 잔여 checklist](docs/decision-platform/P1_실물_artifact_잔여_체크리스트.md)
- [S8 offline demo 시나리오](docs/decision-platform/S8_오프라인_시연_시나리오.md)
- [S8 사용자 테스트 kit](docs/decision-platform/s8-user-test-kit/README.md)
- [P1 Offline Demo 배포·검증](docs/decision-platform/P1_OFFLINE_DEMO_배포_및_검증.md)
- [계약과 검증 방법](contracts/README.md)
- [Decision Platform 개발 문서](workspaces/decision-platform/README.md)
- [작업·보안 규칙](AGENTS.md)

## 라이선스

이 프로젝트의 자체 작성 코드는 [GNU Affero General Public License v3.0](LICENSE),
SPDX 식별자 `AGPL-3.0-only`에 따라 배포한다. 제3자 구성 요소와 데이터에는 각각의 별도
라이선스와 이용 조건이 적용된다.
