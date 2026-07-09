# AGENTS.md

## 적용 범위

이 파일은 레포 전체에 적용된다. 더 깊은 경로에 별도 `AGENTS.md`가 추가되기 전까지 모든 에이전트와 사람 작업자는 이 규칙을 따른다.

## 기준 문서 우선순위

작업 전 아래 문서를 먼저 확인한다. 충돌이 있으면 위쪽 문서가 우선한다.

1. `docs/최종_프로젝트_명세서.md`
2. `docs/API_명세서.md`
3. 공개 문서에 명시된 계약과 workspace 경계

로컬 private notes는 `private-reference/`에만 둔다. 이 폴더는 `.gitignore` 대상이며 GitHub 커밋에 포함하지 않는다.

`private-reference/agent/`는 구현 세션용 로컬 보조 자료다. 에이전트는 사용자가 명시하거나 작업상 필요할 때만 이 폴더를 참고한다. `private-reference/study/CS개념/`은 사용자 학습용 자료이며 구현 입력이 아니다. 에이전트는 이 학습용 문서를 자동으로 읽지 않고, 관련 주제가 나왔을 때 사용자에게 읽어보라고 제안만 한다.

## 현재 단계

**현재 단계: STAGE 2 — 기능 구현 (S1~S8).** 단계가 바뀌면 이 절과 PR 템플릿을 함께 갱신한다(단계 전환 자체가 하나의 PR).

| 단계 | 허용되는 변경 | 전환 조건 |
|---|---|---|
| STAGE 0 | repo hygiene, GitHub 템플릿, 규칙 파일, README, 설정 스캐폴드, Java 25 LTS 스택 기준 정렬 | 로컬에서 작업계획 5.0.6 Definition of Ready(JDK 25 기준) 통과 |
| STAGE 1 — walking skeleton (S0.1~S0.4) | Gradle wrapper 9.5.0, `uv.lock`, Application/health 구현, Flyway V1, 공통 규약(envelope/JWT/idempotency) | S0.4 DoD 통과 |
| STAGE 2 — 기능 구현 (S1~S8) (현재) | 세션계획 8장의 세션 단위 구현. S1.1은 KIS 시장데이터/OAuth/cache/backfill 전용이며 주문·계좌 변경은 제외 | 세션별 DoD |

- STAGE 0에서는 런타임 기능 구현을 새로 추가하지 않는다.
- STAGE 2에서도 세션 범위를 넘겨 구현하지 않는다. S1.1에서는 KIS OAuth 토큰, 국내주식 현재가, 국내주식 기간별시세, 선택적 휴장일 조회 같은 읽기 전용 시장데이터만 다룬다.
- 어느 단계든 다른 팀원 workspace placeholder 경계와 `contracts/` 변경 절차는 동일하게 적용된다.

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
- KIS 원본 응답, 응답 헤더, access token, 계좌 식별자, raw/parquet/csv/jsonl 산출물은 커밋하지 않는다. 테스트에는 마스킹된 offline fixture만 둔다.
- KIS Live 시장데이터 조회 계획과 KIS Live 주문 기능은 분리한다. 실계좌 주문·정정·취소는 S3 이후 별도 live-order gate가 명시되기 전까지 기본 비활성이다.
- 로그 예시가 필요하면 값은 반드시 마스킹한다.
- 커밋 전 gitleaks 또는 GitHub `repo-hygiene` workflow로 secret scan을 통과해야 한다.

## Git과 GitHub 규칙

