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

## P1 Owner-First full-app v3 현재 권위

`contracts/catalogs/p1-full-app-release-contract.v3.json`은 GitHub `1.0.0` full-app release의 현재
계약이다. 기존 `p1-offline-demo-release-manifest.v1`과 full-app v2는 역사적 회귀로 byte-stable하게
보존한다. Team A/B 수신본은 ignored `dev/upstream-intake/<manifest-sha256>`에서 원본 해시를 보존한
뒤 검토된 production source만 해당 workspace로 승격한다. cache, raw intake, untrusted pickle,
provider 원본과 local output은 승격하지 않는다.

`FINAL` release는 v3의 16개 hard gate가 모두 `PASS`일 때만 가능하다. contract-only 단계의 root
OpenAPI는 48개를 유지하고 Automation/Journal runtime PR에서 exact 56으로 전환했다. V91/1.1.0은
기존 exact-56/33 bytes를 보존한 채 예산·가변수량·손절익절 v2 다섯 operation을 더해 root exact-61,
Team A exact-38을 현재 기준으로 둔다. `BLOCKED_INCOMPLETE_RISK_BALANCE` 동안 v2 arm은 409이고
자동매매 활성화는 0이다. LightGBM은 계속 연구 전용이고 KIS Live order와 GDELT outbound는 0이다.

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
- **S7.0~S7.4와 단독 수행 가능한 S8.1~S8.4는 구현·통합 검증됐다.** DB async가 안전한
  기본 adapter이고 Kafka는 명시적 선택이며, polling/worker는 환경 opt-in이다. S7.3은 Decision
  분포·signal freshness·failed job·DLQ stream metric만 소유한다. cross-market scheduler/reader/
  materializer/`UNAVAILABLE` job 생성은 0이다. S8 Dashboard는 model evaluation, backtest, risk,
  RAG source 네 latest ViewModel만 추가하며 Experience Dashboard workspace는 수정하지 않는다.
- S8.1 synthetic artifact E2E는 `S8_1_FAKE_E2E_VERIFIED`이고 실제 Return Engine artifact를
  대체하지 않는다. 현재 `S8_1_REAL_ARTIFACT_BLOCKED`,
  `P1_OVERALL=INCOMPLETE_EXTERNAL_ARTIFACT`, `TEAM_A_INTEGRATED=FALSE`다. S8.4는 kit만 준비됐고
  참가자 실행·IRB 판단은 포함하지 않는다.
- S1.4 production은 Python/NumPy이며 runtime hot-swap을 만들지 않는다. S1.4X는 real S8.1,
  S8.4/M4, S1.4R integrity, exact 재승인과 별도 migration ADR 전까지 격리·차단한다.
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
  revision·원인 evidence를 소유한다. S5 Signal feature에는 넣지 않으며 Decision, Signal,
  RiskDecision, order authority는 없다. analyst/news/RAG/LLM도 RiskDecision과 판단 hash를
  바꾸지 않는다.
- **교차시장 계획 타당성은 `PLAN_FEASIBILITY=GO_WITH_EXTERNAL_HARD_GATES`다. S4.8A 일곱 계약은
  `S4_8A=CONTRACT_LOCKED`, S4.8B/C offline runtime은 `S4_8B_C=IMPLEMENTED_MERGE_CANDIDATE`,
  S4.8 전체 offline/storage 상태는 `VERIFIED_OFFLINE_STORED`다. S6.6/S6.7 실행 capability는
  `RETIRED_NOT_APPLICABLE`이다.** 월 데이터 비용 목표는
  `0원`이며 offline fixture와 지연/EOD를 먼저 사용한다. Bloomberg·LSEG·FactSet·코스콤과
  실시간 SOX/VIX feed는 `ENTERPRISE_ONLY_DISABLED`인 post-P1 선택지로서 P1 완주 조건이
  아니다. 기존 Spring/Python/PostgreSQL/Redis/gRPC를 재사용하고 새 agent framework,
  별도 cloud, Kafka를 이 lane의 hard dependency로 추가하지 않는다.
- 교차시장 실행의 순서 0은 `S4.READ`다. 관련 공개·private 명세의 EOF receipt와 충돌
  목록을 남긴 뒤 S4.8A **contract-only PR**이 일곱 JSON Schema,
  `s2-2-system-rule-catalog.v2`, contract-change 기록과 fixture/golden vector를 고정한다.
  이 계약 자체는 runtime 완료 증거가 아니다. S4.8A의 main 병합과 post-merge CI를 확인한
  뒤 S4.8B/C를 시작했다. 무료 historical API가 행별 실제 `availableAt`을 증명하지 못해
  strict `REAL_PIT` 입력을 만들 수 없으므로 S6.6 replay/threshold와 S6.7 WARN_ONLY runtime은
  v2 retirement 계약과 V79로 폐쇄했다. 전체 RiskEngine은 기존 exact-14 규칙을 유지한다.
- S4.8B는 provider 호출 없는 수동/offline EOD materialization, append-only 저장 경계와
  I/O 없는 결정적 `CrossMarketScorer` kernel을 소유한다. S4.8 storage/reader는 독립적인
  offline evidence 경계로 유지하며 RiskEngine이나 scheduler에 연결하지 않는다.
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
  추가하는 수는 0이다. historical S6.7 wrapper/schema는 실행 코드가 아니며 재도입하려면
  strict PIT 증거와 별도 breaking contract-change를 다시 승인한다.
  Return Engine과 Experience Dashboard placeholder에는 구현 파일을 만들지 않는다.
