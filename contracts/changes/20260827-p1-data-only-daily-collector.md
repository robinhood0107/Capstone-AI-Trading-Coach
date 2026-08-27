# P1 data-only daily collector fixture runtime

## KR

Owner Phase A의 data-only collector를 기존 S5.7A/B/C 계약 위에 additive runtime으로 구현한다.

- collection schedule은 Asia/Seoul 16:10이며 설정 기본값은 OFF다.
- 16:10 collection은 deterministic fixture record만 owner-private staging에 기록한다.
- accepted daily shard는 기존 pinned next-XKRX-session 08:10 evidence clock과 S5.7C runtime을 그대로
  사용한다. 기존 `market-data-daily-shard.v1`, `market-data-health.v1` bytes는 변경하지 않는다.
- normal plan은 KRX daily 5 + KIS daily exact-31 + ECOS 2의 기존 exact-38이고 월 경계는 기존
  exact-41이다. KIS token 최대 1, retry 0, GDELT 0 경계도 plan hash에 결속한다.
- 성공 record는 immutable file과 append-only journal로 즉시 봉인한다. 첫 결손 뒤 후속 operation을
  실행하지 않고, 재개 시 성공 record를 다시 호출하지 않는다.
- receipt-set까지 packet과 일치한 뒤에만 complete manifest를 마지막으로 게시한다. same session의
  same plan은 no-op이고 다른 plan은 `HALTED`, receipt mismatch는 `NEEDS_HUMAN`이다.
- exact-31 중 하나라도 빠지거나 symbol/session binding이 다르면 complete manifest와 daily shard를
  게시하지 않는다. 모든 terminal result는 `buyCandidateAllowed=false`다.

이 runtime은 live provider adapter나 credential을 포함하지 않는다. Owner fixture physical count는 KRX,
KIS token/daily, ECOS, GDELT, account, balance, order 모두 0이다. Public API, Dashboard, Signal,
RiskDecision, order authority도 추가하지 않는다.

## EN

This change adds the Owner Phase A data-only collector as an additive runtime over the preserved S5.7A/B/C
contracts.

- The collection schedule is 16:10 Asia/Seoul and is disabled by default.
- The 16:10 collection phase stages deterministic fixture records only in owner-private storage.
- Accepted daily-shard promotion continues to use the pinned next-XKRX-session 08:10 evidence clock and the
  existing S5.7C runtime. Existing `market-data-daily-shard.v1` and `market-data-health.v1` bytes are unchanged.
- The normal plan preserves exact 38 operations: five KRX daily records, exact-31 KIS daily records, and two
  ECOS records. Month-boundary execution preserves exact 41. The hashed plan also binds KIS token max one,
  retry zero, and GDELT zero.
- Each success is sealed immediately in an immutable file and append-only journal. The first evidence gap
  stops later operations, and resume never calls already successful operations again.
- The complete manifest is published last only after the receipt set matches the packet. The same plan for the
  same session is a no-op; a different plan is `HALTED`; a receipt mismatch is `NEEDS_HUMAN`.
- A missing exact-31 symbol or mismatched symbol/session publishes neither a complete manifest nor a daily
  shard. Every terminal result keeps `buyCandidateAllowed=false`.

This runtime contains no live provider adapter or credential. Owner fixture physical counts for KRX, KIS
token/daily, ECOS, GDELT, account, balance, and order are all zero. It adds no public API, Dashboard, Signal,
RiskDecision, or order authority.
