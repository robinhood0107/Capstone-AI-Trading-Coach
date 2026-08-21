# Capstone AI Trading Coach

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
- 알 수 없는 adapter, 준비되지 않은 Kafka, non-loopback PLAINTEXT는 fail-closed다.
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
- Python 3.13과 `uv`
- Git

환경 파일은 커밋하지 않는다. 처음 한 번 다음처럼 준비한다.

```bash
cp .env.example .env
```

`.env`에는 PostgreSQL 역할별 비밀번호, JWT secret/issuer/audience, 목적별 HMAC·gRPC secret,
demo credential bundle을 채운다. secret은 32 bytes 이상의 서로 다른 랜덤 값으로 만들고, BCrypt의
`$`가 포함된 credential bundle은 예시처럼 single quote로 감싼다. API key와 계좌 식별자는 넣지 않는다.

## 5분 DB 기본 실행

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml up -d postgres redis
docker compose --env-file .env -f infra/docker-compose.infra.yml exec -T postgres \
  bash /docker-entrypoint-initdb.d/02-application-roles.sh

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

Kafka는 로컬 loopback 개발에서만 PLAINTEXT를 허용한다.

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml \
  --profile kafka up -d kafka kafka-topic-init
```

topic initializer가 성공한 뒤 Spring을 `ASYNC_ADAPTER=kafka`로 재기동한다. producer/consumer를 먼저
quiesce하고 RUNNING lease와 unacked 작업이 없는지 확인한 뒤 정확히 한 adapter만 활성화한다. silent
fallback과 DB/Kafka dual-active는 금지된다.

DB로 돌아갈 때는 같은 순서로 quiesce한 뒤 `ASYNC_ADAPTER=db`로 재기동한다. Kafka profile이 떠 있어도
DB adapter 선택에는 영향을 주지 않는다.

Kafka UI는 별도 `kafka-ui` profile이며 `127.0.0.1` bind와 login-form credential이 필수다.

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
| P1 | 통합 검증 | P1 잔여 checklist | external artifact 때문에 미완료 |

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

## 현재 정직한 제한

```text
S8_1_FAKE_E2E_VERIFIED
S8_1_REAL_ARTIFACT_BLOCKED
P1_OVERALL=INCOMPLETE_EXTERNAL_ARTIFACT
TEAM_A_INTEGRATED=FALSE
S1_4_PYTHON_DEFAULT_VERIFIED_NOT_RUNTIME_SWITCHABLE
S1_4X_ENTRY_BLOCKED_S8_1_REAL_ARTIFACT
```

실제 Return Engine artifact가 없으므로 real ingest, 독립 재계산, Baseline/Guide/Strict 허용오차 표와
P1 전체 완료를 주장하지 않는다. 사용자 테스트 kit는 준비됐지만 참가자 모집·응답·IRB 판단은
실행하지 않았다. provider/live/account/live-order physical call은 S7/S8 구현 과정에서 0건이다.

## 상세 문서

- [최종 프로젝트 명세서](docs/최종_프로젝트_명세서.md)
- [API 명세서](docs/API_명세서.md)
- [S7–S8/P1 구현·운영 handoff](docs/decision-platform/S7_S8_P1_구현_및_운영_핸드오프.md)
- [P1 실물 artifact 잔여 checklist](docs/decision-platform/P1_실물_artifact_잔여_체크리스트.md)
- [S8 offline demo 시나리오](docs/decision-platform/S8_오프라인_시연_시나리오.md)
- [S8 사용자 테스트 kit](docs/decision-platform/s8-user-test-kit/README.md)
- [계약과 검증 방법](contracts/README.md)
- [Decision Platform 개발 문서](workspaces/decision-platform/README.md)
- [작업·보안 규칙](AGENTS.md)