- S5.0 historical 계약과 generated bytes는 그대로 유지한다. S5 runtime transition은 preserved
  OpenAPI projection 아래 정확히 `GET /api/v2/signals/{symbol}` 하나와 safe artifact ingest를
  승인한다. component는 `AVAILABLE | ABSTAIN`이고 `AVAILABLE + HOLD`는 정상 예측이며,
  stale/FAIL/drift/missing evidence는 prediction/asOf/HMM state 없는 `ABSTAIN`이다. 실제 PIT
  dataset과 production model/pointer가 없으면 all-ABSTAIN만 반환한다. RiskDecision/order wiring과
  cross-market join은 계속 `NO_GO`다.
- S5.6 production clock은 pinned `exchange-calendars==4.13.2` XKRX base와 S1.6의 KIS
  `CTCA0903R.opnd_yn` field authority에서 승인·hash-bound한 correction set으로 파생한다.
  feature/label/daily batch `asOf`, stale 및 DB activation에서 calendar-date `+1 day`나 weekday
  가정을 쓰지 않고 주말·휴일·
  대체공휴일을 건너뛴 다음 XKRX session 08:10 KST를 사용한다. 2026-08-14 다음 session은
  2026-08-17이 아니라 2026-08-18이라는 회귀를 유지한다. S5.6B의 manual release/batch CAS와
  daily refresh는 RiskDecision/order 권한을 만들지 않는다. 자동 retrain과 release stage는 허용하되
  자동 activation은 금지한다.
- S5.6 provider 상한은 fresh 유도식 KRX 4,441 / KIS 기간별시세 2,970 / KIS token 1 / ECOS 24,
  합계 7,436으로 불변이다. KIS 상한은 실제 수집한 KRX 유동성 증거에서 측정한 horizon union 270과
  raw session 1,072에서 유도한다(270 × ceil(1072/100)). 승인 차원에서 유도되는 값은 리터럴로
  중복 선언하지 않는다. recovery lineage만 recovery receipt가 증명한 superseded consumed call 수와
  정확히 같은 allowance를 provider별로(KRX / KIS 기간별시세 / KIS token, 각 최대 8) 가질 수 있고,
  그 값은 packet bytes·binding preimage·receipt·adoption journal 네 곳에서 재계산된다. allowance는
  논리 query 집합을 늘리지 않으며 fresh authoring 경로는 allowance 인자를 받지 않는다. KIS allowance
  필드는 0이 아닐 때만 packet bytes에 나타나므로 이미 봉인된 세대가 계속 검증된다.
- supersede는 호출을 소비하는 모든 provider에 열려 있다. 성공 chunk는 내용과 query 신원으로
  채택하고, 결과가 이관되지 않는 것은 `SUPERSEDED_CONSUMED`로 남긴다. 값이 보존되지 않는 access
  token 성공이 그 경우이며 채택 대상이 아니다. per-query 물리 시도 상한 2회는 세대 안에서만 세고,
  이관된 superseded 소비는 누적 예산에 남지만 새 세대의 재시도 자격을 먹지 않는다. prior packet은
  세대 해시가 아니라 소비 query 다중집합으로 유도한 체인 head이며, supersede는 packet 신원을
  바꿔야 한다.
- 재검증은 `SERVING`에서 append 세션이 임계치를 넘거나 월 경계를 지날 때만 열린다. 무엇을 이미
  학습했는지의 권위는 append-only 상태 이력의 watermark이며 별도 표를 두지 않는다. 자동 재학습과
  release stage는 허용하되 활성 pointer 전환은 계속 수동 CAS다. gate 실패는 계약 위반이 아니라
  정상 상태이므로 이전 release가 계속 서빙되고, 없으면 ABSTAIN이 유지된다.
- 승인 차원에서 유도되는 값은 리터럴로 중복 선언하지 않는다. eligible session 수는 raw에서
  warm-up과 label tail을 뺀 값이고, KRX·KIS 상한은 raw session·월 수·horizon union에서 나오며,
  총 상한은 네 provider 상한의 합이고, KIS source 행 상한은 union과 raw session의 곱이다. 두
  상수가 서로를 제약하면(ECOS 요청 행 상한과 chunk 길이) 하나에서 유도한다. walk-forward 블록
  크기처럼 유도 불가한 계약 상수는 그대로 둔다. 억지로 유도하면 의미가 사라진다.
- 일일 수집분은 학습 저장소에 누적한다. `DailyInferenceState`는 추론용 bounded snapshot이고 daily
  run은 자체 source manifest를 봉인하지 않으므로 학습 누적은 별도 저장소가 필요하다. bootstrap
  packet window는 건드리지 않는다. window를 옮기면 KIS query 신원이 전부 바뀌어 승인 상한만큼
  재수집이 필요하다. append는 window 밖 새 세션만 별도 이름공간으로 쌓고 index는 append-only다.
  chunk는 경로 참조가 아니라 복사한다. daily run root를 참조하면 owner-private 컨테인먼트가 깨진다.