- 기본 브랜치는 `main`이다.
- 작업 브랜치 이름은 `feature/*`, `fix/*`, `docs/*`, `infra/*`, `experiment/*`를 사용한다.
- 커밋 메시지는 `<type>(<session>): 요약` 형식을 권장한다. 예: `chore(S0): repo hygiene 설정`.
- 커밋은 기능 단위로 작게 분리한다. 한 커밋에는 하나의 의도만 담고, 서로 다른 기능·버그·문서 정리는 같은 커밋에 섞지 않는다.
- 테스트 코드와 실제 구현 코드는 원칙적으로 별도 커밋으로 분리한다. 권장 순서는 `test(<session>): 실패/회귀 테스트 추가` → `feat|fix(<session>): 구현` → `docs|chore(<session>): 문서/설정 정리`다.
- Markdown/AGENTS/명세서/규칙 파일 변경은 코드 구현 커밋과 분리한다. 구현과 문서가 같은 세션에서 필요하더라도 리뷰자가 diff를 따로 볼 수 있게 별도 커밋으로 남긴다.
- 예외는 오타 수정, import 정리, 테스트 fixture 이름 변경처럼 해당 커밋의 코드가 없으면 테스트가 실행조차 되지 않는 기계적 동반 변경뿐이다. 예외를 쓰면 커밋 메시지나 PR 본문에 이유를 적는다.
- 커밋과 PR에 AI 도구(Claude, Codex 등)를 author/co-author로 표기하지 않는다. `Co-Authored-By` trailer와 AI 서명 footer를 넣지 않으며, contributors에는 사용자 계정만 남긴다.
- PR에는 문서/API/계약 변경 여부, secret 포함 여부, 다른 팀원 workspace 수정 여부를 명시한다.
- 모든 Issue와 PR 제목/본문은 한국어와 영어를 함께 작성한다. 최소한 `KR:`와 `EN:` 구역을 두어 같은 의도를 양쪽 언어로 확인 가능해야 한다.
- 서로 연관된 Issue, PR, commit은 GitHub 번호로 연결한다. PR 본문에는 `Closes #<issue>` 또는 `Refs #<issue>`를 쓰고, 해당 변경을 직접 수행한 commit 메시지에도 관련 번호(`#<issue>` 또는 `#<pr>`)를 포함한다.

## CI 로드맵 — 언제 무엇을 추가하는가

현재 CI는 `repo-hygiene.yml`(필수 경로/compose 검증/ignore 규칙/secret scan) 하나다. 아래 시점이 되면 **해당 세션의 DoD에 CI job 추가가 포함된 것으로 간주**하고 job을 늘린다. 각 job은 별도 workflow 파일로 추가한다(hygiene은 항상 유지).

| 추가 시점(세션) | 추가할 CI job | 내용 |
|---|---|---|
| S0.3 완료 시 | `kotlin-build.yml` | Gradle wrapper 9.5.0 커밋 후 JDK 25에서 `./gradlew ktlintCheck build` (test 포함, Testcontainers는 ubuntu 러너 Docker 사용) |
| S0.4 완료 시 | kotlin-build에 통합 | Flyway clean 마이그레이션 + unique 제약 Testcontainers 테스트가 test 단계에서 실행됨을 확인 |
| S1.4 완료 시 | `python-ci.yml` | `uv sync --frozen` + `uv run ruff check` + `uv run pytest` (uv.lock 커밋 필수) |
| S0.2 완료 시 | `contracts-ci.yml` | `contracts/examples/*` JSON Schema validation + negative test |
| S2.1(첫 컨트롤러) 완료 시 | contracts-ci에 통합 | springdoc `generateOpenApiDocs` 출력과 `contracts/openapi/` diff — 불일치 시 실패 (API 명세서 17.4) |
| S7.1 완료 시 | kotlin-build에 통합 | Testcontainers Kafka 통합 테스트(outbox publish/manual commit) 포함 확인 |
| M4 직전 | `demo-smoke.yml` (수동 트리거) | compose up → demo_seed → 핵심 E2E 3콜(로그인/원칙/평가) smoke |
| 시간 부족 시(단일 대비책) | — | 작업계획 5.10: contracts+secret scan만 CI에 남기고 kotlin/python은 로컬 pre-push 훅으로 강등 |

## gstack / Codex / Claude 사용 규칙

- 사용자가 gstack workflow(`/office-hours`, `/autoplan`, `/plan-eng-review`, `/review`, `/qa`, `/ship`, `/investigate`, `/cso`, `/document-release`, `/gstack-upgrade` 등)를 요청하면 관련 gstack skill을 먼저 고려한다.
- 웹 QA나 브라우저 작업에서 사용자가 gstack을 명시하면 gstack `/browse` 또는 `/qa` 흐름을 우선한다.
- 사용자가 Codex native browser, web search, connector, app-specific tool을 명시하면 그 명시를 우선한다.
- Claude 전용 설정(`.claude` hook, Claude-only MCP, Claude-only command)은 사용자가 별도로 요청하기 전까지 추가하지 않는다.
- `CLAUDE.md`는 이 파일을 따르는 짧은 연결 문서로만 유지한다.

## 구현 세션 운영

