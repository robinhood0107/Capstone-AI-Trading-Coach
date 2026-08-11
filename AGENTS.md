# AGENTS.md

## 적용 범위

이 파일은 레포 전체에 적용된다. 더 깊은 경로에 별도 `AGENTS.md`가 추가되기 전까지 모든 에이전트와 사람 작업자는 이 규칙을 따른다.

## 기준 문서 우선순위

작업 전 아래 문서를 먼저 확인한다. 충돌이 있으면 위쪽 문서가 우선한다.

1. `docs/최종_프로젝트_명세서.md`
2. `docs/API_명세서.md`
3. 공개 문서에 명시된 계약과 workspace 경계

로컬 전용 참고자료와 개인 파일 경로는 Git 저장소 밖에서 관리하며 GitHub 커밋에 포함하지 않는다.
구현 세션용 로컬 보조 자료는 사용자가 명시하거나 작업상 필요할 때만 참고한다. 사용자 학습용
로컬 자료는 구현 입력이 아니며, 관련 주제가 나왔을 때 읽어볼 자료로만 제안한다.

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
- S2.3 현물 Decision `orderIntent`는 MARKET/LIMIT 모두 `estimatedPrice`만 사용한다.
  `price`/`limitPrice` alias를 추가하지 않으며 exact 8개 field는
  `symbol,side,orderType,quantity,estimatedPrice,estimatedAmount,timeframe,strategyId`다.
  `HASH-CANONICALIZATION-S22-V2`와 `s2.2-metric-snapshot-v2`는
  `contracts/changes/20260724-s2-3-decision-contract-lock.md`를 따른다. OpenAPI SSOT는
  `contracts/openapi/openapi.json`이다.
- S2.3은 저장된 sanitized source의 read adapter만 소유한다. 현재가·호가 producer는 S1.1,
  KIS_MOCK balance producer와 INTERNAL_PAPER ledger mutation은 S3, corp/disclosure는 S1.6,
  deterministic risk/order-count는 deterministic source 모듈 소유다. 이번 continuation은
  offline fixture producer·최소권한 writer·bounded projection을 prerequisite로 구현하지만
  provider/live/order 호출 권한은 아니다. source 구조 자체가 빠지면
  `S23_RUNTIME_SOURCE_BLOCKED`, 구조가 준비된 뒤 row가 없으면 production 값을 꾸미지 않고
  persisted HOLD로 처리한다.
- S4.8A/B/C는 RAG와 분리된 교차시장 source entitlement·fixture/EOD 관측·애널리스트
  revision·원인 evidence를 소유한다. S5 Signal feature에는 넣지 않고 S6.6 독립
  event-study/LightGBM BUY policy replay 뒤 S6.7 저장 snapshot reader로만 RiskEngine에
  연결한다. P1 최고 권한은 `WARN_ONLY`이며 analyst/news/RAG/LLM은 RiskDecision과 판단
  hash를 바꾸지 않는다.
- **교차시장 계획 타당성은 `PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES`다. S4.8A 일곱 계약은
  `S4_8A=CONTRACT_LOCKED`, S4.8B/C offline runtime은 `S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE`,
  S6.6/S6.7은 `NOT_IMPLEMENTED / PLANNED`다.** 월 데이터 비용 목표는
  `0원`이며 offline fixture와 지연/EOD를 먼저 사용한다. Bloomberg·LSEG·FactSet·코스콤과
  실시간 SOX/VIX feed는 `ENTERPRISE_ONLY_DISABLED`인 post-P1 선택지로서 P1 완주 조건이
  아니다. 기존 Spring/Python/PostgreSQL/Redis/gRPC를 재사용하고 새 agent framework,
  별도 cloud, Kafka를 이 lane의 hard dependency로 추가하지 않는다.
