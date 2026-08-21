# docs

팀 배포 기준 문서. 원본 관리처는 로컬(개인 정리 폴더)이며, 갱신 시 이 폴더에 같은 파일명으로 동기화한다.

## 현재 검증 상태

이 표는 `main=bd859ec3…`와 2026-08-15 S4.9 V71 post-merge release evidence를 기준으로 한 Pre-S5
문서 진실 동결이다. `MERGED`는 코드가 병합됐다는 뜻일 뿐 current-HEAD 외부 호출 성공을
뜻하지 않는다. `OFFLINE_ONLY`는 fixture·local/Compose 검증만, `STUB_FAIL_CLOSED`는
의도적으로 stable error로 닫힌 공개 표면만, `CONTRACT_ONLY`는 schema/fixture만 뜻한다.
`IMPLEMENTED_DRAFT`는 current working tree에 코드·migration·자동 검증이 있으나 아직 PR/main
병합이나 provider activation을 뜻하지 않는다.
`LIVE_VERIFIED_MERGE_CANDIDATE`는 live evidence가 있으나 그 evidence를 만든 코드가 아직 main에
병합되지 않은 상태다.
`LIVE_VERIFIED`는 DB에 결속된 usage/evidence와 raw-artifact 0 검증이 있어야만 사용할 수 있다. 현재 fresh
namespace에서는 public Voyage RAG, 삭제까지 끝난 synthetic owner Voyage one-shot, S4.9 OAuth/MCP/
SearXNG/Vertex smoke에 사용한다. KIS 실제 주문에는 사용하지 않는다.

