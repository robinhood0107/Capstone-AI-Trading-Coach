# P1 일일 시장데이터 수집 자동화 — 기본 5개를 유지한 채 (2026-09-03)

`20260903-p1-market-data-daily-freshness.md`가 "남는 운영 요건"으로 남겨 둔 것을 없앤다.
스택을 여러 날 켜 둘 때 사람이 하루 한 번 갱신을 불러야 했다.

기존 `contracts/changes/` 파일은 수정하지 않고 신규 파일만 추가한다.

## 1. 탈락한 셋과 그 이유

| 선택지 | 왜 기각했나 |
|---|---|
| 앱 컨테이너에 writer DSN 주기 | 코드는 가장 적다(`serve()`에 한 줄). 그러나 **거래하는 앱이 가격 원장을 위조**할 수 있게 된다. V76 체인 트리거·writer role 단정·receipt 해시·`temporalQuality`가 존재하는 이유가 그 분리다. 원장의 증거 가치가 사라진다 |
| 호스트 cron / systemd timer | 리포 밖 OS 상태에 의존한다. "compose 하나로 리눅스든 WSL이든 어디서나 동작"이라는 전제를 깬다 |
| Python 스케줄러 추가 | 타임존 계산과 루프 코드가 늘어난다. 셸 한 줄로 되는 일이다 |

## 2. 계약을 깨지 않는 자리

`20260826-p1-compose-functional-modules.md`가 **"기본 장기 컨테이너는 5개, 모델 profile은
7개로 고정한다"**고 적고 다섯을 이름으로 열거한다. `20260827-p1-compose-supply-handoff.md`는
**"기본 5개, `--models` 7개, provider-free 기본값"**이라고 적는다.

고정된 것은 **기본 구성**과 **`--models` 변형**이다. `--mock`은 기본이 아닌 선택 모드이고
(같은 기록이 provider-free를 기본값으로 명시한다) 최신성이 실제로 문제가 되는 것도 자동매매가
도는 그 모드뿐이다.

그래서 수집기를 `automation` profile 뒤에 두고 `--mock`에서만 띄운다. `--models`가 이미 쓰는
것과 같은 형태이므로 새 개념이 없다.

```text
./capstone up            5개   (계약 문장이 문자 그대로 유지된다)
./capstone up --mock     6개   + market-data-daily
./capstone up --models   7개   + bge-m3, paddleocr-vl
./capstone up --mock --models   8개
```

**실측** — plain `up`은 `CAPSTONE_PERSISTENT_CONTAINERS=5`이고 수집기가 제거되며,
`up --mock`은 `=6`이고 수집기가 상주한다. 둘 다 `CAPSTONE_UP=PASS`.

## 3. 무엇을 새로 만들지 않았나

| 필요 | 재사용한 것 |
|---|---|
| 이미지 | `capstone-decision-platform:p1-local` — `market-data-cli`와 동일 |
| secret | `market_data_env` — **신규 0개.** `p1ctl`의 exact inventory를 건드리지 않는다 |
| 네트워크 | `p1-data`(postgres) + `p1-app`(외부 HTTPS) — `market-data-cli`와 동일 |
| 명령 | 기존 `yfinance_daily_cli`. **Python 변경 0줄** |
| 스케줄링 | 셸 루프 한 줄. `/usr/bin/sh`(busybox)와 `/usr/bin/sleep`이 이미지에 있음을 확인했다 |
| 보안 | `app-security` 앵커 그대로 — `read_only`, `cap_drop: ALL`, `no-new-privileges`, `pids_limit`, `restart: on-failure:3` |

## 4. 최소 권한 — entrypoint 프로파일을 새로 두었다

`market-data` 프로파일을 재사용하려 했더니 `invalid_secret_file`로 닫혔다. 그 프로파일은
secret 파일 **세 개**를 요구한다 — `market_data_env`, `market_data_provider_env`,
`automation_runtime_env`.

즉 재사용하면 상주 수집기가 **KIS 앱키와 자동운용 DSN까지** 갖는다. 하루 한 번 공개 일봉을
받아 적는 일에 주문 권한과 상태기계 권한을 함께 주는 셈이다.

그래서 `market-data-daily` 항목을 새로 두었다. 요구하고 허용하는 키는
`MARKET_DATA_WRITER_DSN` 하나뿐이다. whitelist를 느슨하게 하지 않고 항목을 더하는 방식은
`calendar-offline-seed` 때와 같다.

**실측** — 컨테이너 안의 `/run/secrets/`에 `market_data_env` 하나만 있다.

## 5. 왜 한 시간 간격인가

최신이면 `providerCalls=0`이다(실측). 그래서 한 시간 간격의 비용은 DB 질의 하나뿐이다.
타임존 계산이 필요 없고, 머신이 절전에서 돌아와도 다음 시간에 스스로 따라잡는다.

**장중에 미확정 일봉을 적재할 수 없다.** `_pending_sessions`가 오늘을 제외하므로 완료된
거래일만 대상이 된다 — 구성상 안전하며 간격과 무관하다.

실패해도 루프는 계속 돈다(`|| true`). 하루 못 채운 것이 루프를 멈출 이유는 아니고, 다음 시간에
다시 시도한다.

**실측** — 컨테이너 안 프로세스가 `sh -c while :; ...` + `sleep 3600`이고, `sleep`을 깨우자
다음 반복이 실행됐다(로그의 마커가 1건 → 2건). `sleep` 종료가 루프를 죽이지 않는다.

## 6. 회귀

`contracts/tests/test_p1_compose_supply_handoff.py` 3건. DB도 컨테이너도 쓰지 않는 정적 검사다.

- 기본 5개 + `--models` 2개 + `--mock` 1개의 가산식과, `--mock`에서만 띄우고 아니면 내리는 분기
- 수집기가 `automation` profile 뒤에 있고, 기존 이미지를 쓰고, secret이 정확히 하나이며
  `market_data_provider_env`·`automation_runtime_env`가 **붙지 않았는지**, 네트워크 둘,
  보안 앵커 상속, 기존 CLI 사용
- entrypoint whitelist가 이 프로파일에 writer DSN 하나만 허용하고 자동운용 DSN·KIS 앱키·
  runtime shared secret을 **허용하지 않는지**

## 7. 이 문서가 갱신하는 이전 판단

`20260903-p1-market-data-daily-freshness.md` §"배선"의 마지막 단락은 이렇게 적었다 — "스택을
여러 날 켜 둔 채로는 호스트가 하루 한 번 이 명령을 불러야 한다. 상주 컨테이너를 하나 늘리면
자동화되지만 `CAPSTONE_PERSISTENT_CONTAINERS=5` 계약이 깨지므로 그 결정은 하지 않았다."

그 판단의 전제가 틀렸다. 계약이 고정한 것은 **기본 구성**이고, `--mock`은 기본이 아니다.
profile 뒤에 두면 계약 문장이 문자 그대로 유지되면서 자동화된다. 운영 요건은 사라진다 —
`./capstone up --mock` 한 번이면 그 뒤로 사람이 손댈 일이 없다.

`./capstone market-data refresh`는 그대로 둔다. 즉시 확인하고 싶을 때 쓰는 운영자 명령이고
멱등이다.
