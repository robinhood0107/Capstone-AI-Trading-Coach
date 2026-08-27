# P1 Data-only daily collector 운영 가이드

## 현재 판정

```text
OWNER_DAILY_COLLECTOR_FIXTURE=IMPLEMENTED_MERGE_CANDIDATE
COLLECTION_SCHEDULE=16:10_ASIA_SEOUL_DEFAULT_OFF
DAILY_SHARD_EVIDENCE_CLOCK=NEXT_XKRX_SESSION_08:10_ASIA_SEOUL
FIXTURE_PROVIDER_PHYSICAL_CALLS=0
BUY_CANDIDATE_ALLOWED=FALSE
LIVE_PROVIDER_ADAPTER=NOT_IMPLEMENTED
```

이 runtime은 수집과 accepted shard 게시를 두 단계로 나눈다. XKRX session 당일 16:10 이후 fixture
record를 staging하고, 기존 S5.7C가 다음 XKRX session 08:10 evidence clock 이후 동일 record set을 다시
검증해 `market-data-daily-shard.v1`을 게시한다. 따라서 16:10 요구를 추가하면서 기존 달력·asOf 계약을
바꾸지 않는다.

## 단계와 불변식

1. 기본 `enabled=false`, 휴장일, 16:10 전, session이 지난 late start는 transport 생성 전 종료한다.
2. plan은 packet SHA, exact-31 membership, exact operation 순서, 16:10 schedule, provider cap을 canonical
   SHA-256으로 결속한다.
3. normal session은 KRX 5 + KIS daily 31 + ECOS 2 = exact 38이고 월 경계는 KRX monthly 3개를 더한
   exact 41이다.
4. 성공 operation은 owner-private regular file과 `collection-journal.jsonl`에 즉시 fsync한다.
5. 첫 결손에서 중단하며 같은 plan 재개는 기존 성공 record를 local read로 재사용한다.
6. source receipt set이 packet binding과 일치한 경우에만 `complete-manifest.json`을 마지막에 게시한다.
7. promotion은 complete manifest와 self-hash를 검증한 뒤 기존 S5.7C manifest-last/DB transaction 경계를
   사용한다.

`STAGED_COMPLETE`는 fixture record set 준비 완료일 뿐 실제 provider health 또는 주문 가능 상태가
아니다. `buyCandidateAllowed`는 모든 결과에서 false다. 이후 automation closed loop가 fresh accepted
daily shard를 별도 gate로 확인하기 전에는 신규 BUY를 열 수 없다.

## Fail-close 상태

- `DISABLED`: 기본 OFF
- `NOT_DUE`: 16:10 이전
- `NO_NEW_SESSION`: XKRX 휴장일
- `SKIPPED_LATE_START`: session 당일을 지나 새 collection 시작 금지
- `EVIDENCE_GAP`: provider fixture 결손 또는 exact symbol/session 불일치
- `NEEDS_HUMAN`: packet receipt-set binding 불일치
- `HALTED`: 같은 session의 다른 plan 또는 기존 sealed bytes 손상
- `NO_OP`: 같은 session/same plan의 complete manifest 재실행

## 검증

```bash
cd workspaces/decision-platform/python-services
.venv/bin/ruff check app/p1_owner/data_only_collector.py tests/p1_owner/test_data_only_collector.py
.venv/bin/mypy app/p1_owner/data_only_collector.py tests/p1_owner/test_data_only_collector.py
.venv/bin/pytest -q tests/p1_owner/test_data_only_collector.py tests/data/market_data
```

fixture 검증은 network deny 경계에서 실행한다. KRX, KIS token/daily, ECOS, GDELT physical call은
모두 0이다. 계좌·잔고·주문도 호출하지 않으며 live adapter와 자격증명 처리를 포함하지 않는다.
Public endpoint, Signal, RiskDecision, order 연결도 없다.
