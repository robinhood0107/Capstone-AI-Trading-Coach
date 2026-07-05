# AGENTS.md

## 적용 범위

이 파일은 레포 전체에 적용된다. 더 깊은 경로에 별도 `AGENTS.md`가 추가되기 전까지 모든 에이전트와 사람 작업자는 이 규칙을 따른다.

## 기준 문서 우선순위

작업 전 아래 문서를 먼저 확인한다. 충돌이 있으면 위쪽 문서가 우선한다.

1. `docs/최종_프로젝트_명세서.md`
2. `docs/API_명세서.md`
3. 공개 문서에 명시된 계약과 workspace 경계

개인 참고 문서는 `private-reference/`에만 둔다. 이 폴더는 `.gitignore` 대상이며 GitHub 커밋에 포함하지 않는다.

## 현재 단계

현재 레포는 GitHub 초기 환경 세팅 단계다.

- 런타임 기능 구현을 새로 추가하지 않는다.
- Gradle wrapper, `uv.lock`, Spring/Python 실제 health 구현, full build CI는 S0.1 walking skeleton 이후 추가한다.
- 지금 허용되는 변경은 repo hygiene, GitHub 템플릿, 규칙 파일, README 수준의 초기 설정이다.

## 워크스페이스 경계

- `workspaces/decision-platform/`: 박종진(`robinhood0107`) 담당. 이 개인 레포에서 실제 구현할 수 있는 영역이다.
- `workspaces/return-engine/`: 팀원 B 담당. 현재는 placeholder이며, 이 레포에서는 `README.md` 외 구현 파일을 만들지 않는다.
- `workspaces/experience-dashboard/`: 팀원 A 담당. 현재는 placeholder이며, 이 레포에서는 `README.md` 외 구현 파일을 만들지 않는다.
- `contracts/`: workspace 간 계약의 단일 진실 소스다. 변경 시 `contracts/changes/`에 이유와 영향 범위를 남긴다.
- `artifacts/`: 계약을 만족하는 산출물 교환 폴더다. 원본 코드, 대용량 원시 데이터, 로컬 실행 산출물은 커밋하지 않는다.

## 보안과 비밀값

- `.env`, `.env.local`, `*.env`, `http-client.private.env.json`은 커밋하지 않는다.
- 커밋 가능한 환경 파일은 `.env.example`뿐이다.
- `private-reference/` 아래 파일은 커밋하지 않는다.
- API key, JWT secret, 계좌번호, 토큰, 주문/잔고 원본 로그는 코드, 문서, 테스트 fixture에 넣지 않는다.
- 로그 예시가 필요하면 값은 반드시 마스킹한다.
- 커밋 전 gitleaks 또는 GitHub `repo-hygiene` workflow로 secret scan을 통과해야 한다.

## Git과 GitHub 규칙

- 기본 브랜치는 `main`이다.
- 작업 브랜치 이름은 `feature/*`, `fix/*`, `docs/*`, `infra/*`, `experiment/*`를 사용한다.
- 커밋 메시지는 `<type>(<session>): 요약` 형식을 권장한다. 예: `chore(S0): repo hygiene 설정`.
- PR에는 문서/API/계약 변경 여부, secret 포함 여부, 다른 팀원 workspace 수정 여부를 명시한다.
- 현재 CI는 비파괴 repo hygiene만 수행한다. Kotlin/Python 전체 빌드 CI는 S0.1 이후 추가한다.

## gstack / Codex / Claude 사용 규칙

- 사용자가 gstack workflow(`/office-hours`, `/autoplan`, `/plan-eng-review`, `/review`, `/qa`, `/ship`, `/investigate`, `/cso`, `/document-release`, `/gstack-upgrade` 등)를 요청하면 관련 gstack skill을 먼저 고려한다.
- 웹 QA나 브라우저 작업에서 사용자가 gstack을 명시하면 gstack `/browse` 또는 `/qa` 흐름을 우선한다.
- 사용자가 Codex native browser, web search, connector, app-specific tool을 명시하면 그 명시를 우선한다.
- Claude 전용 설정(`.claude` hook, Claude-only MCP, Claude-only command)은 사용자가 별도로 요청하기 전까지 추가하지 않는다.
- `CLAUDE.md`는 이 파일을 따르는 짧은 연결 문서로만 유지한다.

## 작업 방식

- 파일을 만들거나 수정하기 전 현재 상태를 확인한다.
- 이미 있는 사용자 변경은 되돌리지 않는다.
- 파괴적 명령(`git reset --hard`, broad delete, force push 등)은 사용자가 명확히 요청하지 않으면 실행하지 않는다.
- 구현 작업을 시작하기 전에는 관련 명세와 workspace 경계를 확인한다.
