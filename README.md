# Capstone-AI-Trading-Coach

투자 원칙 기반 AI 트레이딩 코치 — 졸업과제 모노레포.

> 이 레포는 개인(박종진, `robinhood0107`) 계정에 먼저 생성되었습니다. 팀 공용 레포로 전환 시 원격만 옮기면 되도록 구조는 [최종 프로젝트 명세서](docs/최종_프로젝트_명세서.md) 6장의 모노레포 설계를 그대로 따릅니다.

## 에이전트/CI 규칙

작업 전 [AGENTS.md](AGENTS.md)를 먼저 확인한다. 현재 GitHub Actions는 Repo Hygiene,
Contracts CI, Kotlin Build, Python CI, S1.4X contract correctness를 수행한다. **어느 세션
완료 시점에 어떤 CI job을 추가하는지는 AGENTS.md의 "CI 로드맵" 표를 따른다**.

## 현재 구현 상태

STAGE 2에서 S1.6 Market Calendar/Event Aggregator offline 구현, S2.1 Principle CRUD,
S2.2 offline evaluator와 S2.3 Decision runtime까지 `main`에 병합됐다. 이 S2.4 변경은
owner-scoped portfolio Risk 조회와 DB-authoritative 전역 Kill Switch를 추가한다. Kill Switch
활성화는 유효한 Decision을 append-only 방식으로 무효화하고 신규 평가를 fail-closed하며,
재가동은 transaction 안에서 현재 ADMIN 권한을 다시 확인한다. portfolio freshness는 실제 저장
현재가·잔고·결정적 risk observation만 사용하고 없는 source를 0이나 false로 꾸미지 않는다.
S2.4의 KIS/broker/gRPC/외부 HTTP와 주문 호출은 모두 0건이다.

- PR #16 merge commit: `6f439155d9f5ec626fc185f29f2e0bd64ca54780`
- PR #17 merge commit 및 S1.3/S1.3K 기능 완료 기준점: `814aab377251d76672566d39c3edb379d132248e`
- PR #28 S1.4X Gate 1, PR #30 모델 위험 계약, PR #32 S1.5 Data Quality Report까지 병합
- PR #34 S1.6 prerequisite merge commit: `5f537857a1b57c5b8321f70d8df292a851514b2d`
- PR #35 S1.6 offline 구현, PR #39 S2.1 계약 amendment, PR #41 S2.1 Principle CRUD까지 병합
- PR #43 S2.2 offline Rule Evaluator와 owner-scoped Principle read adapter 병합
- PR #45 S2.3 Decision API/V9 persistence와 stored-source runtime 병합
- S2.2 검증 범위: Kotlin evaluator/portfolio/hash/readiness 회귀, PostgreSQL 16 Testcontainers,
  generated contract와 OpenAPI drift gate, repo hygiene와 secret scan

완료된 A4/B1/KRX11 approval packet은 재사용하지 않는다. 이후 실제 provider 호출은 새 HEAD·명령·기준일·호출 예산·TTL에 결속한 별도 승인 뒤에만 실행한다.

## 워크스페이스 소유권

| 경로 | 담당 | 상태 |
|---|---|---|
| `workspaces/decision-platform/` | 박종진 (`robinhood0107`) | 이 계정에서 실제 구현 진행 |
| `workspaces/return-engine/` | 팀원 B | 자리만 확보 — 병합 전까지 placeholder |
| `workspaces/experience-dashboard/` | 팀원 A | 자리만 확보 — 병합 전까지 placeholder |
| `contracts/` | 박종진 초안, 전원 합의 | 스키마/API 변경 시 `contracts/changes/`에 기록 |
| `artifacts/` | 전원 | 계약된 산출물만 저장, 원본 코드 금지 |
| `infra/` | 공통 | Docker Compose / CI |
| `docs/` | 공통 | 명세서, ADR, 보고서 |

## 시작하기 (decision-platform)

