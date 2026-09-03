# P1 rejection-point audit: what was relaxed, what was kept, and why

## KR

정상 경로를 막는 것은 거부 지점(reject/skip/raise)이다. 그래서 함수를 전부 읽는 대신 거부
지점을 세고 각각을 **유지 / 완화 / 제거** 셋 중 하나로만 판정했다. 새 추상이 필요한 발견은
기록만 하고 이번에 손대지 않는다 - 그것을 억지로 만드는 것이 진짜 과설계다.

### 완화한 것

**`daily_ready` 가 `manifest_kind='DAILY'` 만 인정했다** (V93:194-199 -> V117)

DB 에는 `AUTOMATION_BOOTSTRAP` 1건뿐이고 `DAILY` 는 0건이라 `dailyShardFreshComplete` 가
영원히 false 였다. `automation.py:1162` 가 `SKIPPED_DATA_UNAVAILABLE` 로 전이하므로
**arm 이 성공해도 주문이 나가지 않는다.** 그런데 status API 의 `blockers` 는 빈 배열이고
`canArm` 도 true 여서 운영자가 원인을 알 방법이 없었다.

`manifest_kind IN ('DAILY','AUTOMATION_BOOTSTRAP')` 로 넓혔다. 남긴 것 -
`status='ACCEPTED'`, `as_of <= session_date + 09:20 KST`, 실행 role 게이트. 진위·수용
여부·최신성은 여전히 검사하고 manifest 종류 이름만 넓혔다. 같은 표의 같은 스키마이고
`market_data_manifest_kind_check` 가 세 종류를 모두 허용한다.

**비밀 스캔이 PR 마다 0개 커밋을 검사한 뒤 통과했다** (`p1-full-app-security.yml`)

`actions/checkout` 의 `fetch-depth` 기본값이 1 이라 PR 에서 base SHA 가 없고
`base..head` 범위가 풀리지 않는다. gitleaks 8.30.1 은 그 상황에서 ERR 만 찍고 **exit 0**
으로 끝난다(직접 확인: 존재하지 않는 범위 exit=0). `--exit-code 1` 은 "유출을 찾았을 때"만
적용되므로 범위가 없는 것과 유출이 없는 것이 구분되지 않았다.

`fetch-depth: 0` 을 주고, 스캔 전에 범위를 검증해 풀리지 않으면
`SECRET_SCAN_RANGE_UNRESOLVABLE` 로 명시적으로 실패시킨다. 검사한 커밋 수도
`SECRET_SCAN_COMMITS` 로 남긴다. 이력 범위를 읽는 다른 워크플로는 이미 `fetch-depth: 0`
을 갖고 있었고 이 파일만 빠져 있었다.

**연도 하나가 달력 밖이라는 이유로 전체 seeding 을 버렸다** (`offline_seed_cli`)

pinned XKRX 달력은 오늘부터 약 1년 뒤에서 끝난다(실측 상한 2027-09-03). 그래서 "올해와
내년" 의 마지막 연도가 `DateOutOfBounds` 로 실패하는 것이 정상이다. 처음 판은 그 한 해
때문에 쓸 수 있는 80 세션까지 버렸다. 연도별로 관용하고 건너뛴 연도를 마커에 남긴다.
모든 연도가 달력 밖이면 그건 입력 오류이므로 조용한 성공으로 두지 않는다.

### 제거한 것

**낡은 기본 이미지 이름** (`p1ctl`, `compose.db.yml`, `compose.kafka.yml`, `demo.env.example`)

이 레포는 spring 과 python 서비스를 하나의 이미지로 빌드한다 - `full-appctl` 이
`decision-platform.Dockerfile` 하나만 빌드한다. 실제로 존재하는 이름은
`capstone-decision-platform:p1-local` 뿐이고 `capstone-spring-api` /
`capstone-python-services` 는 빌드되지 않는다(`docker images` 로 확인).

`full-appctl` 과 `compose.yml` 은 이미 옳은 이름을 기본값으로 쓰는데 나머지 파일의 기본값만
옛 이름에 남아 있었다. 평소에는 `full-appctl` 이 값을 export 하고 넘기므로 드러나지 않지만,
`p1ctl` 을 직접 부르면 `init_state` 가 `spring_image_missing` 으로 죽는다 - 이름이 낡았을
뿐인데 "이미지가 없다"로 보인다. `verify_runtime_env` 의 허용 집합은 옛 이름도 계속 받는다
(기존 상태 디렉터리 호환). 기본값만 맞췄다.

### 고친 결함 (거부 지점이 아니라 누락)

**e2e harness 가 orphan 을 만들었다** (`tests/e2e/harness.py`)

`p1_return_model_seed_signal.bundle_sha256 -> p1_return_artifact_bundle` 은
`ON DELETE RESTRICT` 다. 즉 FK 가 켜져 있으면 bundle 삭제가 거부된다. 그런데 정리 단계는
`session_replication_role=replica` 로 FK 를 끄고 지우므로 삭제가 성공하고 signal 행만 남는다.
이 표가 삭제 목록에도 잔여 검증 목록에도 없어서 그 누출이 보이지도 않았다.
**실측: DB 에 orphan 62행.**