| 세션 | 구현 상태 | 병합 근거 | current-HEAD 물리 호출 | 남은 경계/다음 소유 세션 |
|---|---|---|---:|---|
| S0.1 | `MERGED` | 초기 walking skeleton·V1 | 0 | 재검증만 수행 |
| S0.2 | `MERGED` | PR #1 `648cad44` | 0 | v1 contract byte 보존 |
| S0.3 | `MERGED` | PR #2 `626e8082` | 0 | Compose/Flyway 재검증 |
| S0.4 | `MERGED` | main Flyway baseline V1~V5 | 0 | clean/upgrade 재검증 |
| S1.1 | `MERGED` | KIS read-only client·quota boundary | 0 | fresh packet 전 live 재검증 금지 |
| S1.2 | `MERGED` | PR #11 `0685afe2`, #13 `5179f85c` | 0 | OpenDART packet-bound probe는 후속 |
| S1.3/1.3K | `MERGED` | PR #16 `6f439155`, #17 `814aab37` | 0 | ECOS/KRX historical receipt는 새 HEAD 권한이 아님 |
| S1.3G | `OFFLINE_ONLY` | PR #73 `2a2dc7b5` | 0 | Decision Platform existing GDELT offline aggregate producer unchanged; HTTP transport/executor/outbound 0 |
| S1.4 | `MERGED` | PR #23 `5b046978` | 0 | deterministic calculation regression |
| S1.4X | `DEFERRED_BY_DESIGN` | PR #27/#28 isolated research | 0 | production은 post-S8 gate 전 금지 |
| S1.5 | `MERGED` | PR #32 `2baec015` | 0 | network backfill은 별도 packet |
| S1.6 | `OFFLINE_ONLY` | PR #35 `056ca2d9` | 0 | registry/collector live는 후속 packet |
| S2.1 | `MERGED` | PR #41 `18adfcaf` | 0 | OpenAPI/DB regression 유지 |
| S2.2 | `OFFLINE_ONLY` | PR #43 `1e05053a` | 0 | deterministic evaluator only |
| S2.3 | `MERGED` | PR #45 `ff2b9938` | 0 | stored-source E2E 재검증 |
| S2.4 | `MERGED` | PR #47 `a84f4815` | 0 | Kill Switch/RLS regression |
| S3.1 | `MERGED` | PR #49/#51/#79 | 0 | KIS Mock full probe는 다음 거래창의 새 packet까지 미검증; KIS live order는 계속 0 |
| S3.2 | `MERGED` | PR #53 `eda6b00e` | 0 | INTERNAL_PAPER only |
| S3.3 | `MERGED` | PR #55 `d31e6238` | 0 | stored fill/reconciliation only |
| S4.0/S4.1 | `MERGED` | RAG registry·source boundary | 0 | v1 exact-30의 byte stability 유지 |
| S4.2A/B/S4.3/S4.4/S4.7B/C | `OFFLINE_ONLY` | PR #62/#64/#66/#68/#70/#72/#77 | 0 | local BGE/retrieval/history regression, provider live 0 |
| S4.2C/S4.4G | `STUB_FAIL_CLOSED` | PR #77 control plane | 0 | Voyage/Gemini outbound executor는 hard-disabled |
| S4.5/S4.6 | `OFFLINE_ONLY` | PR #77 fixture evaluation·numeric loopback | 0 | fixture/retrieval-only; provider live 0 |
| S4.7D parser/OCR | `OFFLINE_ONLY` | PR #84 `014ccca1`, #85 `4bcca91e` | 0 | 안전 parser/OCR만 구현, importer/index writer 없음 |
| S4.7D v2 runtime | `IMPLEMENTED_DRAFT` | PR #87/#88 + V25–V59 | 0 | `HISTORICAL_SUPERSEDED`; public activation 전 구현 상태를 재현하기 위한 truth-freeze marker |
| S4.7D v2 public runtime | `LIVE_VERIFIED` | `main=27dac2ca…`, fresh DB V59→V65 preservation validation | 재호출 0; 보존된 document `63/63`, evaluation `2/2`, production query receipt | `FULL_READY`, active `voyage_context_4_1024_v1`, sources/chunks `142/7,871`; public BGE 재실행 0; V64 scope `120/300초` 검증 완료 |
| Pre-S5 RAG/global-news lock | `CONTRACT_LOCKED` | Issue #95 addendum + Optional 3 v2 | 0 | `OA112_ACTIVE_CONTRACT_LOCKED`, `S4_7D_OA112_PHYSICAL_ACTIVATION=NOT_MATERIALIZED`; Optional 3 one-shot executor는 packet/evidence 부재 시 outbound 0 |
| Pre-S5 owner dual-profile runtime | `LIVE_VERIFIED` | `main=4e3be52c…`, V65 evidence를 V69에서 보존 검증 | owner Voyage 1회 | library별 profile 명시 선택, synthetic 9-format `9/9` import·검색·전량 hard-delete·residual 0, owner BGE local smoke·provider call 0 완료 |
| Pre-S5 foreign-news local runtime | `IMPLEMENTED_DRAFT` | current working tree V49 + packet-gated probe bridge | 0 | sanitized owner-local aggregate/read route와 Finnhub/SEC/Fed local one-shot probe/materialization bridge만 구현; selected-model·canonical packet·fresh execution evidence 전 outbound 0, GDELT HTTP transport/outbound 0 |
| S4.8A | `CONTRACT_LOCKED` | PR #75 `c17d51f6` | 0 | `S4_8A=CONTRACT_LOCKED`; provider entitlement/adapter는 미활성 |
| S4.8 Core 6 v2 | `IMPLEMENTED_DRAFT` | PR #92 contract lock + local probe runtime | 0 | `S4_8_CORE6_V2=CONTRACT_LOCKED / S4_8_CORE6_LOCAL_PROBE_RUNTIME=IMPLEMENTED_DRAFT`; KIS current-price·SEC EDGAR(2)·KRX daily(2)의 fixed local one-shot executor와 content-free receipt bridge가 있으며 fresh packet/evidence 전에는 socket 0; KOFIA blocked, OpenDART/ECOS projection-only |
| S4.8 Core 6 + Optional 3 local runtime | `IMPLEMENTED_DRAFT` | V50 + Core 6/Optional 3 packet-gated probes | 0 | V50 nine-lane typed projection은 provider 0; selected successful Core 6 receipt complete-set만 read-only로 `AVAILABLE`를 materialize하며 Optional 3도 fresh packet/evidence 전에는 socket 0 |
| S4.8B/C | `IMPLEMENTED_MERGE_CANDIDATE` | PR #77 `509d8eee` | 0 | `S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE`; fixture/scorer/V23/read port만, endpoint/RiskEngine/provider는 미구현 |
| S4.9 MCP + Strong LLM | `LIVE_VERIFIED` | PR #131 `bd859ec3`, DB V71 | PKCE 1 flow, MCP 5 tools, SearXNG search/read, Vertex Strong LLM | public-only owner isolation, answer validation, V71 direct answer/history `COMMITTED`; exact-tree Security coverage complete/findings 0과 merge-SHA CI green 확인 |
| S4.9 LangGraph + Google grounding | `LIVE_VERIFIED` | PR #131 `bd859ec3`, V70/V71 | Google autonomous search와 DDG cap fallback 실제 실행 | bounded LangGraph, Pacific-month 4,000 soft cap, source/support provenance; Google no-support는 `RETRIEVAL_ONLY`, DDG CAPTCHA는 typed fail-closed |
| [S6.1~S6.5 금융공학](decision-platform/S6_금융공학_실행_및_검증.md) | `IMPLEMENTED_DRAFT` | local atomic commits, V77, authenticated cross-process smoke | 0 | offline kernel·교육 REST·manual stored batch 구현; UI consumer와 scheduler는 범위 밖 |
| [S6.6 research replay](decision-platform/S6_무료_API_strict_PIT_가용성_판정.md) | `RETIRED_NOT_APPLICABLE` | historical schema/fixture bytes only | feasibility probe 14, runtime 0 | 무료 historical API가 행별 실제 `availableAt`을 증명하지 못해 실행 코드와 CLI 제거 |
| [S6.7 P1 overlay](decision-platform/S6_금융공학_실행_및_검증.md) | `RETIRED_NOT_APPLICABLE` | V78 historical table + V79 capability retirement | runtime 0 | materializer/reader/overlay/config 제거, endpoint·Dashboard·activation gate 없음 |