- 학습 window는 bundle과 append 세션의 합집합에서 유도하며 승인된 raw/eligible session 수를
  유지한다. 창이 굴러갈 때 cutoff도 새 마지막 session에서 다시 유도한다. cutoff를 그대로 두면
  append된 세션의 label maturity가 cutoff보다 늦어 PIT 경계가 깨진다. 휴장일은 달력 권위가 이미
  다음 session을 고르므로 별도 분기를 두지 않는다. 월 경계에 새로 들어온 멤버가 warm-up 역사를
  갖지 못하면 그 달만 `EVIDENCE_GAP`으로 제외하고 원장에 남긴다.
- S5 실행은 `s5-tick`이 단계 하나씩 전진시킨다. 단계는 tick이 실제로 멈출 수 있는 경계만
  둔다(`MATERIALIZING` / `QUALIFYING` / `SERVING` / `NEEDS_HUMAN`). 코드가 지킬 수 없는 구분을
  상태로 만들지 않는다. 전이는 전진만 허용하며 재검증 주기만 `SERVING`에서 `QUALIFYING`으로
  돌아간다. 상태 이력은 append-only이고 `NEEDS_HUMAN`은 사람이 되돌리기 전에는 나오지 않는다.
  tick은 멱등하며 중간 종료가 안전한 것은 progress journal이 query 단위 멱등성을 이미 보장하기
  때문이다. tick에 별도 resume 로직을 만들지 않는다.
- tick 종료 코드는 0 진척, 1 무진척, 2 사람 필요다. 무진척은 실패가 아니라 그 주기에 할 일이
  없었다는 뜻이며, 이를 실패로 보면 watchdog이 계속 울려 곧 무시된다. watchdog은 `NEEDS_HUMAN`과
  연속 무진척 임계 초과에만 말한다.
- 실패는 분류가 다음 행동을 정한다. `RETRYABLE_TRANSIENT`는 다음 tick 재시도,
  `EVIDENCE_GAP`은 그 단위만 제외, `CONTRACT_VIOLATION`과 `BUDGET_EXHAUSTED`는 정지다. 분류를
  선언하지 않은 예외는 `CONTRACT_VIOLATION`으로 fail-closed 한다. 모르는 실패를 재시도나 제외로
  넘기면 승인 호출을 태우거나 데이터를 조용히 축소한다. 분류 지식은 예외를 정의한 곳이 선언한다.
- 증거 결손으로 제외한 단위 비율이 1%를 넘으면 `NEEDS_HUMAN`이다. 증거 있는 제외라도 대규모면
  조용한 축소다. 진단은 `diagnostics.jsonl` append-only 원장 한 곳에 신원·분류·수치만 남기고
  provider 응답 조각은 담지 않는다. 기록 실패가 수집 결과를 바꾸지 않는다.
- `calendar-divergence-candidates.json`은 진단이 아니라 차단 게이트 토큰이므로 원장으로 접지
  않는다. append-only 원장은 "해소됨"을 표현할 수 없다. 토큰은 삭제 가능한 별도 파일로 두고
  사건만 원장에 미러링한다.
- 자동 재학습·qualification·release stage는 허용한다. 자동 pointer activation은 금지이며 활성
  전환은 계속 수동 CAS다. 서빙 모델은 사람 승인 없이 바뀌지 않는다.
- **2026-08-20 이후 LightGBM은 연구·재현 전용이다.** source/feature/diagnostics와 학습 코드는
  보존하지만 production Signal projection, release/batch stage, activation, daily inference/publication,
  rollback, RiskDecision, order 권한은 닫는다. production bootstrap과 `s5-tick`도 root·quota·provider
  접근 전에 연구 전용으로 종료하고 보존된 systemd unit은 설치·활성화하지 않는다. V73 audit
  schema는 삭제하지 않고 V74가 역할별 실행 권한을 회수한다. `GET /api/v2/signals/{symbol}`의
  LightGBM component는 DB row 유무와 관계없이
  `ABSTAIN/MISSING_EVIDENCE`다. KRX/KIS/ECOS data-only daily collector와 Market/Data projection은
  모델 publication에서 분리한 별도 contract-change 전까지 활성화하지 않는다.
- **S5.7A data-only 계약은 잠겼지만 runtime authority는 아직 0이다.**
  `market-data-seed.v1`, `market-data-daily-shard.v1`, `market-data-health.v1`과
  `s5-7a-market-data-lock.v1`만 current authority다. seed adoption은 기존 7,218 source chunk를
  읽기 전용으로 검증하되 raw copy·hardlink·source path 영속화를 금지하고 provider 호출은 0이다.
  daily shard는 한 XKRX session, 월중 고정 exact-31, KOSPI/KOSDAQ, ECOS 최대 2 series만 담고
  complete manifest를 마지막에 게시한다. 내부 운영 reader는 253 close, offline 연구 reader는
  1,260 XKRX session으로 제한하며 Spring Decision/Risk, public API, Dashboard, scheduler 권한은 없다.
  저장·reader·runtime·provider authority는 각각 S5.7B/C의 별도 구현과 검증 전에는 미구현이다.
