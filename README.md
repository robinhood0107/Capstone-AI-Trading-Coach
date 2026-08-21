# Capstone-AI-Trading-Coach

투자 원칙 기반 AI 트레이딩 코치 — 졸업과제 모노레포.

> 이 레포는 개인(박종진, `robinhood0107`) 계정에 먼저 생성되었습니다. 팀 공용 레포로 전환 시 원격만 옮기면 되도록 구조는 [최종 프로젝트 명세서](docs/최종_프로젝트_명세서.md) 6장의 모노레포 설계를 그대로 따릅니다.

## 에이전트/CI 규칙

작업 전 [AGENTS.md](AGENTS.md)를 먼저 확인한다. 현재 GitHub Actions는 Repo Hygiene,
Contracts CI, Kotlin Build, Python CI, S1.4X contract correctness를 수행한다. **어느 세션
완료 시점에 어떤 CI job을 추가하는지는 AGENTS.md의 "CI 로드맵" 표를 따른다**.

## 현재 구현 상태

STAGE 2에서 S1.6 Market Calendar/Event Aggregator offline 구현, S2.1 Principle CRUD,
S2.2 offline evaluator, S2.3 Decision runtime, S2.4 Risk/Kill Switch, S3.1~S3.3와 S4.7D
계약/parser/OCR/API skeleton까지 `main`에 병합됐다. session별 current implementation/offline/live
상태는 [Pre-S5 상태 ledger](docs/README.md#현재-검증-상태)가 유일한 공개 요약이다.
S4.9 MCP·LangGraph Strong LLM은 PR #131로 V71까지 병합됐고, live evidence·exact-tree Security·
merge SHA CI를 확인해 `S4_READY_FOR_S5=TRUE`, `S5_ENTRY_GATE=OPEN` 상태다.
S3.1은 S2.3 Decision과 S2.4 Kill Switch를 소비하는 KIS Mock 주문 제출/조회/취소와
stored balance/buyable projection을 추가한다. 주문 요청 body는 `decisionId`, exact 8-field
`orderIntent`, `userAcknowledgement`만 허용하고 account/provider/actor 필드는 인증 principal과
서버-side HMAC scope에서만 만든다. Decision은 한 번만 소비되며 raw idempotency key, raw account,
provider raw payload는 저장하지 않는다. S2.3 stored-source가 없거나 stale/incomplete이면 Decision
runtime은 production 값을 꾸미지 않고 persisted `HOLD`로 남긴다. LIMIT 주문은 verified KRX
tick-table context가 없으면 `BROKERAGE_UNAVAILABLE`로 fail-closed하며, S3.1의 provider/live
account/broker/order physical call은 모두 0건이다.

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

S1.3G 뉴스 authority는 2026-07-31부터 Naver active provider/runtime/storage를 퇴역시키고,
기사 metadata가 없는 GDELT aggregate synthetic fixture 계약으로 전환한다. 2026-08-01 현재
Naver active runtime/schema/test와 승인된 local leaf 제거, GDELT strict parser·ABSTAIN·append-only
offline artifact·승인 packet validator 구현까지 완료했다. GDELT HTTP transport는 활성화하지 않았고
provider 호출, RiskDecision/hash/order 권한, S5 feature 주입은 모두 0이다.

### 교차시장·애널리스트 오버레이 계획 상태

교차시장 계획 타당성은 `PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES`다. `S4_8A=CONTRACT_LOCKED`,
`S4_8_CORE6_V2=CONTRACT_ONLY`, `S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE`다. Core 6 v2는
KIS/OpenDART/SEC EDGAR/KRX/KOFIA/ECOS의 future entitlement·packet·sanitized receipt만 잠그며
provider adapter/live call은 0이다. provider 없는 fixture/scorer/append-only evidence/설명 경계만
구현됐고 `S4_8=VERIFIED_OFFLINE_STORED`다. S6.6/S6.7 실행 capability는 strict `REAL_PIT`
자료 부재로 `RETIRED_NOT_APPLICABLE`이며 cross-market REST/RiskEngine runtime은 없다. 월 데이터 비용 목표는 `0원`,
offline fixture와 지연/EOD가 우선이다. 기관용 데이터 제품과 실시간 SOX/VIX feed는
post-P1 선택지이며 완주 조건이 아니다. 새 agent framework·별도 cloud·Kafka hard
dependency 없이 기존 Spring/Python/PostgreSQL/Redis/gRPC를 재사용한다.

순서 0 read-only `S4.READ`와 S4.8A contract-only merge gate는 충족했고 provider 없는
S4.8B/C를 구현했다.
현재 S4.8A/B/C의 Decision/Signal/Risk/order/hash 권한은 0이며, 애널리스트·뉴스·RAG·LLM은
RiskDecision과 판단 hash를 바꾸지 않는다. S6.6/S6.7 historical artifact는 재현용으로만
보존하며 현재 execution task, runtime capability 또는 S5 entry dependency를 만들지 않는다.
기존 Decision/RAG/Signal v1/v2 payload에 추가하는 교차시장 필드는 0이다.

42개는 integration target 조사 행 수이지 사용 가능한 API 수가 아니고, KIS 18개도
fixture-first adapter 후보다. exact 42개 행과 exact 18개 allowlist의 authority는
Git으로 추적하지 않는 로컬 전용 자료수급 레지스트리이며,
공개 문서에는 전체 inventory를 복제하지 않는다.

2026-07-30 계획 확정 변경은 Markdown-only였고, 후속 S4.8A 계약 PR과 병합된 S4.8B/C offline-only
implementation을 구분한다. provider activation과 RiskEngine/OpenAPI endpoint는
여전히 포함하지 않는다. 상세 순서는
[최종 프로젝트 명세서의 S4~P1 실행 순서](docs/최종_프로젝트_명세서.md#171-2026-07-29-s4p1-실행-순서)를
따른다.

S5.0의 historical Signal v1/v2와 OpenAPI bytes는 보존한다. S5.1~S5.6 repository code,
safe artifact ingest와 인증형 exact `GET /api/v2/signals/{symbol}`는 구현됐지만, 실제 S5.6
bootstrap은 승인된 KRX 상한을 2회 초과해야 완성되는 지점에서 fail-closed됐다. 따라서 현재
실제 dataset/model과 production pointer는 없고 evidence 없는 조회는 all-ABSTAIN이다.
RiskDecision/order wiring은 계속 `NO_GO`다.

## Pre-S5 단독 실행 소유권 잠금

이 block이 현재 Pre-S5 authority다. 기존 역할·일정·산출물 설명은 `HISTORICAL_SUPERSEDED`로만
보존하며, 존재하지 않는 output은 `NOT_AVAILABLE/ABSTAIN`으로 처리한다. 새 implementation task,
Issue, PR, deadline, live blocker 또는 S5 entry dependency는 만들지 않는다.

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

Decision Platform은 existing synthetic/offline GDELT aggregate producer를 소유한다. HTTP transport와
executor/outbound activation은 없으며 Naver는 retired 상태를 유지한다.

## 워크스페이스 소유권 — HISTORICAL_SUPERSEDED catalog

아래 표는 기존 workspace label을 보존하는 historical catalog일 뿐, 현재 실행 책임이나 신규 의존성을
만들지 않는다.

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
S1.2c OpenDART 분석 데이터, S1.3 ECOS와 퇴역 완료된 Naver historical audit,
S1.3G GDELT aggregate contract, S1.3K KRX universe 자동화,
S1.4 금융공학, S1.5 품질 보고, S1.6 내부 offline calendar/event aggregator, S2.1 Principle
CRUD, S2.2 offline rule evaluator, S2.3 Decision runtime과 S2.4 Risk/Kill Switch까지 구현되어
있다. 이 S3.1 변경은 `POST /api/v1/brokerage/mock/orders`,
`GET /api/v1/brokerage/orders/{orderId}`, `POST /api/v1/brokerage/orders/{orderId}/cancel`,
`GET /api/v1/brokerage/mock/accounts/{accountId}/balances`,
`GET /api/v1/brokerage/mock/accounts/{accountId}/buyable`, V11 mock order ledger/RLS/one-use
constraint와 fixture-first KIS Mock adapter/gRPC boundary를 추가했다. provider를 호출하거나 live
계좌·실주문을 열지 않으며, KIS Mock full physical probe는 next valid window의 새 approval packet
전까지 `LIVE_VERIFIED`가 아니다. LIMIT 주문은 현재 KRX 호가단위 source가 pinned artifact로
검증되기 전까지 `BROKERAGE_UNAVAILABLE`로 fail-closed한다.
로컬 전용 참고자료와 개인 파일 경로는 GitHub에 올리지 않는다.

S4.0/S4.1/S4.7A는 source-card 계약과 정규화 RAG registry, owner-scoped source 조회,
공식 근거 manifest와 안전한 로컬 입력 경계를 추가한다. S4.2+ generation runtime과
provider/model/account/order physical call은 이 범위에 포함하지 않는다.

S4.2A는 pinned local BGE ONNX artifact verifier와 공식 5-card PoC를 추가했고, S4.7B는
v1을 변경하지 않는 source-card v2 union과 project-authored exact 30 corpus를 동결한다.
30-card manifest는 금융공학 15개·공식자료/API 15개만 포함하며 upstream reference-only
20개와 원문 payload는 제외한다. S4.2B는 이 frozen corpus의 30개 BGE embedding을
PostgreSQL+pgvector에 materialize하고 독립 DB 재검증, 20회 warmup/100회 measured
local benchmark와 bounded admin CAS를 통과해 generation
`rag_gen_6f2aa814a39d0532d0fa4bbd4e4456d2`를 활성화했다. provider physical call은 0이며
S4.3은 owner/access 제한 exact·lexical·dense channel과 application RRF를 구현했다.
S4.4는 local sensitive/advice guard, purpose-separated idempotency, append-only consent,
owner-scoped AES-256-GCM 30일 history와 metadata-only list를 `FIXTURE_ONLY`로 구현한다.
S4.7C는 기존 S4.7B bytes를 보존한 채 동일 body exact 30의 새
`s4_7c_external_v1` revision을 append하고, local BGE vector 30/30 동등성·retrieval
non-regression·stale CAS rollback을 검증해
`rag_gen_789b3ba9589ad399373194c0e3c0e76f`를 단일 active generation으로 전환했다.
기본 `RAG_GRPC_ENABLED=false`는 `RETRIEVAL_ONLY` S4.4 호환 모드다. S4.6은 canonical
`RagService.Ask`, numeric loopback, consent/rate/idempotency 순서, owner scope·active generation·
top-5 citation 재검증과 encrypted history E2E를 구현했다. 동일 배포 Python
fixture process와 모든 활성 auth·Decision/Python·brokerage credential에서 분리된 전용
`RAG_GRPC_SHARED_SECRET`이 준비된 뒤만 true로 활성화한다. 현재
Gemini·OpenAI·Voyage·account·order 물리 호출은 0이며 live generation은 별도
승인형 S4.4G 경계다.

```bash
cp .env.example .env
# DB/Redis, collector/source-writer/RAG writer/admin/query password, JWT issuer/audience,
# 목적별 signing/HMAC key와 두 attested demo credential bundle을 채운다.
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
`decision_market_writer`/`decision_portfolio_writer`/`decision_risk_writer`/
`decision_fill_writer`, RAG ingest/materialization은 `decision_rag_writer`, bounded active
generation 검증·CAS 활성화는 `decision_rag_admin`, bounded active chunk retrieval은
`decision_rag_query`, migration은 `flyway`, bootstrap 관리는
`POSTGRES_ADMIN_USER`로 분리된다. 기존 `pgdata` volume에는 init script가 자동 재실행되지
않으므로, 기존 관리자 이름/비밀번호를 보존하고 `.env.example`의 collector, source-writer,
`POSTGRES_RAG_WRITER_PASSWORD`, `POSTGRES_RAG_ADMIN_PASSWORD`,
`POSTGRES_RAG_QUERY_PASSWORD`를 추가해 컨테이너를 올린 뒤 다음 명령을 실행한다.
V6/V9/V14/V16/V18 적용 전에는 role을 먼저 만들고, migration 뒤 다시 실행하면 현재
table과 function의 exact 권한을 복원한다. volume 삭제는 이 절차에 포함하지 않는다.
`decision_fill_writer`는 sanitized offline fill observation INSERT만 소유하며
주문·이벤트·Flyway schema에는 접근하지 않는다. `decision_rag_admin`은 raw table
SELECT/DML 없이 verification projection과 원자 pointer CAS 함수만 실행한다.

```bash
docker compose --env-file .env -f infra/docker-compose.infra.yml exec -T postgres \
  bash /docker-entrypoint-initdb.d/02-application-roles.sh
```

V16은 기존 V2 RAG 다섯 table(`rag_sources`, `rag_chunks`, `rag_answers`, `rag_citations`,
`rag_answer_feedback`)이 모두 비어 있을 때만 정규화 registry로 전환한다. 하나라도 row가
있으면 자동 삭제나 강제 전환 없이 migration이 중단되므로 별도 승인된 migration packet으로
처리한다. 기존 volume은 위 role bootstrap을 먼저 실행하고 V16을 적용한 뒤 같은 bootstrap을
한 번 더 실행해 `decision_rag_writer`의 append/update allowlist와
`decision_rag_query`의 bounded function `EXECUTE`만 복원한다.

role bootstrap은 password DDL 전에 session의 statement/error-statement/duration/sample logging을
모두 끄고 `current_setting`으로 effective 값을 검증한다. 하나라도 안전값이 아니면
`ON_ERROR_STOP`으로 password bind 전 중단한다.

S1.6 OpenDART online collector는 `.env.example`의 네 quota 값을 운영 evidence에 맞게 모두
명시해야 하지만, 설정만으로 활성화되지 않는다. 현재 구현은 offline fixture와 mock transport로만
검증됐으며 KIS/KASI/OpenDART provider 호출, 운영 DB 배포와 collector schedule은 별도 승인 대상이다.

## 문서

- [최종 프로젝트 명세서](docs/최종_프로젝트_명세서.md)
- [API 명세서](docs/API_명세서.md)
- [Pre-S5 S0~S4 상태 ledger](docs/README.md#현재-검증-상태)
- [RAG 외부 AI 처리 및 개인 문서 동의](docs/RAG_외부_AI_처리_및_개인문서_동의.md)
- S1.6 내부 계약: 최종 명세 11.1.2와 API 명세 12A
- [S2.2 offline 계약과 재현 명령](contracts/README.md#s22-rule-evaluation-offline-contract-v1)
- [S2.2 계약 변경 기록](contracts/changes/20260724-s2-2-rule-evaluation-offline-contract.md)
- [S2.3 Decision runtime과 stored-source 경계](contracts/README.md#s23-decision-runtime과-stored-source-경계)
- [S2.3 Decision 계약 잠금](contracts/changes/20260724-s2-3-decision-contract-lock.md)
- [S2.4 Risk와 Kill Switch 계약](contracts/README.md#s24-risk-api와-kill-switch)
- [S2.4 계약 변경 기록](contracts/changes/20260725-s2-4-risk-kill-switch-contract.md)
- [S3.1 Brokerage Mock 주문 계약](contracts/README.md#s31-brokerage-mock-주문)
- [S3.1 계약 변경 기록](contracts/changes/20260726-s3-1-brokerage-mock-contract.md)
- [S3.2 INTERNAL_PAPER 원장 계약](contracts/README.md#s32-internalpaper-체결-원장)
- [S3.2 계약 변경 기록](contracts/changes/20260727-s3-2-internal-paper-ledger-contract.md)
- [S3.3 체결 이벤트와 대사 계약](contracts/README.md#s33-체결-이벤트와-대사)
- [S3.3 계약 변경 기록](contracts/changes/20260727-s3-3-fill-events-reconciliation-contract.md)
- [S4.8 교차시장·애널리스트 계약](contracts/README.md#s48-교차시장애널리스트-계약)
- [S4.8 Core 6 v2 계약 변경 기록](contracts/changes/20260802-s4-8-core6-v2-contract-lock.md)
- S1.4X dependency amendment 재현: `workspaces/decision-platform/research/s1-4x-numeric-parity/README.md`