- 교차시장 실행의 순서 0은 `S4.READ`다. 관련 공개·private 명세의 EOF receipt와 충돌
  목록을 남긴 뒤 S4.8A **contract-only PR**이 일곱 JSON Schema,
  `s2-2-system-rule-catalog.v2`, contract-change 기록과 fixture/golden vector를 고정한다.
  이 계약 자체는 runtime 완료 증거가 아니다. S4.8A의 main 병합과 post-merge CI를 확인한
  뒤 S4.8B/C를 시작했고, 후속 S6.6/S6.7은 아직 시작하지 않는다. 전체 RiskEngine은 기존
  `ALLOW/WARN/HOLD/BLOCK`을 유지하지만 P1 교차시장 overlay가 추가할 수 있는 변화는
  적용 대상 신규 BUY의 `ALLOW → WARN`뿐이다.
- S4.8B는 provider 호출 없는 수동/offline EOD materialization, append-only 저장 경계와
  I/O 없는 결정적 `CrossMarketScorer` kernel을 소유한다. S6.6은 그 scorer output으로
  event-study/replay와 threshold 동결만 수행하고, S6.7이 snapshot을 materialize한다.
  S7.3은 같은 저장 port를 주기 실행하는 scheduling만 추가하며 새 source ownership이나
  provider 권한을 만들지 않는다.
- 교차시장 구현의 기본 provider/live/account/order 호출은 0이다. 증권사 PDF는
  `MANUAL_LINK_ONLY`가 기본이고 자동 다운로드·영속 저장·외부 LLM 전송을 허용하지 않는다.
  사용자가 보유한 로컬 파일은 active `LOCAL_EPHEMERAL_PARSE` 경계에서 파일별 packet 없이
  read-only로 처리할 수 있지만, 이는 원문의 이용·재배포 권리를 자동으로 부여하지 않는다.
  교차시장 projection은 `투자포인트`, `실적전망`, `Valuation`, `목표주가`, `위험요인`,
  `Disclaimer` 여섯 절과 사용자가 확인한 bounded tag로 제한한다.
  `derivedDataAllowed=false`이면 parser/LLM의 파생 결과도 저장·전달하지 않고 임시 입력과
  함께 폐기한다. DRM·로그인·paywall 우회와 무단 crawling은 계속 금지한다.
  exact 42개 integration target 행과 exact KIS 18개 endpoint allowlist의 운영 authority는
  Git으로 추적하지 않는 로컬 전용 자료수급 레지스트리다. 원 API 문서 위치 evidence는
  별도의 로컬 전용 참고자료이며 integration target 행 자체의 SSOT가 아니다.
  공개 문서에는 집계와 불변식만 두고 전체 inventory를 복제하지 않는다.
- 기존 Decision request/response, RAG ask/history, Signal v1/v2 payload에 교차시장 필드를
  추가하는 수는 0이다. 내부 `CrossMarketDecisionInput(snapshot, exposure)` wrapper와 별도
  planned 조회 계약만 사용하며, payload 변경이 필요하면 별도 breaking contract-change로
  다시 승인한다.
  Return Engine과 Experience Dashboard placeholder에는 구현 파일을 만들지 않는다.
- S5.0은 Signal v1과 current OpenAPI bytes를 유지한 채 component별
  `AVAILABLE | ABSTAIN`인 Signal v2 계약만 잠근다. `AVAILABLE + HOLD`는 정상 예측이고,
  stale/FAIL/drift/missing evidence는 prediction/asOf/HMM state 없는 `ABSTAIN`이다. active
  `/api/v2/signals/{symbol}`, artifact ingest, RiskDecision/order wiring은 후속 승인 전 `NO_GO`다.
