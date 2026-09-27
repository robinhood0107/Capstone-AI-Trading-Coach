# CONTRIBUTING.md

이 파일은 저장소 전체에 적용되는 기여 규약이다. 모든 작업자가 이 규칙을 따른다.

세션 운영, 단계별 권위처럼 내부 작업 맥락에 해당하는 상세 규칙은 저장소 밖
로컬 전용 문서에서 관리한다. 공개 저장소에는 기여자가 실제로 필요로 하는 것만 둔다.

## 기준 문서 우선순위

작업 전 아래 문서를 먼저 확인한다. 충돌이 있으면 위쪽이 우선한다.

1. `docs/최종_프로젝트_명세서.md`
2. `docs/API_명세서.md`
3. 공개 문서에 명시된 계약과 workspace 경계

로컬 전용 참고자료와 개인 파일 경로는 Git 저장소 밖에서 관리하며 커밋에 포함하지 않는다.

## 워크스페이스 경계

- `workspaces/decision-platform/` — 박종진(`robinhood0107`) 담당. 이 저장소에서 실제 구현하는 영역이다.
- `workspaces/return-engine/` — Team B 수신본 중 검토된 one-shot consumer와 재현 가능한 artifact 코드만 승격한다. provider client, cache, 원시 CSV, 출처 없는 pickle은 넣지 않는다.
- `workspaces/experience-dashboard/` — Team A 수신본 중 검토된 same-origin production source, lockfile, 테스트, Docker 경계만 승격한다. mock/dev 산출물은 production 이미지에서 제외한다.
- `contracts/` — workspace 간 계약의 단일 진실 소스다. 변경하면 `contracts/changes/`에 이유와 영향 범위를 남긴다.
- `artifacts/` — 계약을 만족하는 산출물 교환 폴더다. 원본 코드, 대용량 원시 데이터, 로컬 실행 산출물은 커밋하지 않는다.

## 보안과 비밀값

- `.env`, `.env.local`, `*.env`, `http-client.private.env.json`은 커밋하지 않는다. 커밋 가능한 환경 파일은 `.env.example` 하나다.
- API key, JWT secret, 계좌번호, 토큰, 주문/잔고 원본 로그는 코드·문서·테스트 fixture 어디에도 넣지 않는다.
- 증권사 원본 응답, 응답 헤더, access token, 계좌 식별자, raw/parquet/csv/jsonl 산출물은 커밋하지 않는다. 테스트에는 마스킹한 offline fixture만 둔다.
- 실계좌 주문·정정·취소는 별도 live-order gate가 명시되기 전까지 기본 비활성이다.
- 로그 예시가 필요하면 값은 반드시 마스킹한다.

### 증권사 API 호출 유량

- KIS Developers 공식 안내를 운영 유량의 기준으로 둔다. REST는 실전 계좌당 18건/초, 모의 1건/초이고 토큰 발급과 WebSocket 접속키 발급은 각각 1건/초다.
- 같은 credential과 mode는 Redis 원자 limiter를 공유한다. 실전 기본 120ms, 모의 최소 1,000ms 간격을 적용하며 설정은 공식 상한보다 낮출 수만 있다. limiter 장애 시 온라인 호출은 fail-closed한다.
- 토큰 발급은 일반 REST 예산과 분리한 1건/초 limiter를 쓴다. 안전한 GET의 라우팅 실패만 다음 허용 슬롯에서 재호출하고, 유량 초과와 주문·정정·취소는 자동 재시도하지 않는다.
- WebSocket은 계좌(앱키)당 1세션, 등록 41개로 고정한다. 42번째 등록과 두 번째 세션은 호출 전에 거부한다.

## Git과 GitHub 규칙