- 2026-08-20 S5 qualification continuation은 초기 fit-only 근거로 라벨 경계를 `0.025`로 옮긴 뒤
  승인된 5회 조정(grid 정규화, block 재배분, fit-only label 두 값, macro split-gain 0)을 실측했다.
  마지막 시도는 macro disagreement를 모든 grid/fold에서 `0.0000`으로 만들었지만 fold-3 OVR Platt가
  `dLogLoss +0.0207..+0.0342`, `dBrier +0.0106..+0.0190`으로 실패했다. threshold·gate·final test를
  완화하거나 재열람하지 않고 `REAL_MODEL_AVAILABLE=FALSE`, `PRODUCTION_POINTER=0`을 유지한다.
- 같은 날 별도 승인된 calibrator 계약 probe는 final candidate 선택을 강제로 닫은 복제 root에서
  temperature scaling과 21-session identity-regularized bias-temperature scaling을 현재 feature와
  macro split-gain 0 각각에 적용해 정확히 4회 실측했다. temperature는 fold-3 `dLogLoss`를 모두
  `-0.0039..+0.0065`로 만들었지만 fold-2 ECE가 `0.0515..0.0876`이었고, regularized variant는 일부
  fold-2 ECE를 통과시키는 대신 fold-3 또는 corporate-action subset을 실패했다. 전 실행에서 provider
  INTENT `7,230`, `finalTestAccessCount=0`, release/batch 0을 유지했으므로 실패한 calibrator를 제품
  계약에 넣지 않는다. 다음 재검증은 append session 21개 또는 월 경계 evidence가 열 때만 수행한다.
- 종목별 KIS 커버리지 요구는 수집된 KRX 일별 거래 증거와의 정확한 일치다. 전 종목이 전 구간을
  거래한다고 단정하지 않는다. 상장폐지·신규상장으로 끝이 잘리는 것은 허용하되 중간 결손은
  거부하며(rolling window가 위치 기반이라 의미가 바뀐다), 거래량 0 세션에는 시가가 존재하지
  않으므로 raw 시가 항목을 만들지 않는다. paging은 증거를 다 받으면 멈춘다. 응답 모양만으로는
  역사가 100의 배수로 끝날 때 "더 없음"을 구분할 수 없다.
- 거래일로 주장된 session의 빈 KRX 일별 projection은 일반 실패가 아니라
  `CALENDAR_DIVERGENCE_SUSPECTED`다. 후보 session을 content-free sidecar로 남기고 resume packet을
  만들지 않으며, 해소되지 않은 block은 다음 실행을 provider client 앞에서 멈춘다.
- 인접 session이 정상인데 한 session만 실패하면 `SINGLE_SESSION_QUERY_FAILURE` 후보 증거를 남긴다.
  provider 일시 오류와 구분할 수 없으므로 이 증거는 계약이 허용한 resume을 막지 않으며, 운영자는
  별도 예산의 휴장일 권위로 먼저 확인한 뒤 재개하거나 correction을 추가한다. block bytes는 packet과
  후보 집합에만 의존하고 누적 회계는 append-only journal이 단독 권위다.
- 달력 correction set은 packet 해시 결정성을 위해 정적 상수로 유지하되, 후보 session만 실제 KIS
  `CTCA0903R`로 확정해(최대 32 calls, live 전용, bootstrap 예산과 분리) `trading_sessions`에 적재하고
  상수와 대조한다. 관측이 없으면 통과가 아니라 `CALENDAR_AUTHORITY_UNVERIFIED`다.
- correction 세대는 해시로 식별하고 이전 세대를 삭제하지 않는다. packet은 자신이 author된 세대를
  bytes에 선언하며, 그 선언이 이전 달력으로 돌아가는 유일한 경로다. production 실행은 언제나 현재
  세대만 받고, recovery만 read-only로 이전 세대를 연다. recovery는 historical v1뿐 아니라 이전 세대의
  recovery packet에서도 체인해 이미 수집한 chunk를 재수집하지 않으며, prior journal은 그 journal이
  봉인된 세대의 clock으로 읽는다. 현재 세대 packet은 자기 자신을 supersede할 수 없다.
