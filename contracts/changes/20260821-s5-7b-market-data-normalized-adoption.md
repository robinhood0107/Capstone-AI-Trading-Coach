# S5.7B Market Data normalized adoption

## KR

- S5.7A의 `market-data-seed.v1`, daily-shard, health schema/catalog bytes는 변경하지 않는다.
- 봉인 source manifest SHA
  `6e10ef1201996ed6d59a2bce4db8c2b5e500ff49fd709e798b026cbe8fa45c2c`와 7,218 chunk만 읽어
  provider 호출 0으로 normalized seed를 만들었다.
- 결과는 bars 267,788, indices 2,144, macro 3,042, universes 1,581행이다. archive SHA는
  `f15dc57ee6395c2877d43f36378c628abb71fc438e579b2e8351335ac80a2439`, manifest SHA는
  `e3f26485c93d5e8bd9cdbd7f9ea7cc46cf3f446cf42e9d65b28f1f5b89bd9a5c`다.
- V75는 기존 quote table과 분리된 append-only normalized tables, operational 253/research 1,260
  security-barrier view와 별도 retention capability를 추가한다. Spring app에는 history 권한이 없다.
- stage는 운영자가 지정한 exact manifest SHA를 필수로 검증한다. correction view는 같은 identity/session의
  최신 generation을 먼저 선택해 correction이 253/1,260 session 상한을 이중 소비하지 않게 한다.
- 역사 union의 실제 KRX 단축코드 `0126Z0`은 연구 archive에 원문 보존한다. 현재 daily exact-31은 숫자
  31개이므로 S5.7A daily schema는 변경하지 않는다.
- 전체 품질은 `RECONSTRUCTED_FIXED_LAG`; strict-PIT 성과, public API, scheduler, Signal/Risk/order는 NO_GO다.

## EN

- The S5.7A seed, daily-shard, health schemas, and catalog remain byte-stable.
- A source-only exporter verified the sealed 7,218 chunks and produced the normalized seed with zero
  provider calls and an unchanged historical INTENT count of 7,230.
- V75 adds separate append-only normalized storage, bounded operational/research views, and a distinct
  dry-run-first retention capability. The Spring application receives no history-reader authority.
- Stage requires an explicit expected manifest SHA. Bounded views de-duplicate corrections by the latest
  generation before counting distinct history sessions.
- One alphanumeric KRX short code is preserved in historical research rows; the current numeric exact-31
  daily contract is unchanged.
- This change grants no strict-PIT performance, public API, scheduler, Signal, RiskDecision, or order authority.