- 기본 브랜치는 `main`이다. 작업 브랜치는 `feature/*`, `fix/*`, `docs/*`, `infra/*`, `experiment/*`를 쓴다.
- 커밋 메시지는 `<type>(<session>): 요약` 형식을 권장한다. 예: `chore(S0): repo hygiene 설정`.
- 커밋은 기능 단위로 작게 나눈다. 한 커밋에 하나의 의도만 담고 서로 다른 기능·버그·문서 정리를 섞지 않는다.
- 기본 순서는 `계약 확인 → 최소 구현 → 가장 가까운 focused 검증 → 부족한 회귀 보강 → release 전체 gate 1회`다. 실패 테스트 선작성을 필수 흐름으로 쓰지 않는다.
- 기존 테스트가 변경 동작과 회귀 위험을 충분히 덮으면 중복 테스트를 만들지 않는다. 새 동작, 보안 경계, 재발 가능성이 기존 coverage에 없을 때만 최소한으로 추가한다.
- 문서·명세·규칙 파일 변경은 코드 구현 커밋과 분리한다. 리뷰어가 diff를 따로 볼 수 있어야 한다. 예외는 그 커밋의 코드가 없으면 테스트가 실행조차 되지 않는 기계적 동반 변경뿐이고, 예외를 쓰면 이유를 커밋 메시지나 PR 본문에 적는다.
- **커밋과 PR에는 사람 기여자만 남긴다.** 서비스 계정 `Co-Authored-By:`, `Generated with`, `Assisted-by:` 형태의 트레일러를 추가하지 않는다. 이 규칙은 `.githooks/commit-msg`와 `repo-hygiene.yml`의 검사로 함께 강제된다. 훅은 `git config core.hooksPath .githooks`를 한 번 실행해 활성화한다.
- 사람 공동작성자 표기(`Co-Authored-By: <팀원>`)는 정상이며 그대로 통과한다.
- PR에는 문서·API·계약 변경 여부, secret 포함 여부, 다른 팀원 workspace 수정 여부를 명시한다.
- Issue와 PR의 제목·본문은 한국어와 영어를 함께 적는다. 최소한 `KR:`과 `EN:` 구역을 둔다.
- 연관된 Issue·PR·commit은 GitHub 번호로 연결한다. PR 본문에 `Closes #<issue>` 또는 `Refs #<issue>`를 쓰고, 해당 변경을 수행한 커밋 메시지에도 번호를 넣는다.

## 버전과 릴리스

- 버전은 유의적 버전(`MAJOR.MINOR.PATCH`)이며 단일 출처는 `deploy/p1/mars-release-gate.json`의 `version`이다.
- `develop → main` 병합 한 번이 릴리스 한 번이다. 병합되면 GitHub Release `vX.Y.Z`와 Docker Hub `pjjpjj111/mars-{demo,full}:vX.Y.Z-{api,web,postgres,redis}`가 자동 발행된다.
- 승격 PR 전에 버전을 올리고 `CHANGELOG.md`에 `## [X.Y.Z] - YYYY-MM-DD` 항목을 쓴다. 이미 발행된 버전이거나 항목이 없으면 `MARS product image build` 체크가 병합을 막는다.
- 호환이 깨지는 계약·DB 변경은 MAJOR, 기능 추가는 MINOR, 버그·문서 수정은 PATCH를 올린다.
- 발행된 태그와 Release는 수정하거나 다시 만들지 않는다. 고칠 것이 있으면 다음 PATCH로 낸다.

## CI

현재 워크플로는 네 개다.

| 워크플로 | 내용 |
|---|---|
| `repo-hygiene.yml` | 필수 경로, compose 검증, ignore 규칙, secret scan, 커밋 위생 검사 |
| `contracts-ci.yml` | 계약 schema와 positive/negative fixture, OpenAPI 생성물 일치 |
| `kotlin-build.yml` | JDK 25에서 `./gradlew ktlintCheck build` (Testcontainers 포함) |
| `python-ci.yml` | `uv lock --check` → `uv sync --frozen` → ruff → mypy → pytest |

세션 DoD에 CI job 추가가 포함되면 각 job을 별도 워크플로 파일로 늘린다. hygiene은 항상 유지한다.

## 기준 스택

- JVM: Java 25 LTS, Kotlin, Spring Boot, Gradle wrapper 9.5.0
- Python: 3.12, uv (`uv.lock` 커밋 필수)
- 저장소: PostgreSQL, Redis
- 프런트엔드: TypeScript

## 작업 방식

- 세션이 막히면 새 기능을 넓히기 전에 walking skeleton이 여전히 도는지 먼저 확인한다.
- 계약을 바꾸면 같은 세션에서 `contracts/changes/`와 명세서까지 함께 정리하고, DoD 명령은 실제 실행 가능한 형태로 남긴다.
- 공개 문서에는 로컬 전용 문서의 내용을 길게 복사하지 않는다. 행동 규칙만 짧게 남긴다.

## 현재 권위 표식

아래 값은 문서 동결 검증기가 읽는다. 지금 무엇이 열려 있고 무엇이 닫혀 있는지를
한곳에 고정한다. 상태가 바뀌면 이 절과 근거 문서를 같은 PR 에서 함께 고친다.

| 표식 | 뜻 |
|---|---|
| `PRE_S5_DOC_TRUTH_FREEZE_ADDENDUM_VERIFIED` | Pre-S5 공개 문서 동결 부록을 검증했다 |
| `PRE_S5_EXECUTION_OWNER=DECISION_PLATFORM` | Pre-S5 실행 소유권은 Decision Platform 에 있다 |
| `PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES` | 교차시장 계획은 외부 hard gate 를 조건으로 진행한다 |
| `S4_8A=CONTRACT_LOCKED` | S4.8A 계약은 고정됐다 |
| `S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE` | S4.8B/C offline runtime 은 병합 후보 상태다 |

Strong LLM 모델 식별자는 환경변수 `VERTEX_MODEL_ID` 로 주입한다. 코드에 벤더 모델명을
고정하지 않는다.