- bootstrap 실행은 provider client 생성 전에 quota backend credential을 확인한다. credential 값은
  출력·저장하지 않으며 실패 시 provider 호출은 0이다.
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
  query batch 각 1회만 허용한다. local tokenizer가 없으면 fixed Voyage AI Hugging Face commit의
  `tokenizer.json` 한 파일을 5분·physical cap 1의 exact bootstrap packet으로 먼저 취득하며,
  observed hash 없이는 batch authoring을 시작하지 않는다. Window A packet TTL은 document/evaluation
  batch 종류에만 최대 2시간이고 일반
  runtime query의 5분 TTL은 유지한다. 현재 clean restart에서는 기존 실행 namespace의 15개
  committed batch·vector·attempt·checkpoint와 manifest를 `HISTORICAL_SUPERSEDED`로 격리하고
  count/hash/resume에 사용하지 않는다. public PII는 Document IR에서 먼저 정규화한 뒤 canonical
  chunk·ID·hash·token count를 다시 만들며 profile-neutral token count `1..600`을 checkpoint,
  plan, transport, final staging 모두에서 강제한다. BGE encoder/embedding inference와 download는
  0이고 기존 local BGE tokenizer만 이 600-token 경계 계산에 사용할 수 있다. 같은 fresh namespace에서
  성공한 document/query batch·source checkpoint만 재호출하지 않는다. 모든 batch와
  평가가 끝나기 전 CAS activation은 0이다. RAG v2 consent/import와 이 local batch runtime은
  구현돼 있지만 provider activation은 아니다. Vertex는 local-only 0600 service-account JSON으로 OAuth token을
  한 번 교환하고 `VERTEX_MODEL_ID`(기본 `gemini-3.5-flash`)의 exact packet-bound model을 한 번 호출하며,
  foreign-news(Finnhub personal-local/SEC/Fed/GDELT offline reference)는
  계속 hard-gated다. Optional 3과 Core 6의 KIS current-price·SEC EDGAR submissions/companyfacts·KRX
  KOSPI/KOSDAQ daily만 local one-shot executor를 가진다. canonical short-expiry packet은 실행
  입력의 무결성과 물리 호출 상한을 검증하는 감사 경계로 사용하며, HEAD/tree 또는 CI/security
  digest가 바뀌어도 사용자가 정한 실행 권한은 승인 범위 동안 유지된다. KIS는 cached
  OAuth token이 없으면 token endpoint를 열지 않고 fail-closed한다. Core 6 availability는 성공한
  content-free receipt의 complete required-operation set으로만 materialize하며 KOFIA는 계속
  `BLOCKED_NO_CREDENTIAL_OR_APPROVAL`, OpenDART/ECOS는 authorized projection-only다. raw corpus,
  raw provider data, article metadata, Decision/Signal/Risk/order/hash/S5 feature authority는 계속
  0이고 Core 6 exact set을 넓히지 않는다.

  current public RAG는 fresh namespace에서 `FULL_READY`, active profile
  `voyage_context_4_1024_v1`, sources/chunks `142/7,871`, document batches `63/63`을 보존하며 다시
  실행하지 않는다. owner library profile은 사용자가 import-ticket v2에서
  `voyage_context_4_1024_v1 | bge_m3_local_1024_v1` 중 하나를 반드시 고른다. default·자동 판단·자동
  fallback은 0이고 profile 변경은 모든 owner 문서 hard-delete 뒤 새 import로만 가능하다. public BGE
  inference는 계속 0이며 owner BGE만 user-selected local execution으로 허용한다.

  S4.9 Strong LLM current authority는 `contracts/catalogs/s4-9-mcp-strong-llm-contract.v2.json`이다.
  Kotlin은 인증·owner 동의·Top-5 retrieval·Google 월별 budget·SearXNG/URL reader·최종 검증·DB
  ledger를 소유하고, Python LangGraph는 Vertex provider 대화와 bounded tool state만 소유한다.
  Google Search grounding은 Gemini가 필요 여부와 query를 선택하되 한 prompt당 grounded provider call은
  최대 1회, Pacific 월 local soft cap은 4,000 query, overage는 0이다. cap 차단 시 내부 SearXNG의
  DuckDuckGo 단일 engine으로만 best-effort fallback하며 CAPTCHA 우회, FlareSolverr, browser automation,
  Google/Naver scraping은 추가하지 않는다.

  Google grounding URI는 provider support metadata로 검증하며 서버가 자동 GET하지 않는다. SearXNG 결과,
  사용자 질문의 공개 HTTPS URL, 읽은 문서에서 실제 발견한 링크만 `resultId` provenance graph를 거쳐
  bounded reader가 접근한다. 미등록 model URL, private/link-local 주소, DNS rebinding, credential/cookie는
  계속 차단한다. owner text는 Google discovery에 전달하지 않으며 갱신 동의가 있는 tool-free final call에만
  포함할 수 있다. RAG/MCP/LLM 결과의 Signal/downstream prediction/RiskDecision/order/hash authority는 0이다.

  clean restart의 local namespace는 Compose project `capstone-pre-s5-fresh`, PostgreSQL host port
  `55432`, Redis host port `56379`, output root `capstone-rag/runtime/pre-s5-fresh/local-corpus`로
  고정한다. 기존 OA112·EXACT30·tokenizer source root는 read-only이고 output root와 명시적으로
  분리한다. V1→V59 fresh DB와 empty-state evidence를 확인한 clean merge SHA에서만 Window A를
  authoring하며 예상 집계 `sources=142`, `chunks=7871`, `maxTokens=600`, `documentBatches=63`이
  하나라도 다르면 provider call 0으로 `PRE_S5_FRESH_PLAN_DRIFT` 종료한다. Window A에는 document
  63개와 EXACT30/OA112 evaluation 2개만 포함하고 production query·시장 provider·Vertex·KIS_MOCK은
  포함하지 않는다.

## Pre-S5 단독 실행 소유권 잠금

이 절은 현재 Pre-S5 실행 authority다. 아래와 다른 공개 문서의 기존 역할·일정·artifact 계획은
재현을 위한 `HISTORICAL_SUPERSEDED` 기록으로만 보존하며, 과거 ADR·contract-change·완료 evidence의
bytes를 변경하지 않는다. 현재 존재하지 않는 기존 workspace output은 `NOT_AVAILABLE/ABSTAIN`으로만
처리하고 S5 진입 또는 완료의 의존성으로 만들지 않는다.

