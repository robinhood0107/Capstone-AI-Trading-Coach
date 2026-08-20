# S5.7A model-neutral Market Data contract lock

## KR

LightGBM production publication을 연구 전용으로 닫은 뒤에도 검증된 KIS/KRX/ECOS 수집 경계를 S6
금융공학 계산에 재사용할 수 있도록 모델 중립 data-only 계약을 잠근다.

- seed는 기존 7,218 source chunk를 provider 0회로 검증하되 raw copy, hardlink, source path 영속화와
  feature/label/final-test/release/batch read를 금지한다.
- daily shard는 한 XKRX session, 월중 고정 exact-31, KOSPI/KOSDAQ, ECOS 최대 2 series만 담는다.
- health는 휴장·evidence clock·divergence·gap·partial·needs-human·not-estimable을 fail-closed로 표현한다.
- 운영 reader는 253 close, 연구 reader는 1,260 XKRX session으로 제한하고 provider-on-read는 0이다.
- DB/exporter/reader/runtime/public API/Dashboard/scheduler/provider authority는 이 PR에서 구현하지 않는다.
- LightGBM Signal은 계속 `ABSTAIN`이고 strict-PIT 성과 주장은 `NO_GO`다.

## EN

After closing LightGBM production publication as research-only, this change locks a model-neutral,
data-only contract so the validated KIS/KRX/ECOS boundaries can later support S6 financial calculations.

- The seed verifies the preserved 7,218 source chunks with zero provider calls and forbids raw copying,
  hardlinks, persisted source paths, and feature/label/final-test/release/batch reads.
- A daily shard contains one XKRX session, the monthly-fixed exact-31 membership, KOSPI/KOSDAQ, and at
  most two ECOS series.
- Health states fail closed for holidays, evidence clock waits, divergence, gaps, partial runs,
  human intervention, and non-estimable output.
- The operational reader is bounded to 253 closes and the research reader to 1,260 XKRX sessions;
  provider fan-out on reads is zero.
- This PR implements no database, exporter, reader runtime, public API, dashboard, scheduler, or provider authority.
- LightGBM Signal remains `ABSTAIN`; strict-PIT performance claims remain `NO_GO`.