- Pre-S5 RAG/global-news addendum은
  `contracts/catalogs/pre-s5-rag-news-contract.v1.json`이 SSOT다.
  `OA112_ACTIVE_CONTRACT_LOCKED`는 정확히 14 track × 8의 logical selection일 뿐
  `S4_7D_OA112_PHYSICAL_ACTIVATION=NOT_MATERIALIZED`다. historical OA112/OA140 artifacts,
  v1 OpenAPI/proto/source-card, exact-30와 `news_sentiment_summary.v2`는 byte-stable하게 보존한다.
  reserve는 최대 28이며 자동 승격은 없다. active physical source에는 machine fetch/local processing/
  external embedding/external generation permission과 actual rights/hash evidence가 모두 필요하다.
  public EXACT30+OA112의 CPU BGE 재실행은
  `TERMINALLY_SUPERSEDED_NO_FURTHER_BGE_RUN`이다. Voyage `voyage-context-4` 1024는 official
  tokenizer 기준 110K token 이하의 exact manifest-bound resumable document batch와 EXACT30/OA112
  query batch 각 1회만 허용하며, 성공 batch·source checkpoint를 재호출하지 않는다. 모든 batch와
  평가가 끝나기 전 CAS activation은 0이다. RAG v2 consent/import와 이 local batch runtime은
  구현돼 있지만 provider activation은 아니다. `VERTEX_API_KEY` Vertex Express API-key-only
  `gemini-3.5-flash`와 foreign-news(Finnhub personal-local/SEC/Fed/GDELT offline reference)는
  계속 hard-gated다. Optional 3과 Core 6의 KIS current-price·SEC EDGAR submissions/companyfacts·KRX
  KOSPI/KOSDAQ daily만 local one-shot executor를 가진다. canonical short-expiry packet, exact clean
  HEAD/tree, CI/security digest가 모두 없거나 drift하면 provider outbound는 0이며, KIS는 cached
  OAuth token이 없으면 token endpoint를 열지 않고 fail-closed한다. Core 6 availability는 성공한
  content-free receipt의 complete required-operation set으로만 materialize하며 KOFIA는 계속
  `BLOCKED_NO_CREDENTIAL_OR_APPROVAL`, OpenDART/ECOS는 authorized projection-only다. raw corpus,
  raw provider data, article metadata, Decision/Signal/Risk/order/hash/S5 feature authority는 계속
  0이고 Core 6 exact set을 넓히지 않는다.

## Pre-S5 단독 실행 소유권 잠금

이 절은 현재 Pre-S5 실행 authority다. 아래와 다른 공개 문서의 기존 역할·일정·artifact 계획은
재현을 위한 `HISTORICAL_SUPERSEDED` 기록으로만 보존하며, 과거 ADR·contract-change·완료 evidence의
bytes를 변경하지 않는다. 현재 존재하지 않는 기존 workspace output은 `NOT_AVAILABLE/ABSTAIN`으로만
처리하고 S5 진입 또는 완료의 의존성으로 만들지 않는다.
CI는 PR base에 있던 historical/contract-change/completion-evidence record의 byte 변경·삭제를 거부한다.

```text
PRE_S5_DOC_TRUTH_FREEZE_ADDENDUM_VERIFIED
PRE_S5_EXECUTION_OWNER=DECISION_PLATFORM
S1_3G=OFFLINE_ONLY
NEW_TEAMMATE_IMPLEMENTATION_TASKS=0
NEW_TEAMMATE_ISSUES_OR_PRS=0
REQUIRED_TEAMMATE_ARTIFACTS_FOR_S5_ENTRY=0
TEAMMATE_WORKSPACE_DIFF=0
GDELT_MODE=DECISION_PLATFORM_OFFLINE_REFERENCE_ONLY
GDELT_EXISTING_OFFLINE_PRODUCER_UNCHANGED=1
GDELT_HTTP_TRANSPORT=NOT_ACTIVATED
GDELT_OUTBOUND_IMPLEMENTATION=0
GDELT_OUTBOUND_CALLS=0
GDELT_OFFLINE_REFERENCE_ONLY=1
NAVER_ACTIVE_PROVIDER_RUNTIME_STORAGE=RETIRED
RAG_NEWS_ANALYST_DECISION_SIGNAL_ORDER_AUTHORITY=0
PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES
```