### 외부 실행 승인 정책

- 사용자가 이름이 붙은 phase, window 또는 실행 계획 전체를 승인하면 그 계획에 적힌 provider,
  operation, 계정 mode, 물리 호출·retry·비용 상한이 승인 범위가 된다. packet별 승인을 다시 묻지 않는다.
- 사용자가 provider, operation 범위, 계정 mode, 최대 물리 호출 수와 retry/cost 상한을 명시해
  사전 승인하면 manifest SHA가 생성되기 전의 승인도 유효하다. manifest SHA는 실행 입력의
  무결성과 감사 추적을 위한 식별자이며, 생성 뒤 SHA 응답을 기다리는 별도 단계는 두지 않는다.
- 승인된 범위 안에서 packet이나 manifest를 재생성하거나 HEAD/tree·CI·Security evidence가 바뀌어도
  승인은 유지된다. 에이전트는 최신 입력을 다시 검증하고 실행을 계속한다.
- provider·endpoint·전송 데이터 범위·계정 또는 주문 mode·최대 물리 호출 수·retry·비용 상한 중
  하나라도 사용자가 승인한 범위를 넓힐 때만 새 승인을 받는다. credential, fixed origin, PII/secret,
  quota, raw artifact 금지와 첫 실패 뒤 후속 호출 0 경계는 승인 방식과 무관하게 유지한다.
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
- `workspaces/return-engine/`: Team B 수신본의 검토된 one-shot consumer와 재현 가능한 artifact 코드만 승격한다. provider client, cache, raw CSV와 출처 없는 pickle은 금지한다.
- `workspaces/experience-dashboard/`: Team A 수신본의 검토된 same-origin production source, lockfile, 테스트와 Docker 경계만 승격한다. mock/dev output은 production image에서 제외한다.
- 두 workspace의 README-only placeholder 규칙은 `HISTORICAL_SUPERSEDED`다. ignored `dev/upstream-intake/`는 원본 보존용이며 Git 추적·production build context·release archive에 포함하지 않는다.
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
- RED→GREEN→REFACTOR와 실패 테스트 선작성을 기본·필수 흐름으로 사용하지 않는다. 기본 순서는
  `계약 확인 → 최소 구현 → 가장 가까운 focused 검증 → 부족한 회귀 테스트 보강 → release 전체 gate 1회`다.
- 기존 테스트가 변경 동작과 회귀 위험을 충분히 검증하면 중복 테스트를 추가하지 않는다. 새 동작,
  보안 경계 또는 재발 가능성이 기존 coverage에 없을 때만 최소 테스트를 추가한다.
- 구현과 그 동작을 검증하는 테스트는 같은 `feat|fix` 커밋에 포함할 수 있다. 별도 test-only 선행
  커밋은 복잡한 회귀 계약을 독립적으로 고정해야 할 때만 사용한다.
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
- 외부 작업을 독립 endpoint로 분해할 수 있고 production transport/parser/quota 경계를 그대로 재사용할 수 있을 때만 단일 endpoint·무게시 probe를 추가한다. 순서는 `계약·실패 leaf 확인 → 최소 수정 → focused/관련 matrix → release 전체 gate 1회 → 승인 범위 내 새 packet 발급 → probe 단계별 실행 → 최종 원자 실행`이다. 이미 사전 승인된 범위라면 packet이나 manifest 생성 뒤 별도 SHA 승인 왕복 없이 계속한다.
- probe는 기본적으로 retry `0`, endpoint별 physical cap `1`, artifact `0`이며 첫 실패 뒤 남은 provider 호출은 `0`이다. probe 성공은 accepted production 결과가 아니고, 최종 명령이 현재 응답 전체를 독립적으로 다시 검증해 성공한 뒤에만 원자 publish한다. probe와 최종 응답 hash 일치를 강제하지 않는다.
- 외부 provider 성공을 보장한다고 표현하지 않는다. `curl`, 브라우저 sample, 임시 script로 credential·fixed-origin transport·quota·사용자가 승인한 실행 범위를 우회하지 않고 실패 evidence와 성공 acceptance set을 분리한다.

## S5.7B 중립 Market Data 현재 권위

- S5.7B는 provider-free adoption만 구현한다. `s5-research-market-data-export`는 봉인된 S5 source
  manifest/chunk만 읽고 feature, label, final test, release, Signal batch는 읽지 않는다.
- 운영 모듈 `app.data.market_data`는 `app.lightgbm`을 import하지 않는다. 내부 운영 reader는 현재
  exact-31의 최대 253 close, 연구 reader는 최대 1,260 session만 반환하며 provider-on-read는 0이다.
- V75의 normalized manifest/bars/indices/macro/universe는 append-only다. 같은 session·generation·SHA는
  no-op, 다른 SHA는 `NEEDS_HUMAN`, correction은 직전 generation을 `supersedes_sha256`으로 결속한다.
- correction view는 `(identity, session)`별 가장 높은 generation만 먼저 선택한 뒤 253/1,260 session
  상한을 적용한다. stage는 운영자가 지정한 exact manifest SHA가 archive와 일치해야 DB를 연다.
