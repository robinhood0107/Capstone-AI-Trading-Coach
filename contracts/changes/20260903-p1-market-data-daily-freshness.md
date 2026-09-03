# P1 시장데이터 일일 최신성 — 수집 배관과 e2e 누수 봉쇄 (2026-09-03)

이전 기록(`20260903-p1-daily-operation-verification.md`)이 "남은 결정"으로 남겨 둔 최신성 문제를
닫는다. 그리고 그 확인 과정에서 실기동을 실제로 망가뜨릴 결함 하나를 더 찾아 함께 닫는다.

기존 `contracts/changes/` 파일은 수정하지 않고 신규 파일만 추가한다.

## 1. 문제 — 가용성이 아니라 최신성이었다

자동운용 루프는 매일 돌지만 시장데이터를 스스로 갱신하지 않았다. `market_data_bars` 를 쓰는
production 경로는 `repository.adopt_daily_shard` 하나이고 그것을 부르는 것은 운영자 명령뿐이었다.

루프가 멈추지는 않았다. 소스 세션과 이력의 마지막 바가 **같은 manifest 상태에서 파생**되므로
함께 뒤로 밀린다. 실측으로 확인했다 — manifest 가 2026-08-28 하나뿐인 상태에서 09-07 / 09-08 /
10-05 추론이 모두 정상 완료됐다. 그래서 매일 같은 창으로 같은 신호가 나왔다.

그것을 걸러내는 게이트도 없다. `daily_ready`(V117:79-85)의 조건은 `session_date <=
claim.session_date` 와 `as_of <= claim.session_date + 09:20 KST` 상한뿐이라 **오래된 manifest 는
항상 통과한다.** 과제한의 거울상인 과관용이다.

## 2. 해결 — `app/data/market_data/yfinance_daily_cli.py` (신규)

배관만 잇는다. 새로 만든 것이 거의 없다.

| 필요 | 재사용한 것 |
|---|---|
| 적재 | `repository.stage_daily_shard` → `adopt_daily_shard`. 기존 production writer. manifest 체인 확인·writer role 강제·한 트랜잭션 삽입을 그대로 쓴다 |
| 종목 | `contracts/catalogs/p1-return-universe.v1.json` 의 exact-31 과 `yfinanceTicker`. 티커 변환을 다시 만들지 않는다. 이미지에 `/app/contracts/catalogs` 가 함께 들어 있음을 확인했다 |
| 기준점 | `current_market_data_manifest_head(date)` — SECURITY DEFINER 이고 writer 에게 EXECUTE 가 있다. production writer 가 체인 확인에 쓰는 그 함수다 |
| 세션 | 오프라인 XKRX 달력(`build_xkrx_sessions_in_range`). 순수 `exchange_calendars` 로 네트워크도 DB 도 쓰지 않고, `trading_sessions` 를 채운 것과 같은 출처라 결과가 구성상 일치한다 |
| 실행 | 기존 `market-data-cli` compose 서비스. 새 서비스도 새 secret 도 만들지 않았다 |
| HTTP | 이미 있는 `httpx`. **`yfinance` 패키지를 넣지 않았다** — 일봉 하나 받으려고 `curl_cffi`/`beautifulsoup4`/`peewee` 를 production 이미지에 끌고 들어올 이유가 없다 |

### 역할 분리를 넓히지 않았다

실측: `decision_market_writer` 는 어떤 표에도 **SELECT 가 없다**(INSERT 전용). `market_data_bars`
는 **어떤 role 도 SELECT 하지 못하고** 운영 명부는 전용 reader 만 읽는다. 이것은 사고가 아니라
안전 경계다.

처음 설계는 `trading_sessions` / `market_data_bars` / `market_data_operational_universe` 를
읽으려 했고 `DATABASE_UNAVAILABLE` 로 닫혔다. **권한을 넓혀서 통과시키지 않고** 읽기를 위 표의
`current_market_data_manifest_head` + 오프라인 달력 + 커밋된 카탈로그로 바꿨다. 결과적으로
**SELECT 가 한 건도 없다.**

같은 이유로 자동운용 런타임에 writer DSN 을 주지 않았다. `serve()`(automation_runtime.py:862)가
매 세션 `ensure_daily_signals` 직전에 깨어나므로 그 자리가 편하지만, 그러면 **거래하는 앱
컨테이너가 시장데이터를 위조할 수 있다.** 수집은 writer 를 가진 `market-data-cli` 에 남기고
호스트가 부른다.

### 정직성

출처는 공개 지연 피드다. 그래서 bar 의 `temporalQuality` 를 `COLLECTION_ONLY` 로 남긴다 —
공식 시세 vintage 라고 주장하지 않는다. `as_of` 는 해당 세션의 장 마감(15:30 KST)으로 둔다.
받은 시각이 아니라 데이터가 가리키는 시각이 그것이고, 다음 세션의 09:20 상한도 자연히 만족한다.

한 세션은 exact-31 이 모두 모일 때만 적재한다. 계약을 만족하지 않는 봉(결손, 0 이하 가격,
high < max(open,close), low > min(open,close))은 **고치지 않고 버리고**, 그 세션은 31 이 모이지
않아 자연히 멈춘다. 값을 억지로 맞추는 것이 가장 나쁜 결과다.

### 실측