Decision Platform은 기존 synthetic/offline GDELT aggregate producer를 계속 소유한다. HTTP
transport와 executor는 활성화하거나 추가하지 않으며 outbound physical call은 0이다. RAG·news·analyst는
Decision, Signal, RiskDecision, order, decision hash에 영향을 주지 않는다.

## 워크스페이스 경계

- `workspaces/decision-platform/`: 박종진(`robinhood0107`) 담당. 이 개인 레포에서 실제 구현할 수 있는 영역이다.
- `workspaces/return-engine/`: 팀원 B 담당. 현재는 placeholder이며, 이 레포에서는 `README.md` 외 구현 파일을 만들지 않는다.
- `workspaces/experience-dashboard/`: 팀원 A 담당. 현재는 placeholder이며, 이 레포에서는 `README.md` 외 구현 파일을 만들지 않는다.
- 위 두 placeholder의 기존 role label은 `HISTORICAL_SUPERSEDED`이며 현재 실행 task, artifact, entry dependency를 만들지 않는다.
- `contracts/`: workspace 간 계약의 단일 진실 소스다. 변경 시 `contracts/changes/`에 이유와 영향 범위를 남긴다.
- `artifacts/`: 계약을 만족하는 산출물 교환 폴더다. 원본 코드, 대용량 원시 데이터, 로컬 실행 산출물은 커밋하지 않는다.

## 보안과 비밀값

- `.env`, `.env.local`, `*.env`, `http-client.private.env.json`은 커밋하지 않는다.
- 커밋 가능한 환경 파일은 `.env.example`뿐이다.
- 로컬 전용 참고자료와 개인 파일 경로는 커밋하지 않는다.
- API key, JWT secret, 계좌번호, 토큰, 주문/잔고 원본 로그는 코드, 문서, 테스트 fixture에 넣지 않는다.
- KIS 원본 응답, 응답 헤더, access token, 계좌 식별자, raw/parquet/csv/jsonl 산출물은 커밋하지 않는다. 테스트에는 마스킹된 offline fixture만 둔다.
- KIS Live 시장데이터 조회 계획과 KIS Live 주문 기능은 분리한다. 실계좌 주문·정정·취소는 S3 이후 별도 live-order gate가 명시되기 전까지 기본 비활성이다.
- 로그 예시가 필요하면 값은 반드시 마스킹한다.
- 커밋 전 gitleaks 또는 GitHub `repo-hygiene` workflow로 secret scan을 통과해야 한다.

### KIS 호출 유량 불변식