- `decision_market_writer`는 INSERT만, operational/research/retention role은 서로 다른 NOLOGIN capability다.
  `decision_app`과 Spring Decision/Risk에는 두 history reader 권한을 주지 않는다.
- ECOS active retention은 최대 365일이고 entitlement 종료가 더 빠르면 그 날짜를 따른다. 삭제는 별도
  retention role의 명시적 apply에서만 가능하며 `market-data-retention` 기본 모드는 dry-run이다.
- 실제 adoption 기준 source는 7,218 chunk, historical INTENT 7,230이다. normalized archive는 bars
  267,788, indices 2,144, macro 3,042, universes 1,581행이며 provider 호출은 0이었다. 역사 union에는
  KRX 영숫자 단축코드 1개가 있으므로 연구 저장은 6자리 영숫자를 보존하되 현재 exact-31은 숫자 31개다.
- 이 데이터의 전체 품질은 `RECONSTRUCTED_FIXED_LAG`이며 strict-PIT 성과 주장, LightGBM Signal,
  RiskDecision, order 권한은 계속 0이다. public Market Data API와 scheduler는 구현하지 않는다.

## S5.7C 수동 Market Data runtime 현재 권위

- `market-data-daily-replay`는 `OFFLINE_REPLAY_ONLY` packet과 owner-private sealed record만 읽는 수동
  명령이다. KRX/KIS/ECOS HTTP client나 credential을 생성하는 live adapter는 존재하지 않으며 provider,
  account, balance, order physical call cap은 모두 0이다.
- 실행 순서는 packet·zero-cap·pinned XKRX correction attestation·writer role preflight 뒤 replay root를
  여는 방식으로 고정한다. 정상 session은 유도된 5 KRX + 31 KIS + 2 ECOS = 38 operations, 실제 월 경계는
  3 KRX monthly operations를 더한 41 operations다. 중간 월 membership은 직전 membership SHA와 같아야 한다.
- 각 성공 operation은 content-addressed immutable staging과 fsync journal에 즉시 남긴다. 첫 결손 뒤 남은
  operation은 읽지 않으며 resume은 staging 성공분을 재사용한다. required set과 packet receipt-set SHA가
  모두 맞아야 DB transaction을 열고 `daily-shard.json`을 마지막에 게시한다. partial/divergence/binding
  mismatch는 accepted manifest와 DB row를 만들지 않는다.
- 휴장·비거래일은 `NO_NEW_SESSION`, 다음 XKRX session 08:10 KST 전에는
  `WAITING_FOR_EVIDENCE_CLOCK`, 빈 KRX daily projection은 `CALENDAR_DIVERGENCE_SUSPECTED`다.
  `2026-08-14` 다음 evidence session은 `2026-08-18`이라는 회귀를 유지한다.
- S5.7C는 scheduler, health worker activation, public REST/OpenAPI/Dashboard, live provider authority를
  추가하지 않는다. S6은 저장된 operational/research reader만 사용하고 LightGBM Signal은 계속
  `ABSTAIN/MISSING_EVIDENCE`, RiskDecision/order authority는 0이다.

## P1.V0 provider-0 검증 하네스 현재 권위

- `contracts/catalogs/p1-verification-catalog.v1.json`은 profile별 구현 상태, 필수 gate와 provider
  authority를 분리하는 SSOT다. `implementationState`와 `executionState`를 합쳐 쓰지 않으며 전체
  결과도 `PASS | FAIL | BLOCKED | INCOMPLETE` 중 하나로 보고한다.
- `p1-verify run --profile S0_S5_CURRENT`는 provider 권한 없이 두 독립 lane을 검증한다. Market Data
  lane은 sealed replay→staging/journal→V75/V76→253/1,260 reader를, Decision lane은 sanitized fixture
  writer→Principle→Decision→INTERNAL_PAPER→fill/reconciliation을 확인한다. 이는 단일 live
  S0~S5 black-box 파이프라인이 아니다.
- V76은 daily INSERT를 직렬화하고 DB가 인정한 직전 accepted manifest SHA와 packet의 predecessor가
  같은지 preflight와 trigger에서 모두 확인한다. writer에는 base table SELECT를 주지 않고 bounded
  `SECURITY DEFINER` head 조회만 허용한다. DB commit 뒤 local manifest 게시 전 종료는 재실행에서 DB
  `NO_OP` 후 누락 manifest만 게시해야 한다.
- S5.7의 `38/41`은 sealed replay operation 수이고 provider physical call 수가 아니다. 현재 offline
  shard/health의 provider physical call은 정확히 0이다.
- P1.V0 report는 raw body/header/token/credential/URL/실제 시장값이나 명령 출력을 저장하지 않고 gate
  상태·physical count·content-free evidence SHA만 저장한다. owner-private report는 Git 밖에 둔다.
- 현재 판정은 `S0_S5_CURRENT=PASS`, `PROVIDER_READ_SMOKE=NOT_IMPLEMENTED`,
  `FULL_LIVE_DAILY_COLLECTOR=NOT_IMPLEMENTED`, `S6_OFFLINE=NOT_IMPLEMENTED`,
  `P1_OVERALL=INCOMPLETE`, `LIGHTGBM_PRODUCTION=INTENTIONALLY_DISABLED`다.
