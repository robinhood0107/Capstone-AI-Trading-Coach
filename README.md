# Capstone-AI-Trading-Coach

투자 원칙 기반 AI 트레이딩 코치 — 졸업과제 모노레포.

> 이 레포는 개인(박종진, `robinhood0107`) 계정에 먼저 생성되었습니다. 팀 공용 레포로 전환 시 원격만 옮기면 되도록 구조는 [최종 프로젝트 명세서](docs/최종_프로젝트_명세서.md) 6장의 모노레포 설계를 그대로 따릅니다.

## 에이전트/CI 규칙

작업 전 [AGENTS.md](AGENTS.md)를 먼저 확인한다. 현재 GitHub Actions는 Repo Hygiene, Contracts CI, Kotlin Build를 수행하며 Python CI는 S1.4 완료 시 추가한다. **어느 세션 완료 시점에 어떤 CI job을 추가하는지는 AGENTS.md의 "CI 로드맵" 표를 따른다**.

## 현재 구현 상태

STAGE 2에서 S1.3 ECOS/Naver 내부 source snapshot과 S1.3K KRX OPEN API universe 자동화까지 `main`에 병합됐다.

- PR #16 merge commit: `6f439155d9f5ec626fc185f29f2e0bd64ca54780`
- PR #17 merge commit 및 S1.3/S1.3K 기능 완료 기준점: `814aab377251d76672566d39c3edb379d132248e`
- 최종 검증: KRX focused `313 passed`, S1.3 data matrix `892 passed`, 전체 Python `1086 passed`, Ruff, mypy, contracts, JDK 25 Gradle build, Compose, repo hygiene, gitleaks, 독립 리뷰 통과

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

현재 레포는 STAGE 2이며 Decision Platform의 S0 walking skeleton, S1.1 KIS 시장데이터, S1.2c OpenDART 분석 데이터, S1.3 ECOS/Naver snapshot, S1.3K KRX universe 자동화까지 구현되어 있다. 상세 개인 참고 노트는 GitHub에 올리지 않고 로컬 `private-reference/` 폴더에서만 관리한다.

```bash
cp .env.example .env
# PostgreSQL 관리자/runtime/Flyway password, Redis password, JWT/demo password를 채운다.
# API key는 필요한 provider를 실제 호출할 때 운영자만 주입하며 커밋하지 않는다.
docker compose --env-file .env -f infra/docker-compose.infra.yml up -d
docker compose --env-file .env -f infra/docker-compose.infra.yml ps
docker compose --env-file .env -f infra/docker-compose.infra.yml exec postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dx"'

cd workspaces/decision-platform/spring-api
./gradlew tasks
./gradlew build
```

PostgreSQL runtime은 `decision_app`, migration은 `flyway`, bootstrap 관리는 `POSTGRES_ADMIN_USER`로 분리된다. 기존 `pgdata` volume에는 init script가 자동 재실행되지 않으므로, 기존 관리자 이름/비밀번호를 `POSTGRES_ADMIN_USER`/`POSTGRES_ADMIN_PASSWORD`로 보존해 컨테이너를 올린 뒤 다음 명령을 한 번 실행한다. volume 삭제는 이 절차에 포함하지 않는다.

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml exec -T postgres \
  bash /docker-entrypoint-initdb.d/02-application-roles.sh
```

## 문서

- [최종 프로젝트 명세서](docs/최종_프로젝트_명세서.md)
- [API 명세서](docs/API_명세서.md)