V64는 V60의 Core 6 direct-read terminal 분류, V61~V62 provider accounting과 V63 empty owner
library의 generation scope만 제한적으로 허용한다. Core 6 direct-read lane의 complete receipt set을 `AVAILABLE`, 일부 receipt만 있는 terminal set을
`ABSTAIN/DIRECT_PROBE_RECEIPT_SET_INCOMPLETE`로 Python runtime과 동일하게 수용한다. 이는 provider
권한 추가가 아니다. 현재 release terminal set은 KIS `AVAILABLE`, KRX·SEC EDGAR·OpenDART·ECOS
`ABSTAIN`, KOFIA·Finnhub·Twelve Data·Massive `BLOCKED`다. Pre-S5 main baseline은 보존됐고 S4.9
live smoke는 V71 DB에서 current LangGraph direct answer까지 성공했다. PR #131 병합, V1→V71
clean/upgrade·RLS, 전체 local gate, exact-tree 최종 Security coverage complete/findings 0과 merge SHA의
required/post-merge CI green까지 확인됐다. 따라서 `S4_READY_FOR_S5=TRUE`, `S5_ENTRY_GATE=OPEN`이다.

2026-08-15 live smoke는 Authorization Code + PKCE S256 발급·교환과 token-family revoke,
MCP `initialize`/`tools/list` 5개, public RAG search, SearXNG search 1회, exact HTTPS URL read 1회,
`capstone_answer_validate=VALID_WITH_WARNINGS`, `capstone_answer_save` 호출 0/history row 0을 확인했다.
고정 교육 질문의 Strong LLM은 local evidence가 0인 경우의 허용 경계인
`MODEL_KNOWLEDGE_ONLY`로 `ANSWERED`했고, latest usage는 provider `VERTEX_AI`, model
`gemini-3.5-flash`, prompt/output token 양수, `COMMITTED`다. 이는 evidence-backed citation 답변 성공을
뜻하지 않으며 MCP exact quote 검증 경로는 위 별도 tool smoke로 검증했다. raw token/web body/model
request/response 저장은 모두 0이고 public `142/7,871/63/2`, owner residual 0은 변하지 않았다.

V70/V71 live overlay에서는 Gemini가 Vertex Google Search를 자율 선택해 실제 query를 만들었고 usage를
`VERTEX_GOOGLE/COMMITTED`로 정산했다. provider가 source/support를 반환하지 않은 응답은 추정 citation을
만들지 않고 `RETRIEVAL_ONLY`로 닫았다. Google soft cap 격리에서는 SearXNG가 Investor.gov 결과와 bounded
read evidence를 만들고 EVIDENCE generation까지 `COMMITTED`했으며, V71은 그 `searxng_<24hex>` provenance를
history canonicalizer에 forward 결속한다. 후속 DuckDuckGo CAPTCHA/ALL_ENGINES 장애는 우회 없이 typed
failure로 종료했다.