- 구현 또는 구현 계획 세션을 시작할 때는 로컬 agent 프라이머 문서를 먼저 확인한다. 이 프라이머의 문서 지도, 세션 프롬프트 템플릿, 과거 실수 기록을 사용해 사용자가 매번 같은 컨텍스트를 다시 설명하지 않게 한다.
- 프라이머 확인 뒤에는 현재 세션에 해당하는 로컬 세션별 작업계획 문서의 작업, DoD, 함정과 대비책을 확인한다. Decision/Risk/Order/RAG처럼 세부 설계가 필요한 작업은 프라이머의 문서 지도에 따라 상세 구현명세서, ADR, 권장 코딩패턴 예시집을 추가로 읽는다.
- 세션 운영 판단은 로컬 세션별 작업계획 문서 10장의 운영 팁을 따른다. 특히 S6.4(BSM/Greeks/IV)는 막힌 날 사용할 버퍼 세션으로 보고, Kafka 트랙은 S7.0+S7.3만으로 시연·계약·보고서가 성립한다는 기준 아래 매몰비용을 경계한다.
- `private-reference/study/CS개념/`은 구현 입력으로 자동 사용하지 않는다. 관련 개념이 나오면 사용자에게 읽을 문서를 추천하되, 사용자가 명시적으로 요구한 경우에만 구현 컨텍스트로 읽는다.
- 확정된 스택, 단계, 세션 운영 규칙, 문서 우선순위가 바뀌면 이 파일과 프라이머를 함께 갱신한다. public 문서에는 private 문서의 내용을 길게 복사하지 말고 행동 규칙만 짧게 남긴다.
- 세션이 막히면 새 기능을 넓히기 전에 walking skeleton이 여전히 도는지 확인한다. 계약 변경은 같은 세션에서 `contracts/changes/`와 명세서까지 함께 정리하고, DoD 명령은 실제 실행 가능한 형태로 남긴다.

## Java/Kotlin/Spring 기준 스택

- JVM은 **JDK 25 LTS**로 고정한다. JDK 26은 최신 feature release지만 이 프로젝트의 기준은 최신 LTS다.
- Spring 판단 계층은 **Spring Boot 4.1.0 + Spring Framework 7.0.8+ + Kotlin 2.4.0 + Gradle 9.5.0** 조합으로 맞춘다.
- Java preview 기능은 사용하지 않는다. `--enable-preview`를 빌드, 테스트, 실행 옵션에 추가하지 않는다.

## 작업 방식

- 파일을 만들거나 수정하기 전 현재 상태를 확인한다.
- 명세나 계획을 보강할 때는 새 문서 파일을 만들기보다 기존 문서(최종 프로젝트 명세서, API 명세서, 세션별 작업계획 등)에 절을 추가하는 방식을 우선한다.
- 이미 있는 사용자 변경은 되돌리지 않는다.
- 파괴적 명령(`git reset --hard`, broad delete, force push 등)은 사용자가 명확히 요청하지 않으면 실행하지 않는다.
- 구현 작업을 시작하기 전에는 관련 명세와 workspace 경계를 확인한다.
- 설정 파일을 제외한 코드에는 필요한 지점마다 한글 주석 또는 docstring을 남긴다. 주석은 각 언어의 기본 주석 문법(`#`, `//` 등)을 쓰고, 별도 접두어 없이 바로 적는다.
- public 함수·class·client method처럼 다른 모듈이 호출하는 경계에는 “입력/출력 계약, 외부 원천, 보안·운영 주의점” 중 해당하는 내용을 1~2문장 docstring 또는 주석으로 남긴다.
- private helper는 코드가 이미 말하는 “무엇을 하는가”를 반복하지 말고, 계약·보안·운영·테스트 관점에서 “왜 이 방식이어야 하는가”가 있을 때만 주석을 단다. 예: `# 휴장일에는 외부 KIS 호출 자체를 만들지 않아 rate limit과 장애 전파를 동시에 줄인다.`
- 모듈 경계, 외부 API 호출, secret/token 처리, 파일 저장, 멱등성, fallback, scope guard처럼 나중에 실수하기 쉬운 지점에는 조금 더 자세한 주석을 둔다. 단순 할당·명백한 반복문·테스트 fixture 값에는 주석을 억지로 달지 않는다.
- 모든 코드 변경에는 그 동작을 검증하는 테스트 코드가 함께 있어야 한다. 테스트 없이 코드를 추가하거나 수정하지 않으며, 외부 API·시각적 확인·수동 smoke처럼 자동화가 어려운 부분도 fixture, mock, 계약 검증, smoke 명령 중 하나로 재현 가능한 검증 근거를 남긴다.
- 작업을 커밋할 때는 테스트 추가, 실제 구현, 문서/규칙 변경을 가능한 한 각각 별도 커밋으로 남긴다. PR 리뷰에서 “테스트가 무엇을 요구했고 구현이 무엇을 만족했는지”가 커밋 단위로 보여야 한다.
