# Capstone-AI-Trading-Coach

투자 원칙 기반 AI 트레이딩 코치 — 졸업과제 모노레포.

> 이 레포는 개인(박종진, `robinhood0107`) 계정에 먼저 생성되었습니다. 팀 공용 레포로 전환 시 원격만 옮기면 되도록 구조는 [최종 프로젝트 명세서](docs/최종_프로젝트_명세서.md) 6장의 모노레포 설계를 그대로 따릅니다.

## 에이전트/CI 규칙

작업 전 [AGENTS.md](AGENTS.md)를 먼저 확인한다. 현재 GitHub Actions는 Repo Hygiene,
Contracts CI, Kotlin Build, Python CI, S1.4X contract correctness를 수행한다. **어느 세션
완료 시점에 어떤 CI job을 추가하는지는 AGENTS.md의 "CI 로드맵" 표를 따른다**.

## 현재 구현 상태

STAGE 2에서 S1.5 KIS Data Quality Report CLI까지 `main`에 병합됐고, S1.6 선행 amendment
PR #34도 병합됐다. 후속 변경은 Market Calendar/Event Aggregator의 strict registry,
offline adapters, Flyway V6 저장소, 최소권한 collector role, quota/retry/privacy/state 계약을
fixture-first로 구현한다. provider online 활성화와 공개 REST/gRPC/Dashboard는 포함하지 않는다.

- PR #16 merge commit: `6f439155d9f5ec626fc185f29f2e0bd64ca54780`
- PR #17 merge commit 및 S1.3/S1.3K 기능 완료 기준점: `814aab377251d76672566d39c3edb379d132248e`
- PR #28 S1.4X Gate 1, PR #30 모델 위험 계약, PR #32 S1.5 Data Quality Report까지 병합
- PR #34 S1.6 prerequisite merge commit: `5f537857a1b57c5b8321f70d8df292a851514b2d`
- S1.6 후속 변경의 검증 범위: Python calendar/OpenDART 회귀, PostgreSQL 16 Testcontainers,
  JDK 25 Flyway/권한, contracts, S1.4X correctness, repo hygiene와 secret/PII scan

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
S1.4 금융공학, S1.5 품질 보고와 S1.6 내부 offline calendar/event aggregator까지 구현되어
있다. 상세 개인 참고 노트는 GitHub에 올리지 않고 로컬 `private-reference/` 폴더에서만 관리한다.

```bash
cp .env.example .env
# DB/Redis password, JWT issuer/audience, 목적별 signing/HMAC key와 두 attested demo credential bundle을 채운다.
# bundle은 $ 포함 BCrypt hash 보존을 위해 single quote 안에 두며 plaintext demo password는 저장하지 않는다.
# API key는 필요한 provider를 실제 호출할 때 운영자만 주입하며 커밋하지 않는다.
docker compose --env-file .env -f infra/docker-compose.infra.yml up -d
docker compose --env-file .env -f infra/docker-compose.infra.yml ps
docker compose --env-file .env -f infra/docker-compose.infra.yml exec postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dx"'

cd workspaces/decision-platform/spring-api
./gradlew tasks
./gradlew build
```

PostgreSQL runtime은 `decision_app`, S1.6 수집은 `decision_collector`, migration은 `flyway`,
bootstrap 관리는 `POSTGRES_ADMIN_USER`로 분리된다. 기존 `pgdata` volume에는 init script가
자동 재실행되지 않으므로, 기존 관리자 이름/비밀번호를 보존하고
`POSTGRES_COLLECTOR_PASSWORD`를 추가해 컨테이너를 올린 뒤 다음 명령을 한 번 실행한다.
V6 적용 전에는 collector role을 먼저 만들고, V6 적용 뒤 재실행해도 script가 exact calendar
권한을 복원한다. volume 삭제는 이 절차에 포함하지 않는다.

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
- S1.4X dependency amendment 재현: `workspaces/decision-platform/research/s1-4x-numeric-parity/README.md`