- KIS Developers의 [API 호출 유량 안내(2026-04-20 기준)](https://apiportal.koreainvestment.com/community/10000000-0000-0011-0000-000000000001/post/d0d1a83f-6f8d-4437-9700-6d26702fd989)를 운영 유량의 공식 기준으로 둔다. REST는 실전 계좌당 18건/초, 모의 1건/초이고 `/oauth2/tokenP`와 WebSocket 접속키 발급은 각각 1건/초다.
- S1.1의 현재가·백필·휴장일 조회와 모든 물리 재시도는 같은 credential/appkey+mode의 opaque scope로 Redis 원자 limiter를 공유한다. 실전은 기본 120ms, 모의는 최소 1,000ms no-burst 간격을 적용하며 설정은 공식 상한보다 낮출 수만 있다. limiter/Redis 장애 시 온라인 호출은 fail-closed한다.
- `/oauth2/tokenP`는 제한 단위가 공지에 없으므로 일반 REST budget과 분리한 deployment-global 1건/초 limiter를 보수 적용하고, token cache/singleflight는 mode별 scope로 분리해 cache를 재확인한다. 안전한 GET의 KIS 분산/라우팅 실패만 다음 허용 슬롯에서 제한 재호출하고, 유량 초과와 주문·정정·취소는 자동 재시도하지 않는다.
- WebSocket은 S3/P2 이후 구현한다. 그 전에도 계약은 계좌(앱키)당 1세션, 국내·해외·주식·파생 및 체결가·호가·예상체결·체결통보 합산 41개 등록으로 고정한다. 42번째 등록과 두 번째 세션은 provider 호출 전에 거부한다.

## Git과 GitHub 규칙

- 기본 브랜치는 `main`이다.
- 작업 브랜치 이름은 `feature/*`, `fix/*`, `docs/*`, `infra/*`, `experiment/*`를 사용한다.
- 커밋 메시지는 `<type>(<session>): 요약` 형식을 권장한다. 예: `chore(S0): repo hygiene 설정`.
- 커밋은 기능 단위로 작게 분리한다. 한 커밋에는 하나의 의도만 담고, 서로 다른 기능·버그·문서 정리는 같은 커밋에 섞지 않는다.
- 테스트 코드와 실제 구현 코드는 원칙적으로 별도 커밋으로 분리한다. 권장 순서는 `test(<session>): 실패/회귀 테스트 추가` → `feat|fix(<session>): 구현` → `docs|chore(<session>): 문서/설정 정리`다.
- Markdown/AGENTS/명세서/규칙 파일 변경은 코드 구현 커밋과 분리한다. 구현과 문서가 같은 세션에서 필요하더라도 리뷰자가 diff를 따로 볼 수 있게 별도 커밋으로 남긴다.
- 예외는 오타 수정, import 정리, 테스트 fixture 이름 변경처럼 해당 커밋의 코드가 없으면 테스트가 실행조차 되지 않는 기계적 동반 변경뿐이다. 예외를 쓰면 커밋 메시지나 PR 본문에 이유를 적는다.
- 커밋과 PR에는 Codex·Claude 등 AI 도구의 기여 표시를 절대 남기지 않는다.
- PR에는 문서/API/계약 변경 여부, secret 포함 여부, 다른 팀원 workspace 수정 여부를 명시한다.
- 모든 Issue와 PR 제목/본문은 한국어와 영어를 함께 작성한다. 최소한 `KR:`와 `EN:` 구역을 두어 같은 의도를 양쪽 언어로 확인 가능해야 한다.
- 서로 연관된 Issue, PR, commit은 GitHub 번호로 연결한다. PR 본문에는 `Closes #<issue>` 또는 `Refs #<issue>`를 쓰고, 해당 변경을 직접 수행한 commit 메시지에도 관련 번호(`#<issue>` 또는 `#<pr>`)를 포함한다.

## CI 로드맵 — 언제 무엇을 추가하는가

현재 CI는 `repo-hygiene.yml`(필수 경로/compose 검증/ignore 규칙/secret scan),
`contracts-ci.yml`(계약 schema와 positive/negative fixture), `kotlin-build.yml`(JDK 25
Gradle build), `python-ci.yml`(Python 3.12 품질 게이트)이다. 아래 시점이 되면 **해당
세션의 DoD에 CI job 추가가 포함된 것으로 간주**하고 job을 늘린다. 각 job은 별도 workflow
파일로 추가한다(hygiene은 항상 유지).

| 추가 시점(세션) | 추가할 CI job | 내용 |
|---|---|---|
| S0.3 완료 시 | `kotlin-build.yml` | Gradle wrapper 9.5.0 커밋 후 JDK 25에서 `./gradlew ktlintCheck build` (test 포함, Testcontainers는 ubuntu 러너 Docker 사용) |
| S0.4 완료 시 | kotlin-build에 통합 | Flyway clean 마이그레이션 + unique 제약 Testcontainers 테스트가 test 단계에서 실행됨을 확인 |
| S1.4 완료 시 | `python-ci.yml` | Python 3.12 + uv 0.11.26에서 `uv lock --check` → `uv sync --frozen` → `uv run --frozen ruff check .` → `uv run --frozen mypy app` → `uv run --frozen pytest -q` (`uv.lock` 커밋 필수) |
| S0.2 완료 시 | `contracts-ci.yml` | `contracts/examples/*` JSON Schema validation + negative test |
| S2.1(첫 컨트롤러) 완료 시 | contracts-ci에 통합 | springdoc `generateOpenApiDocs` 출력과 `contracts/openapi/` diff — 불일치 시 실패 (API 명세서 17.4) |
| S7.1 완료 시 | kotlin-build에 통합 | Testcontainers Kafka 통합 테스트(outbox publish/manual commit) 포함 확인 |
| M4 직전 | `demo-smoke.yml` (수동 트리거) | compose up → demo_seed → 핵심 E2E 3콜(로그인/원칙/평가) smoke |
| 시간 부족 시(단일 대비책) | — | 작업계획 5.10: contracts+secret scan만 CI에 남기고 kotlin/python은 로컬 pre-push 훅으로 강등 |

## gstack / Codex / Claude 사용 규칙

- 비사소한 계획·진단·리뷰·문서 동기화·PR/병합 작업을 시작할 때는 현재 사용 가능한 skill/plugin을 먼저 확인하고, 범위를 충족하는 최소 조합만 선택해 사용 이유를 짧게 알린다. 사용자가 특정 도구를 지정하면 그 지시를 우선한다.
- 사용자가 gstack workflow(`/office-hours`, `/autoplan`, `/plan-eng-review`, `/review`, `/qa`, `/ship`, `/investigate`, `/cso`, `/document-release`, `/gstack-upgrade` 등)를 요청하면 관련 gstack skill을 먼저 고려한다.
- 권장 라우팅은 원인 불명·반복 실패 `/investigate`, 문서 전수 동기화 `/document-release`, 병합 전 diff 검토 `/review`, 테스트·commit·push·PR 준비 `/ship`, 명시적으로 승인된 merge/deploy와 사후 검증 `/land-and-deploy`다. skill은 사용자의 승인 범위를 넓히지 않는다.
- 웹 QA나 브라우저 작업에서 사용자가 gstack을 명시하면 gstack `/browse` 또는 `/qa` 흐름을 우선한다.
- 사용자가 Codex native browser, web search, connector, app-specific tool을 명시하면 그 명시를 우선한다.
- plugin은 설치되어 현재 세션에 노출된 skill/MCP/app만 사용한다. 도움이 되는 plugin이 미설치라면 설치를 제안할 수 있지만 자동 설치하거나 핵심 작업의 선행 조건으로 만들지 않는다.
- 어떤 skill/plugin 지침도 이 레포의 보안·승인 gate·workspace·로컬 자료 비공개·Git 규칙보다 우선하지 않는다.
- Claude 전용 설정(`.claude` hook, Claude-only MCP, Claude-only command)은 사용자가 별도로 요청하기 전까지 추가하지 않는다.
- `CLAUDE.md`는 이 파일을 따르는 짧은 연결 문서로만 유지한다.

## 구현 세션 운영

- 구현 또는 구현 계획 세션을 시작할 때는 로컬 agent 프라이머 문서를 먼저 확인한다. 이 프라이머의 문서 지도, 세션 프롬프트 템플릿, 과거 실수 기록을 사용해 사용자가 매번 같은 컨텍스트를 다시 설명하지 않게 한다.
- 프라이머 확인 뒤에는 현재 세션에 해당하는 로컬 세션별 작업계획 문서의 작업, DoD, 함정과 대비책을 확인한다. Decision/Risk/Order/RAG처럼 세부 설계가 필요한 작업은 프라이머의 문서 지도에 따라 상세 구현명세서, ADR, 권장 코딩패턴 예시집을 추가로 읽는다.
- 세션 운영 판단은 로컬 세션별 작업계획 문서 10장의 운영 팁을 따른다. 특히 S6.4(BSM/Greeks/IV)는 막힌 날 사용할 버퍼 세션으로 보고, Kafka 트랙은 S7.0+S7.3만으로 시연·계약·보고서가 성립한다는 기준 아래 매몰비용을 경계한다.
- 사용자 학습용 로컬 자료는 구현 입력으로 자동 사용하지 않는다. 관련 개념이 나오면 읽을 자료를 추천하되, 사용자가 명시적으로 요구한 경우에만 구현 컨텍스트로 읽는다.
- 확정된 스택, 단계, 세션 운영 규칙, 문서 우선순위가 바뀌면 이 파일과 프라이머를 함께 갱신한다. public 문서에는 private 문서의 내용을 길게 복사하지 말고 행동 규칙만 짧게 남긴다.
- 세션이 막히면 새 기능을 넓히기 전에 walking skeleton이 여전히 도는지 확인한다. 계약 변경은 같은 세션에서 `contracts/changes/`와 명세서까지 함께 정리하고, DoD 명령은 실제 실행 가능한 형태로 남긴다.

### S1.4X 격리 연구 gate

- S1.4X는 `workspaces/decision-platform/research/s1-4x-numeric-parity/` 내부의
  비생산 연구다. Gate 0 ADR이 `accepted` 상태로 병합되고 별도 Gate 1 neutral
  contract·oracle·fixture·benchmark plan이 병합되기 전에는 Scala/Haskell source,
  Gate 1 fixture 또는 S1.4X workflow를 만들지 않는다.
- production Python import/runtime/dependency, root `contracts/`, OpenAPI/JSON
  Schema, RiskEngine API와 다른 팀원 workspace는 변경하지 않는다. 이 경계가
  필요해지면 S1.4X를 넓히지 말고 별도 cross-workspace contract 결정으로 분리한다.
- candidate PR의 correctness CI는 필수이며 mismatch가 하나라도 있으면 성능 평가를
  중단한다. full benchmark는 전체 correctness 통과 뒤 같은 host·fixture·CPU
  affinity·thread 조건의 local quiet-host 단계에서만 실행한다.
- 반복 benchmark workflow는 `workflow_dispatch` 전용이고 required check가 아니다.
  scorecard와 benchmark 결과는 production migration 또는 언어 선택 승인이 아니다.

### 외부 provider 반복 실패 복구

- 같은 live 단계가 반복 실패하거나 stable code만으로 원인을 좁힐 수 없으면 동일한 end-to-end 명령을 다시 실행하지 않는다. 실패 approval/evidence를 소비 완료로 동결하고, offline 회귀 테스트와 allowlisted typed diagnostic으로 exact failure leaf를 먼저 만든다.
- 외부 작업을 독립 endpoint로 분해할 수 있고 production transport/parser/quota 경계를 그대로 재사용할 수 있을 때만 단일 endpoint·무게시 probe를 추가한다. 순서는 `focused test → 최소 수정 → focused/관련 matrix/전체 gate → 새 packet 발급·현재 사용자의 exact 승인 → probe 단계별 실행 → 최종 원자 실행`이다. exact 승인 수신 전 provider 호출은 `0`이다.
- probe는 기본적으로 retry `0`, endpoint별 physical cap `1`, artifact `0`이며 첫 실패 뒤 남은 provider 호출은 `0`이다. probe 성공은 accepted production 결과가 아니고, 최종 명령이 현재 응답 전체를 독립적으로 다시 검증해 성공한 뒤에만 원자 publish한다. probe와 최종 응답 hash 일치를 강제하지 않는다.
- 외부 provider 성공을 보장한다고 표현하지 않는다. `curl`, 브라우저 sample, 임시 script로 credential·fixed-origin transport·quota·승인 gate를 우회하지 않고 실패 evidence와 성공 acceptance set을 분리한다.

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