현재 레포는 STAGE 2이며 Decision Platform의 S0 walking skeleton, S1.1 KIS 시장데이터,
S1.2c OpenDART 분석 데이터, S1.3 ECOS/Naver snapshot, S1.3K KRX universe 자동화,
S1.4 금융공학, S1.5 품질 보고, S1.6 내부 offline calendar/event aggregator, S2.1 Principle
CRUD, S2.2 offline rule evaluator와 S2.3 Decision runtime까지 구현되어 있다. 이 S2.4 변경은
`GET /api/v1/risk/portfolio`, Kill Switch 조회·변경, V10 원자 전이·무효화·audit·outbox와
Decision guard를 추가한다. provider를 호출하거나 S3 주문 orchestration을 대신하지 않으며,
구조가 준비된 뒤 source row가 없거나 stale하면 nullable 값과 sanitized warning으로 응답한다.
상세 개인 참고 노트는 GitHub에 올리지 않고 로컬
`private-reference/` 폴더에서만 관리한다.

```bash
cp .env.example .env
# DB/Redis 및 collector/source-writer password, JWT issuer/audience, 목적별 signing/HMAC key와 두 attested demo credential bundle을 채운다.
# bundle은 $ 포함 BCrypt hash 보존을 위해 single quote 안에 두며 plaintext demo password는 저장하지 않는다.
# API key는 필요한 provider를 실제 호출할 때 운영자만 주입하며 커밋하지 않는다.
docker compose --env-file .env -f infra/docker-compose.infra.yml up -d
docker compose --env-file .env -f infra/docker-compose.infra.yml ps
docker compose --env-file .env -f infra/docker-compose.infra.yml exec postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dx"'

cd workspaces/decision-platform/spring-api
./gradlew tasks
./gradlew build
```

PostgreSQL runtime은 `decision_app`, S1.6 수집은 `decision_collector`, sanitized source append는
`decision_market_writer`/`decision_portfolio_writer`/`decision_risk_writer`, migration은 `flyway`,
bootstrap 관리는 `POSTGRES_ADMIN_USER`로 분리된다. 기존 `pgdata` volume에는 init script가
자동 재실행되지 않으므로, 기존 관리자 이름/비밀번호를 보존하고 `.env.example`의 collector와
세 source-writer password를 추가해 컨테이너를 올린 뒤 다음 명령을 한 번 실행한다. V6/V9 적용
전에는 role을 먼저 만들고, migration 뒤 재실행하면 현재 table의 exact 권한을 복원한다. volume
삭제는 이 절차에 포함하지 않는다.

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml exec -T postgres \
  bash /docker-entrypoint-initdb.d/02-application-roles.sh
```

S1.6 OpenDART online collector는 `.env.example`의 네 quota 값을 운영 evidence에 맞게 모두
명시해야 하지만, 설정만으로 활성화되지 않는다. 현재 구현은 offline fixture와 mock transport로만
검증됐으며 KIS/KASI/OpenDART provider 호출, 운영 DB 배포와 collector schedule은 별도 승인 대상이다.

## 문서

- [최종 프로젝트 명세서](docs/최종_프로젝트_명세서.md)
- [API 명세서](docs/API_명세서.md)
- S1.6 내부 계약: 최종 명세 11.1.2와 API 명세 12A
- [S2.2 offline 계약과 재현 명령](contracts/README.md#s22-rule-evaluation-offline-contract-v1)
- [S2.2 계약 변경 기록](contracts/changes/20260724-s2-2-rule-evaluation-offline-contract.md)
- [S2.3 Decision runtime과 stored-source 경계](contracts/README.md#s23-decision-runtime과-stored-source-경계)
- [S2.3 Decision 계약 잠금](contracts/changes/20260724-s2-3-decision-contract-lock.md)
- [S2.4 Risk와 Kill Switch 계약](contracts/README.md#s24-risk-api와-kill-switch)
- [S2.4 계약 변경 기록](contracts/changes/20260725-s2-4-risk-kill-switch-contract.md)
- S1.4X dependency amendment 재현: `workspaces/decision-platform/research/s1-4x-numeric-parity/README.md`