bundle 보다 먼저 지우고, 잔여 검증에 `seedSignals` 를 넣었다. 다시 새면 이번엔 FAIL 로 보인다.

### 유지한 것 - 판정 근거를 남긴다

**`importer._annualized_sharpe` 를 `financial_engineering.sharpe_ratio` 로 대체하지 않는다**

같은 계산이 두 벌이라고 판단했으나 실측해 보니 **중복이 아니라 다른 계약이었다.**

| 입력 | `importer` | `financial_engineering` |
|---|---|---|
| 정상 (n=2~1000) | 동일 | 동일 (최대 절대차 `0.000e+00`) |
| 빈 목록 | `0.0` | `ValueError: input_empty` |
| 1개 | `0.0` | `ValueError: input_too_short` |
| 분산 0 (상수 열) | `0.0` | `ValueError: denominator_zero` |
| `253.0` (float) | 받는다 | `ValueError: periods_per_year_invalid` |

수식은 비트 단위로 같다. 다른 것은 퇴화 입력에서의 태도다. 대체하면 무거래·무변동 종목
하나가 31종목 import 전체를 예외로 죽인다 - `calculate_calmar` 의 `np.inf` 가
`_reject_non_finite` 로 실행을 통째로 죽였던 것과 **정확히 같은 실패 방식**이다. 그리고
`sessions_per_year=253.0` 대안 Sharpe 경로가 아예 깨진다. 그래서 유지한다.

### 기록만 하고 손대지 않는 것

**같은 달 재스테이징 불가** - `V75:222-224` 의 `UNIQUE(membership_month, rank, generation)`
과 `automation_bootstrap.py:487,497` 의 `generation=1` 하드코딩. `on conflict` 도 재실행
가드도 없으므로 같은 달을 다시 스테이징하면 unique 위반이다. 그런데 올바른 해법은
"중복 허용"이 아니라 supersession 체인이다 - 스키마의
`market_data_generation_supersedes_check` 가 `generation > 1` 이면
`supersedes_sha256 IS NOT NULL` 을 요구하고, 현재 insert 는 그 값을 null 로 둔다.
generation 계산 + 이전 manifest 지정 + 체인 검증은 새 기구다. 지금 어떤 경로도 막고 있지
않으므로(bootstrap 은 이미 1건 ACCEPTED) 기록만 한다.

**`market-data-cli` 에 `volumes:` 가 없다** - `compose.yml` 의 이 서비스로는 offline
replay/stage 경로를 컨테이너에서 실행할 수 없다. 그런데 offline replay 로 진짜 `DAILY`
manifest 를 만드는 길은 이미 채택하지 않기로 했다 - sealed replay record 38개의 값을
운영자가 손으로 authoring 하게 되어 provider 호출은 0이지만 데이터 진위가 사람 손에
들어간다. 그 경로를 쓰지 않으므로 volume 도 필요하지 않다. 기록만 한다.

**`windowSize` 가 세 곳에 있다** - `model_shape.WINDOW_SIZE`(런타임 상수),
`p1-return-config.v2` 의 `{"const": 20}`(계약), `assets`/generator 의 fixture 값.
계약 상수와 런타임 상수를 한쪽에서 파생시키려면 workspace/contract 경계를 넘는 생성
단계가 필요하다. 새 기구이므로 기록만 한다.

**스키마 최대 버전 상수 14곳** - `116` 이 Kotlin 테스트 13곳과 `public_rag_seed` 의
forward-compatible 목록 1곳에 상수로 흩어져 있어 migration 하나를 더할 때마다 전부
손대야 한다. 값만 정확히 올렸고 파생 상수로 묶지 않았다. 다만 `public_rag_seed` 의 목록은
안티패턴이 아니라 설계다 - "sealed public Seed 는 V87 에 byte-bound 이고 이후 additive
migration 은 호환성을 **명시적으로 선언**한다". 그 선언이 곧 그 항목이다.

## EN

What blocks a working path is a rejection point (reject/skip/raise). So instead of reading
every function, the audit counted rejection points and gave each exactly one verdict:
**keep / relax / remove**. Findings that would need a new abstraction are recorded and left
alone - building one on speculation is the real over-engineering.

### Relaxed

**`daily_ready` accepted only `manifest_kind='DAILY'`** (V93:194-199 -> V117). The database
holds exactly one `AUTOMATION_BOOTSTRAP` manifest and zero `DAILY`, so
`dailyShardFreshComplete` was permanently false and `automation.py:1162` transitioned to
`SKIPPED_DATA_UNAVAILABLE`: **arming succeeded but no order could ever be placed.** The
status API showed an empty `blockers` array and `canArm: true`, so the cause was invisible.
The predicate now accepts both kinds; `status='ACCEPTED'`, the
`as_of <= session_date + 09:20 KST` freshness bound, and the executing-role gate are
untouched. Provenance, acceptance and freshness are still checked - only the kind name widened.