`pre-s5-owner-voyage`와 `pre-s5-final-gate`는 ignored 0700/0600 control만 읽는 내부 operator다.
구현·focused gate 또는 live smoke 하나만으로 `S5_ENTRY_GATE=OPEN`을 선언하지 않는다. owner Voyage
one-shot과 KIS quote, S4.9 Voyage query·Vertex `COMMITTED` evidence는 완료됐고 재호출하지 않는다.
owner delete residual 0, S4.8 exact terminal set, current DB aggregate와 final Git/CI/security가 모두
일치한 뒤에만 OPEN을 계산한다.
거래시간 외 사용자 승인형 KIS V3는 동일 7단계를 provider call 0의 결정적 mock으로 검증하며,
`KIS_MOCK_AFTER_HOURS_RECONCILIATION_VERIFIED`와 physical marker를 분리한다.

Naver runtime은 퇴역했으며 재활성화하지 않는다. 추가 GDELT, Voyage, Gemini, OpenAI,
account/order 물리 호출은 별도 운영 범위 없이는 0이다. RAG는 설명·근거·citation 경계일 뿐
`RAG_DECISION_SIGNAL_ORDER_AUTHORITY=0`이며 Signal, RiskDecision, 주문 판단이나 hash를 바꾸지 않는다.

## Pre-S5 단독 실행 소유권 잠금

이 catalog가 현재 Pre-S5 public authority다. 이외의 기존 역할·일정·artifact 계획은
`HISTORICAL_SUPERSEDED`로 보존하며, 기존 workspace output이 없으면 `NOT_AVAILABLE/ABSTAIN`으로만
처리한다. 그것은 S5 진입이나 완료 marker의 의존성이 아니다.

<!-- PRE_S5_SOLO_ROLE_CATALOG_BEGIN -->
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
HISTORICAL_TEAM_ROLE_CATALOG=TEAM_B:RETURN_ENGINE|LSTM|RULE_BASELINE|BACKTEST;TEAM_A:EXPERIENCE_DASHBOARD
HISTORICAL_TEAM_ROLE_STATUS=HISTORICAL_SUPERSEDED
TEAMMATE_ARTIFACT_ABSENCE=NOT_AVAILABLE_OR_ABSTAIN
<!-- PRE_S5_SOLO_ROLE_CATALOG_END -->

Decision Platform은 기존 synthetic/offline GDELT aggregate producer를 소유한다. HTTP transport와
executor/outbound implementation은 추가하지 않으며, Naver는 계속 retired다. RAG·news·analyst는
Decision, Signal, RiskDecision, order, decision hash 권한이 0이다.

## Pre-S5 RAG·global-news active addendum

`PRE_S5_RAG_GLOBAL_NEWS_CONTRACT_LOCKED=1`

`contracts/catalogs/pre-s5-rag-news-contract.v1.json`이 historical OA140 program을 바꾸지 않는
현재 addendum이다. active logical selection은 정확히 `OA112_ACTIVE_CONTRACT_LOCKED`(14 track ×
8 = 112)이며 `S4_7D_OA112_PHYSICAL_ACTIVATION=NOT_MATERIALIZED`다. reserve research는 최대
28개이고 automatic promotion은 없다. 과거 OA112 metadata manifest, v1 OpenAPI/proto/source-card,
exact-30와 `news_sentiment_summary.v2`는 byte-stable하다.