- `p1-verify author`는 clean HEAD/tree/lock/catalog과 TTL 최대 60분을 결속하는 packet만 만들며 provider
  실행 권한을 구현하지 않는다. P1.V1 live adapter가 별도 PR로 병합되고 CI가 성공하기 전 live smoke는
  실행하지 않는다. account, balance, order cap은 모든 profile에서 0이다.

## P1.V1 격리 provider read smoke 현재 권위

- 위 P1.V0 절의 `PROVIDER_READ_SMOKE=NOT_IMPLEMENTED` 문구는 역사 상태다. 현재 catalog 구현 상태는
  `IMPLEMENTED`이고 수동 `p1-verify run --profile PROVIDER_READ_SMOKE --packet ...`만 제공한다.
- live 실행은 clean merged SHA에서 발급한 TTL 최대 60분 packet을 owner-private one-shot claim ledger에
  먼저 소비한 뒤에만 가능하다. 같은 packet은 성공·실패·차단 여부와 무관하게 다시 실행하지 않는다.
- 순서는 KRX KOSPI 1 → KRX KOSDAQ 1 → KIS cached token 확인/필요 시 token 1 → KIS 현재가 1 →
  KIS 일봉 1 → ECOS 기준금리 1 → ECOS 원/달러 1이다. data physical cap은 6, token cap은 0 또는 1,
  provider retry/retransmission은 0이다.
- 첫 terminal 실패 뒤 남은 gate는 모두 `NOT_RUN`이다. report에는 gate 상태, physical count와
  content-free evidence SHA만 남기고 raw body/header/token/credential/URL/실제 관측값은 남기지 않는다.
- account, balance, order, product DB write, accepted Market Data manifest 변경은 0이다. 이 smoke 성공은
  current credential·quota·fixed endpoint·transport·parser health만 뜻하며 S5.7 live daily collector,
  S6, LightGBM, RiskDecision, order, scheduler activation 증거가 아니다.
- CI와 fixture 검증의 provider 호출은 계속 0이다. 실제 smoke 결과는 adapter PR 병합과 post-merge CI
  성공 뒤 발급한 packet의 실측 report만으로 갱신한다.

## P1.V1 격리 provider read smoke 실측 현재 상태

- 2026-08-21 clean merged SHA `80ff5fae1b65d2d181497538623657dd664e6958`의 PR 및 post-merge
  CI 성공 뒤 TTL 60분 one-shot packet `7a5e180b5a6b2066bd74a32a669ac7565c07cc819646388a00caf583e79eed3c`를
  정확히 한 번 실행했다. evidence clock으로 선택한 대상 XKRX session은 `2026-08-20`이다.
- content-free report SHA는 `7aefeb03d3ce66b5d16b50ee25031ef38b7c34bf9f0aabea6c464d82d587c938`이며
  KRX KOSPI, KRX KOSDAQ, KIS 현재가, KIS 일봉, ECOS 기준금리, ECOS 원/달러 여섯 gate가 모두
  `PASS`했다. data provider physical call은 정확히 6회, KIS token은 cache miss로 1회였고
  retransmission은 0이다.
- account, balance, order call과 product DB write는 각각 0이다. raw body/header/token/credential/URL과
  실제 시장값은 report에 남기지 않았다. 같은 packet은 claim이 소비했으므로 재실행하지 않는다.
- 이 결과로 `PROVIDER_READ_SMOKE_EXECUTION=PASS`만 갱신한다. `FULL_LIVE_DAILY_COLLECTOR`,
  `S6_OFFLINE`은 계속 `NOT_IMPLEMENTED`, LightGBM production은 `INTENTIONALLY_DISABLED`,
  `P1_OVERALL`은 `INCOMPLETE`다.

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
- 모든 코드 변경에는 동작 검증 근거가 있어야 한다. 기존 테스트가 충분하면 해당 focused 명령과 결과를
  재사용하고, coverage가 부족할 때만 최소 테스트를 추가한다. 외부 API·시각적 확인·수동 smoke처럼
  자동화가 어려운 부분도 fixture, mock, 계약 검증, smoke 명령 중 하나로 재현 가능하게 남긴다.
- 테스트 삭제·skip·기대값 완화로 통과시키거나 migration/RLS/API parity gate를 생략하지 않는다.
  가설 수정마다 전체 pytest·Gradle·CI를 반복하지 않고, 변경 지점의 가장 가까운 검증부터 실행한 뒤
  동결된 release tree에서 전체 gate를 한 번 수행한다.
- 변경 유형별 기본 검증은 문서·규칙은 `git diff --check`·링크·hygiene, Python/Kotlin 로직은
  해당 모듈 focused test와 Ruff/mypy 또는 ktlint, DB migration·ACL은 migration contract와 RLS/security
  integration, provider transport는 deterministic fixture와 socket 0 preflight, 공개 계약은 generator와
  OpenAPI/proto byte parity다. release 후보만 전체 local gate와 required CI를 각각 한 번 수행한다.
- 구현과 관련 테스트는 같은 기능 커밋에 둘 수 있지만 Markdown·AGENTS·명세서·규칙 변경은 코드와
  분리한다. 리뷰에서는 PR 본문과 검증 evidence로 구현 범위와 통과한 테스트를 명확히 연결한다.
