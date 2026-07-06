# Capstone-AI-Trading-Coach

투자 원칙 기반 AI 트레이딩 코치 — 졸업과제 모노레포.

> 이 레포는 개인(박종진, `robinhood0107`) 계정에 먼저 생성되었습니다. 팀 공용 레포로 전환 시 원격만 옮기면 되도록 구조는 [최종 프로젝트 명세서](docs/최종_프로젝트_명세서.md) 6장의 모노레포 설계를 그대로 따릅니다.

## 에이전트/CI 규칙

작업 전 [AGENTS.md](AGENTS.md)를 먼저 확인한다. 현재 GitHub Actions는 repo hygiene 중심의 비파괴 검증만 수행한다. **어느 세션 완료 시점에 어떤 CI job을 추가하는지는 AGENTS.md의 "CI 로드맵" 표를 따른다** (kotlin-build는 S0.3, python-ci는 S1.4, contracts-ci는 S0.2, OpenAPI diff는 S2.1 등).

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

현재 레포는 초기 공개 스캐폴드 단계다. 상세 개인 참고 노트는 GitHub에 올리지 않고 로컬 `private-reference/` 폴더에서만 관리한다.

```bash
docker compose -f infra/docker-compose.infra.yml up -d
cp .env.example .env   # 값 채우기
cd workspaces/decision-platform/spring-api && ./gradlew bootRun
# 주의: gradle wrapper(gradlew)는 아직 커밋 전이다. S0.3(Initializr 생성물 반입) 후부터 동작한다.
#       그 전에는 IntelliJ Gradle sync 또는 로컬 설치 gradle로 확인한다.
```

## 문서

- [최종 프로젝트 명세서](docs/최종_프로젝트_명세서.md)
- [API 명세서](docs/API_명세서.md)