**The PR secret scan passed after scanning zero commits.** `actions/checkout` defaults to
`fetch-depth: 1`, so on a pull_request event the base SHA is absent and `base..head` does
not resolve. gitleaks 8.30.1 then logs an error and exits **0** (measured directly:
nonexistent range gives exit=0), because `--exit-code 1` applies only to *found* leaks. An
unresolvable range was indistinguishable from a clean scan. The checkout now uses
`fetch-depth: 0`, and the range is verified before scanning so an unresolvable one fails
explicitly as `SECRET_SCAN_RANGE_UNRESOLVABLE`; the scanned commit count is logged as
`SECRET_SCAN_COMMITS`. Every other workflow that reads a history range already had
`fetch-depth: 0` - only this one was missing it.

**One out-of-calendar year discarded the whole seeding run.** The pinned XKRX calendar ends
roughly one year out (measured bound 2027-09-03), so the later of "this year and next"
failing with `DateOutOfBounds` is normal. The first version threw away all 80 usable
sessions because of it. Years are now tolerated individually and skipped ones are reported
in the marker; if every requested year is outside the calendar that is an input error and
does not pass silently.

### Removed

**Stale default image names** in `p1ctl`, `compose.db.yml`, `compose.kafka.yml` and
`demo.env.example`. This repository builds the Spring and Python services as a single image -
`full-appctl` builds only `decision-platform.Dockerfile` - so the only name that exists is
`capstone-decision-platform:p1-local`; `capstone-spring-api` and `capstone-python-services`
are never built (confirmed with `docker images`). `full-appctl` and `compose.yml` already
defaulted to the correct name; only the other files drifted. It stays hidden in normal use
because `full-appctl` exports the values, but invoking `p1ctl` directly makes `init_state`
die with `spring_image_missing` - a stale name presenting as a missing image. The
`verify_runtime_env` accepted set still admits the old names for existing state directories;
only the defaults changed.

### A defect, not a rejection point

**The e2e harness created orphans.** The foreign key from
`p1_return_model_seed_signal.bundle_sha256` to `p1_return_artifact_bundle` is
`ON DELETE RESTRICT`, so with foreign keys enforced the bundle delete would be refused. The
cleanup phase disables enforcement with `session_replication_role=replica`, so the delete
succeeds and the signal rows survive. The table appeared in neither the delete list nor the
residue check, so the leak was also invisible. **Measured: 62 orphan rows.** The table is now
deleted before the bundle and counted as `seedSignals` in the residue check.

### Kept, with the reasoning recorded

**`importer._annualized_sharpe` is not replaced by
`financial_engineering.sharpe_ratio`.** These looked like one calculation implemented twice;
measurement showed they are two different contracts. On normal input they agree to the bit
(maximum absolute difference `0.000e+00` for n=2 through 1000). On degenerate input the
importer returns `0.0` where the shared module raises - `input_empty`, `input_too_short`,
`denominator_zero` - and the importer's `253.0` alternative-Sharpe path is rejected outright
as `periods_per_year_invalid`. Substituting would turn a single symbol with no trades or no
variance into an exception that aborts the whole 31-symbol import: exactly the failure mode
of `calculate_calmar` returning `np.inf` and `_reject_non_finite` killing the entire run.

### Recorded, deliberately untouched

**Re-staging the same membership month is impossible** -
`UNIQUE(membership_month, rank, generation)` in V75:222-224 against the hardcoded
`generation=1` in `automation_bootstrap.py:487,497`, with no `on conflict` and no re-run
guard. The correct fix is not to permit duplicates but to build a supersession chain: the
schema's `market_data_generation_supersedes_check` requires `supersedes_sha256 IS NOT NULL`
whenever `generation > 1`, and the current insert leaves it null. Computing the generation,
naming the superseded manifest and validating the chain is new machinery, and nothing is
blocked today, so it is recorded only.

**`market-data-cli` declares no `volumes:`**, so the offline replay and stage paths cannot
run inside that container. The offline-replay route to a genuine `DAILY` manifest was
already rejected for a different reason - it would put the values of 38 sealed replay records
into an operator's hands, keeping provider calls at zero while moving data provenance to a
human. With that route unused, the volumes are not needed either.

**`windowSize` lives in three places** - the runtime constant `model_shape.WINDOW_SIZE`, the
`{"const": 20}` in `p1-return-config.v2`, and the fixture values in `assets` and the
generator. Deriving one from the other would need a generation step crossing the
workspace/contract boundary, so it is recorded only.

**The maximum schema version is a constant in fourteen places** - `116` across thirteen
Kotlin tests plus the `public_rag_seed` forward-compatible list. Each migration therefore
touches all of them. The values were raised precisely and not folded into a derived
constant. The `public_rag_seed` list is not an instance of the anti-pattern, though: it is
the design - the sealed public Seed is byte-bound to V87 and later additive migrations
**declare** compatibility explicitly, and that entry is the declaration.