RAG v2는 existing ask/status/history bytes를 bind하고 consent/effective-consent, 5분 single-use
owner-bound import/delete ticket, owner deletion activation/hard-delete, embedding profile policy를 addendum으로
잠근다. HTTP addendum은 consent/effective-consent/import/delete ticket과 Vertex packet preparation의 다섯
route만 추가한다. Voyage는
`voyage-context-4` 1024차원 full-generation profile이고 query별 fallback/mixed profile은 없으며,
official tokenizer 기준 110K token 이하 exact manifest-bound resumable document batch와
EXACT30/OA112 query batch 각 1회만 허용한다. tokenizer가 없으면 먼저 Voyage AI의 Hugging Face
commit `8ca946072a18e398cd61f2ad0243b56d0350b1db`에 고정된 5분·1회 bootstrap packet으로
`tokenizer.json`만 취득·검증한다. 이 observed hash 없이는 batch authoring을 시작하지 않는다.
그 뒤 한 번의 Window A 승인이 완료될 수 있도록 document/evaluation packet들은
최대 2시간 TTL이며 일반 runtime query의 5분 TTL은 유지한다. 현재 clean restart에서는 기존
namespace의 15개 committed batch·vector·attempt·checkpoint와 이전 manifest를
`HISTORICAL_SUPERSEDED`로 격리하고 같은 fresh namespace에서 성공한 결과만 재사용한다. public PII는
Document IR에서 먼저 정규화하고 canonical chunk·ID·hash·token count를 다시 만들어 checkpoint,
plan, transport, final staging 모두에서 `1..600`을 강제한다. 첫 실패 뒤 남은 provider call은 0이다.
CPU BGE public 재실행은
`TERMINALLY_SUPERSEDED_NO_FURTHER_BGE_RUN`이며 activation 대안이 아니다. 전역 public base는
`OWNER_PRIVATE` empty sentinel(`ownerScopeSha256=null`, ordered group 0)만 사용해 owner 원문을
Voyage input에 포함하지 않는다. Vertex는 local root 아래 0600 service-account JSON만 읽어 OAuth token을
1회 교환하고 `VERTEX_MODEL_ID`(기본 `gemini-3.5-flash`)의 packet-bound global publisher model로
top-5와 질문당 `generateContent` 1회만 실행하며 fallback은 0이다. ambient ADC, API key와 Gemini
Developer API는 v2 runtime에서 허용하지 않는다.
위 `TARGET_NOT_ACTIVE/provider physical call 0` 문구는 최초 activation 전 역사 상태다. current V71
fresh namespace는 public Voyage `FULL_READY`와 S4.9 Vertex Strong LLM의 latest `COMMITTED` usage를
보존한다. V1~V71 migration과 기존 generation/attempt ledger는 삭제하지 않는다.

fresh namespace는 `capstone-pre-s5-fresh`, PostgreSQL `55432`, Redis `56379`, output root
`capstone-rag/runtime/pre-s5-fresh/local-corpus`로 고정한다. BGE encoder/embedding inference와
download는 0이고 기존 local BGE tokenizer만 600-token 경계 계산에 사용한다. Window A acceptance는
`sources=142`, `chunks=7,871`, `maxTokens=600`, `documentBatches=63`이며 document 63개와
EXACT30/OA112 evaluation 2개만 묶는다. 이 값이 drift하거나 clean merge SHA·V59 fresh DB·empty-state
evidence가 없으면 manifest와 provider call은 0이다.

`S4_7D_CONSENT_TICKET_CONTROL_PLANE=OFFLINE_ONLY`는 owner-bound consent append/effective read,
5분 single-use import/delete ticket, content-free Vertex preparation만 local DB에서 수행한다. Vertex
preparation은 `req_` request ID, 같은 parsed ask command의 HMAC, provider preparation 전용 5분 opaque scope만 반환하며 owner ID·raw question·raw
evidence를 저장하거나 응답에 넣지 않는다. owner raw document/path, BAT argv, importer, materializer,
retrieval, provider outbound는 이 control plane만으로 활성화되지 않는다.

foreign-news는 Finnhub personal-local, SEC official, Federal Reserve official, existing GDELT
offline-reference lane만 정의한다. current working tree V49와 local probe bridge는 owner-local
sanitized aggregate/direct-payload read route 및 Finnhub/SEC/Fed의 one-shot packet-gated executor와
append-only materialization preflight를 구현한다. selected local model, canonical packet, fresh clean
HEAD/tree·CI/security evidence가 모두 없으면 executor socket은 0이며 GDELT HTTP transport/executor/outbound는
계속 없다.
응답은 explanation-only이며 Decision/Signal/Risk/order/hash와 S5 feature 권한이 0이고 raw provider
data/article metadata를 저장하지 않는다. SEC/Fed의 `officialReleaseLocator`는 article metadata가 아닌
허용된 sanitized provenance locator다. current working tree V50은 Core 6과 Optional 3의 정확히
9개 lane을 typed `AVAILABLE | ABSTAIN | BLOCKED` 상태와 sanitized append-only projection으로만
materialize한다. Optional 3(Finnhub Recommendation/Earnings, Twelve Data, Massive)는 v2 local
one-shot executor를 갖지만, canonical packet·fresh clean HEAD/tree·CI/security evidence 전에는
provider call이 0이다. 실행해도 one operation/one physical call/retry 0/raw persistence 0이며
Core 6, foreign-news, GDELT와 Decision/Signal/Risk/order 권한은 열리지 않는다.

