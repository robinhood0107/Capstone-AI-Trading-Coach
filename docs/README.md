# docs

팀 배포 기준 문서. 원본 관리처는 로컬(개인 정리 폴더)이며, 갱신 시 이 폴더에 같은 파일명으로 동기화한다.

## 현재 검증 상태

이 표는 PR #92 merge `d27322cd3b4c4098577decb5bae9ec32e707050e`를 기준으로 한 Pre-S5
문서 진실 동결이다. `MERGED`는 코드가 병합됐다는 뜻일 뿐 current-HEAD 외부 호출 성공을
뜻하지 않는다. `OFFLINE_ONLY`는 fixture·local/Compose 검증만, `STUB_FAIL_CLOSED`는
의도적으로 stable error로 닫힌 공개 표면만, `CONTRACT_ONLY`는 schema/fixture만 뜻한다.
`IMPLEMENTED_DRAFT`는 current working tree에 코드·migration·자동 검증이 있으나 아직 PR/main
병합이나 provider activation을 뜻하지 않는다.
`LIVE_VERIFIED`는 exact HEAD·승인 packet·물리 호출 영수증이 있어야만 사용할 수 있으며 현재
아래 S0~S4 표에는 없다.

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
| S4.7D v2 runtime | `IMPLEMENTED_DRAFT` | PR #87/#88 + current working tree V25–V51 | 0 | local materializer·immutable bundle·profile-selected retrieval·ticket/Vertex preparation과 OA112 first-download quarantine bootstrap 구현은 검증 중; OA112 rights/cache/DB activation 전 `FULL_READY`가 아니며 Voyage/Vertex physical call은 0 |
| Pre-S5 RAG/global-news lock | `CONTRACT_LOCKED` | Issue #95 addendum + Optional 3 v2 | 0 | `OA112_ACTIVE_CONTRACT_LOCKED`, `S4_7D_OA112_PHYSICAL_ACTIVATION=NOT_MATERIALIZED`; Optional 3 one-shot executor는 packet/evidence 부재 시 outbound 0 |
| Pre-S5 foreign-news local runtime | `IMPLEMENTED_DRAFT` | current working tree V49 + packet-gated probe bridge | 0 | sanitized owner-local aggregate/read route와 Finnhub/SEC/Fed local one-shot probe/materialization bridge만 구현; selected-model·canonical packet·fresh execution evidence 전 outbound 0, GDELT HTTP transport/outbound 0 |
| S4.8A | `CONTRACT_LOCKED` | PR #75 `c17d51f6` | 0 | `S4_8A=CONTRACT_LOCKED`; provider entitlement/adapter는 미활성 |
| S4.8 Core 6 v2 | `IMPLEMENTED_DRAFT` | PR #92 contract lock + local probe runtime | 0 | `S4_8_CORE6_V2=CONTRACT_LOCKED / S4_8_CORE6_LOCAL_PROBE_RUNTIME=IMPLEMENTED_DRAFT`; KIS current-price·SEC EDGAR(2)·KRX daily(2)의 fixed local one-shot executor와 content-free receipt bridge가 있으며 fresh packet/evidence 전에는 socket 0; KOFIA blocked, OpenDART/ECOS projection-only |
| S4.8 Core 6 + Optional 3 local runtime | `IMPLEMENTED_DRAFT` | V50 + Core 6/Optional 3 packet-gated probes | 0 | V50 nine-lane typed projection은 provider 0; selected successful Core 6 receipt complete-set만 read-only로 `AVAILABLE`를 materialize하며 Optional 3도 fresh packet/evidence 전에는 socket 0 |
| S4.8B/C | `IMPLEMENTED_MERGE_CANDIDATE` | PR #77 `509d8eee` | 0 | `S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE`; fixture/scorer/V23/read port만, endpoint/RiskEngine/provider는 미구현 |

Naver runtime은 퇴역했으며 재활성화하지 않는다. GDELT, Voyage, Gemini, OpenAI, account/order
물리 호출은 새 HEAD의 승인 packet 없이는 0이다. RAG는 설명·근거·citation 경계일 뿐
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
최대 2시간 TTL이며 일반 runtime query의 5분 TTL은 유지한다. 성공 document/query batch·142개 source checkpoint는 재사용하고
첫 실패 뒤 남은 provider call은 0이다. CPU BGE public 재실행은
`TERMINALLY_SUPERSEDED_NO_FURTHER_BGE_RUN`이며 activation 대안이 아니다. 전역 public base는
`OWNER_PRIVATE` empty sentinel(`ownerScopeSha256=null`, ordered group 0)만 사용해 owner 원문을
Voyage input에 포함하지 않는다. Vertex는 `VERTEX_API_KEY`만 읽는 Vertex Express API-key-only
`gemini-3.5-flash` 단일 generator target이며 top-5와 질문당 `generateContent` 1회, fallback 0이다.
ADC/service-account, ambient credential, credential file과 Gemini Developer API는 v2 runtime에서 허용하지 않는다.
Voyage와 Vertex는 모두 `TARGET_NOT_ACTIVE`이며 provider physical call은 0이다. local DB는 V54에서
부분 BGE generation을 삭제하지 않고 terminal supersession marker와 Voyage document/query batch vector ledger를
보존한다.

`S4_7D_CONSENT_TICKET_CONTROL_PLANE=OFFLINE_ONLY`는 owner-bound consent append/effective read,
5분 single-use import/delete ticket, content-free Vertex preparation만 local DB에서 수행한다. Vertex
preparation은 `req_` request ID, 같은 parsed ask command의 HMAC, 2분 opaque scope만 반환하며 owner ID·raw question·raw
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
| [RAG_외부_AI_처리_및_개인문서_동의.md](RAG_외부_AI_처리_및_개인문서_동의.md) | logical OA112·개인 문서의 외부 processor/동의/철회·삭제 경계 — 현재 `TARGET_NOT_ACTIVE` |
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