```
적재     P1_MARKET_DATA_YF_REFRESH=ADOPTED sessions=3
         2026-08-31:INSERTED(31), 2026-09-01:INSERTED(31), 2026-09-02:INSERTED(31)
         symbols=31 temporalQuality=COLLECTION_ONLY providerCalls=31
체인     2026-08-28 AUTOMATION_BOOTSTRAP (root)
         → 2026-08-31 DAILY → 2026-09-01 DAILY → 2026-09-02 DAILY   전부 ACCEPTED
바       3,100 → 3,193   세션당 31종목
멱등     두 번째 실행 UP_TO_DATE sessions=0 providerCalls=0
연속성   005930  08-28 종가 257,000 → 08-31 시가 249,000. 하루 30% 초과 급변 0건
소스     일일 추론의 소스 세션이 2026-08-28 → 2026-09-02 로 전진
KIS      계좌·주문 호출 0. 공개 일봉 피드만 사용
```

### 배선

`up_app` 이 캘린더 seeding 직후에 부른다. 마커 줄을 직접 출력해 무엇이 적재됐는지 보이게 하고,
**실패해도 `up` 을 죽이지 않는다** — 시장데이터가 낡은 것은 루프를 멈추게 하지 않고 사람이 보면
되는 일이다. `CAPSTONE_MARKET_DATA_DAILY=FRESH|STALE` 로 요약한다.

운영자 명령 `./capstone market-data refresh` 도 함께 둔다. 멱등이므로 언제 불러도 안전하다.

**남는 운영 요건(정직한 공시)** — 스택을 여러 날 켜 둔 채로는 호스트가 하루 한 번 이 명령
(또는 멱등인 `./capstone up`)을 불러야 한다. 상주 컨테이너를 하나 더 늘리면 자동화되지만
`CAPSTONE_PERSISTENT_CONTAINERS=5` 계약이 깨지므로 그 결정은 하지 않았다.

## 3. 함께 찾은 결함 — e2e 배치가 실기동의 결정 입력이 될 수 있었다

최신성을 확인하다 발견했다. **이쪽이 더 위험하다.**

`p1_read_daily_inference_context_v1` 은 `(bundle_sha256, target_session)` 이 `COMPLETE` 이면
조건 없이 `outcome=REPLAYED` 를 돌려주고, `ensure_daily_signals` 는 추론을 건너뛴다.

관통 하네스가 `p1_return_daily_signal_batch` 와 그 projection 을 **정리 목록에 넣지 않았다.**
그래서 e2e 가 만든 `target 2026-09-04` 배치가 실물 bundle 로 `COMPLETE` 로 남았고, 그것이
참조하는 manifest 는 정리 대상이라 사라져 dangling(0행)이 됐다.

즉 **내일 실제 2026-09-04 세션이 e2e fixture 신호 62행을 그날의 결정 입력으로 쓸 상태였다.**
가용성 문제가 아니라 진위 문제다. blocker 에도 나타나지 않는다.

실측으로 미래 세션을 겨냥한 배치 4건을 확인했다 — 09-04(dangling), 09-07 / 09-08 / 10-05(소스가
2026-08-28 로 낡음). 전부 오늘 테스트가 만든 것이다(`created_at 2026-09-03`).

### 처리

1. **부류를 닫았다** — `harness.py` 의 `SNAPSHOT_TABLES` · 삭제문 · 잔여 검증 세 곳에
   `p1_return_daily_signal_batch` 를 넣었다. projection 은 batch 를 참조하므로 자식을 먼저 지운다.
   잔여 검증에 `dailyBatches=` 를 더해 다시 새면 e2e 가 스스로 FAIL 한다.
   앞서 고친 `p1_return_model_seed_signal` orphan 과 같은 부류다 — FK 를 끄고 정리하면서 목록에
   없는 표만 남는다.
2. **인스턴스를 지웠다** — 미래 세션 배치 4건과 projection 248행. 하네스와 같은
   `session_replication_role=replica` 경로를 썼다. 두 표는 append-only 트리거로 보호되는데
   그 가드는 **실제 실행의 감사 기록**을 지키기 위한 것이고, 지운 것은 오늘 테스트가 만든
   잔여다. 오늘(2026-09-03) 배치 1건은 그대로 두었다.
3. 결과: 남은 배치 1건, 고아 projection 0, 2026-09-04 가 참조할 소스는 `2026-09-02 DAILY`.

## 4. 회귀 테스트

`tests/data/market_data/test_yfinance_daily_cli.py` 12건. DB 도 네트워크도 쓰지 않는다.

- 세션 선정이 **오늘을 포함하지 않는다** — 장중 일봉은 확정되지 않는다
- head 가 최신이거나 미래면 빈 목록, limit 준수, 오름차순, head 초과
- 계약을 만족하지 않는 봉 5종을 **고치지 않고 버린다**
- 빈 provider 응답을 `FETCH_EMPTY` 로 닫는다
- 종목이 커밋된 exact-31 이고 티커 규칙이 카탈로그 문구 그대로다
- `MAX_SESSIONS` 범위 밖 값을 거부한다

적재 자체는 기존 `repository.stage_daily_shard` 가 하고 그쪽에 이미 테스트가 있다.

## 5. 이 문서가 갱신하는 이전 판단

`20260903-p1-daily-operation-verification.md` 의 "남은 결정 — 시장데이터가 매일 갱신되지 않는다"
는 선택지 셋을 적고 정책 결정으로 미뤄 두었다. 그중 **첫째(일일 수집 자동화)를 채택했다.**

`daily_ready` 에 하한을 추가하는 둘째는 채택하지 않았다. 수집이 매일 돌면 head 가 실제로
전진하므로 하한이 막을 것이 남지 않고, 하한은 수집이 하루 실패했을 때 청산·대사 경로까지
같이 닫는다 — 과제한이 된다. 최신성 노출(셋째)은 `CAPSTONE_MARKET_DATA_DAILY` 마커로 부분
반영했다.