`PRE_S5_DOC_TRUTH_FREEZE_VERIFIED`는 이 표와 아래 SSOT link가 EOF/lstat receipt, v1/exact-30
불변 hash, link/anchor/Mermaid 검사, 로컬 전용 reference 자료 비추적 검사까지 통과했음을 뜻한다.

S4부터 P1 종료까지의 공개 방향과 교차시장 역할·검증 순서는
[최종 프로젝트 명세서](최종_프로젝트_명세서.md),
REST/gRPC 계약은 [API 명세서](API_명세서.md), machine-readable S4 profile/policy는
[contracts README](../contracts/README.md#s4-rag-profilepolicy-catalog)를 따른다.
교차시장 계약은
[contracts README](../contracts/README.md#s48-교차시장애널리스트-계약)와
[S4.8A 변경기록](../contracts/changes/20260731-s4-8a-cross-market-contract-lock.md),
[Core 6 v2 변경기록](../contracts/changes/20260802-s4-8-core6-v2-contract-lock.md)과
[local Core 6 runtime addendum](../contracts/changes/20260810-s4-8-core6-local-probe-runtime.md)을 따른다.
S4.8B/C offline runtime과 S5.0 계약은 각각
[S4.8B/C 변경기록](../contracts/changes/20260801-s4-8b-s4-8c-offline-runtime.md),
[S5.0 변경기록](../contracts/changes/20260801-s5-0-signal-v2-contract-lock.md)을 따른다.
[S5 runtime transition 변경기록](../contracts/changes/20260815-s5-signal-runtime-transition.md)은
historical bytes를 유지한 S5.1~S5.5 fixture-first 구현, exact symbol-only GET, real dataset/model/pointer
부재와 RiskDecision/order `NO_GO`를 고정한다.
[S5.1 PIT/artifact hardening 변경기록](../contracts/changes/20260816-s5-1-pit-artifact-hardening.md)은
calendar-derived monthly schedule, 외부 manifest digest trust anchor와 실제 LightGBM cross-market
0-call/hash 격리 회귀를 고정한다.
[S5.6 production materialization 변경기록](../contracts/changes/20260816-s5-6-production-materialization-lock.md)은
reconstructed historical 등급, 측정된 horizon union 270에서 유도한 exact 7,436-call one-shot
상한, KRX→KIS→ECOS fail-stop 순서,
durable resume receipt와 source/feature bundle v2를 고정한다. S5.6A data-plane code는 구현됐고,
[2026-08-17 calendar recovery 기록](../contracts/changes/20260817-s5-bootstrap-calendar-recovery-lock.md)은
실제 KRX 4,082 physical calls, 성공 chunk 4,080개 재사용과 그 시점의 `CAPACITY_EXHAUSTED`를
봉인한다. 원 packet·두 run·4,080개 reusable chunk는 content-addressed 이중 vault에 보존했고 실제
archive 복원, inventory parity 및 provider-call 0 executor replay를 통과했다.
[2026-08-19 superseded allowance·달력 권위 기록](../contracts/changes/20260819-s5-superseded-allowance-and-calendar-authority.md)은
recovery receipt가 증명한 superseded consumed call 수만큼만 KRX 상한을 복원하는 evidence-bound
allowance를 고정한다.
[2026-08-19 제헌절 달력 교정 기록](../contracts/changes/20260819-s5-constitution-day-calendar-correction.md)은
실제 수집이 `2026-07-17`에서 멈춘 뒤 휴장일 권위로 확정한 두 번째 correction과, 단일 session 실패도
divergence 증거로 남기는 경계를 고정한다. correction set은 `2026-06-03`과 `2026-07-17` 두 개다.
[2026-08-19 correction 세대 체인 기록](../contracts/changes/20260819-s5-recovery-generation-chaining.md)은
이전 correction 세대를 해시로 보존해 recovery가 최신 소비 run에서 체인하도록 고정한다. 두 번째
correction이 왔을 때 이미 수집한 chunk가 버려지고 실제 provider 누계가 승인 상한을 넘을 뻔한 경로를
닫는다. fresh 유도식은 4,441/7,436으로 불변이므로 새 approved root는 더 큰 예산을
열 수 없고, allowance는 packet bytes·binding preimage·receipt·adoption journal 네 곳에서 재계산된다.
[2026-08-19 provider 전여 supersede 기록](../contracts/changes/20260819-s5-provider-wide-supersede.md)은
KRX만 supersede된다는 가정이 사유가 달력 correction일 때만 성립했음을 고정한다. KIS 조정 필드
가드가 실제 응답을 거부하는 동안 논리 query 하나가 물리 시도 2회를 모두 소진해 packet이 완주
불가가 됐고, 성공 chunk 채택과 소비 원장 이관을 KIS까지 넓혀 provider 호출 0으로 세대를 옮겼다.
같은 기록은 per-query 재시도 자격을 세대 안으로 한정하고(누적 예산은 superseded 소비까지 계속
센다), 값이 보존되지 않는 access token 성공을 채택 불가로 두며, 체인 head를 소비 query
다중집합에서 유도하는 경계를 함께 고정한다.
같은 기록은 거래일로 주장된 session의 빈 KRX 일별 projection을 `CALENDAR_DIVERGENCE_SUSPECTED`로
분류해 resume packet 없이 멈추는 경계와, 후보 session만 실제 `CTCA0903R`로 확정하는 최대 32-call
휴장일 권위 생산자를 함께 고정한다. 실제 model release와 pointer는 실행 receipt 전까지 0이다.
[S5.6B model release 변경기록](../contracts/changes/20260816-s5-6b-model-release-lock.md)은 exact
four-grid qualification, immutable release/exact-31 batch, V73 역할별 CAS, XKRX 휴일 clock과
수동 daily refresh의 41-call 상한을 고정한다. repository-local 구현은 실제 provider/qualification
receipt가 아니므로 실제 model과 pointer는 활성화 전까지 계속 0이다.
[foreign-news sanitized runtime 변경기록](../contracts/changes/20260809-pre-s5-foreign-news-sanitized-runtime.md),
[foreign-news one-shot provider runtime 변경기록](../contracts/changes/20260810-pre-s5-foreign-news-provider-one-shot-runtime.md),
[Voyage resumable batch 변경기록](../contracts/changes/20260812-pre-s5-voyage-resumable-batch-activation.md),
[S4.8 sanitized projection 변경기록](../contracts/changes/20260809-s4-8-runtime-sanitized-projection.md)은
current working tree 구현 경계와 physical-call hard gate를 기록한다.
exact 42개 integration target 행과 exact KIS 18개 allowlist는
Git으로 추적하지 않는 로컬 전용 자료수급 레지스트리가
authority이며 공개 문서에는 전체 목록을 복제하지 않는다.

| 문서 | 내용 |
|---|---|
| [최종_프로젝트_명세서.md](최종_프로젝트_명세서.md) | 프로젝트 전체 명세 — 방향, 시스템 구조, 모노레포 설계, 역할분담, 팀별 축소 계획(18.A) |
| [API_명세서.md](API_명세서.md) | Decision Platform API 전체 명세 — REST/gRPC 계약, 오류 코드, fail-closed 정책 |
| [RAG_외부_AI_처리_및_개인문서_동의.md](RAG_외부_AI_처리_및_개인문서_동의.md) | logical OA112·개인 문서의 외부 processor/동의/철회·삭제 및 S4.9 재동의 경계 |
| [Pre-S5 RAG/global-news contract](../contracts/catalogs/pre-s5-rag-news-contract.v1.json) | OA112 logical selection, RAG v2 addendum, foreign-news/Optional 3 execution boundary |
| [S4 RAG profile/policy 결정 기록](../contracts/changes/20260729-s4-rag-contract-catalog.md) | 정확히 두 embedding profile, 세 policy, public model 선택 금지와 negative fixture |
| [ADR-038](adr/ADR-038-naver-retirement-gdelt-aggregate.md) | Naver active 뉴스 경계 퇴역, GDELT aggregate-only ownership와 ABSTAIN 권한 결정 |
| [S1.3G 뉴스 계약 잠금](../contracts/changes/20260731-s1-3g-naver-retirement-gdelt-aggregate-lock.md) | GDELT observation/news summary v2, 기사 metadata 저장 0, 판단 권한 없음 |
| [S0_2_P0_계약_통합_필드명_결정.md](S0_2_P0_계약_통합_필드명_결정.md) | S0.2 P0 contracts 통합 기준 — order intent, signal artifact/API view, HMM regime, Return Engine export 필드명 결정 |
| [decision-platform/S1_2_OpenDART_공시위험점수_근거.md](decision-platform/S1_2_OpenDART_공시위험점수_근거.md) | S1.2 OpenDART 점수와 S1.6 offline state/quota/privacy 경계 — 공식 endpoint, 점수 한계, 신뢰성 단계 |
| [중간보고서_작성용_초기설계.md](중간보고서_작성용_초기설계.md) | 중간보고서 작성 가이드 — 문장/표/그림 초안, RISE 심사기준 대응 |
| [선물옵션_모의주문_확장_시나리오.md](선물옵션_모의주문_확장_시나리오.md) | P2 국내선물옵션 확장 설계 (v1 범위 아님, 기본 OFF) |
| [선물옵션_모의주문_확장_시나리오_API_명세서.md](선물옵션_모의주문_확장_시나리오_API_명세서.md) | P2 확장 API 계약 + KIS TR_ID 매핑 |
| [S2.2 offline 계약 변경 기록](../contracts/changes/20260724-s2-2-rule-evaluation-offline-contract.md) | 14-rule evaluator, evidence disposition, portfolio selector, bounds/hash와 S2.3 이연 경계 |
| [S2.3 Decision 계약 잠금](../contracts/changes/20260724-s2-3-decision-contract-lock.md) | 현물 OrderIntent/hash V2, stored quote/KIS_MOCK balance producer-consumer 경계, no-fake HOLD 정책 |
| [S2.4 Risk/Kill Switch 계약 잠금](../contracts/changes/20260725-s2-4-risk-kill-switch-contract.md) | owner-scoped portfolio Risk, DB singleton generation, append-only Decision 무효화와 비대칭 재가동 권한 |
| [ADR-027] | S1.4X 격리 Scala/Haskell 수치 parity Gate 0 결정 |
| `decision-platform/` | Decision Platform 공개 기술 문서 — S1.2 OpenDART 근거와 S1.6 offline 상태 경계를 포함 |

[ADR-027]: adr/ADR-027-s1-4x-isolated-numeric-parity.md

## S7–S8/P1 현재 상태 (2026-08-22)

S7.0~S7.4와 단독 수행 가능한 S8.1~S8.4는 구현·통합 검증됐다. DB adapter가 기본이고 Kafka는
선택 가능하며, S7.3은 stream metric 네 종만 소유한다. cross-market runtime/scheduler/API는
`RETIRED_NOT_APPLICABLE` 상태를 유지한다.

| 범위 | 상태 | 설명 |
|---|---|---|
| S7.0 | `VERIFIED_DB` | secure outbox/job claim, gRPC worker, Admin status |
| S7.1 | `VERIFIED_KAFKA_SELECTABLE` | KRaft/topic initializer/publisher, DB default 유지 |
| S7.2 | `VERIFIED_PYTHON_WORKER` | manual ack, processed-event idempotency |
| S7.3 | `VERIFIED_STREAM_METRICS_ONLY` | Decision/stale/failed/DLQ, cross-market scheduling 0 |
| S7.4 | `VERIFIED` | DB/Kafka failure matrix와 bounded replay CLI |
| S7.5 | `DEFERRED_P2` | 이번 production 범위 아님 |
| S8.1 | `FAKE_E2E_VERIFIED` | synthetic DB/Kafka projection parity |
| S8.1 real | `BLOCKED` | Return Engine 실물 artifact 없음 |
| S8.2 | `API_IMPLEMENTED_NO_CROSS_MARKET` | 네 ViewModel, Team A integration은 미수행 |
| S8.3 | `OFFLINE_DEMO_VERIFIED` | 별도 demo project, explicit INTERNAL_PAPER |
| S8.4 | `KIT_READY_PARTICIPANT_RUN_NOT_EXECUTED` | 실제 참가자·IRB 판단 없음 |
| P1 | `INCOMPLETE_EXTERNAL_ARTIFACT` | real artifact 검증 전 완료 주장 금지 |

운영·API handoff는
[S7–S8/P1 구현 및 운영 핸드오프](decision-platform/S7_S8_P1_구현_및_운영_핸드오프.md), 외부
artifact가 도착한 뒤 절차는
[P1 실물 artifact 잔여 체크리스트](decision-platform/P1_실물_artifact_잔여_체크리스트.md)를 따른다.
