# S5.1 PIT universe schedule and feature bundle hardening

## 상태

```text
S5_0_HISTORICAL_OUTPUTS=BYTE_STABLE
S5_1_MONTHLY_SCHEDULE=CALENDAR_DERIVED
S5_1_FEATURE_BUNDLE=MANIFEST_FIRST
S5_1_MANIFEST_TRUST_ANCHOR=REQUIRED
S5_CROSS_MARKET_READER_CALLS=0
PUBLIC_SIGNAL_OPENAPI_DB_CHANGE=0
```

이 변경은 S5.0 Signal v1/v2 schema, generated fixture, OpenAPI, V72와 public Signal API를
변경하지 않는다. S5.1의 caller 조합식 날짜와 manifest 없는 Parquet read를 fail-closed 내부
경계로 교체하고, 저장된 cross-market 상태가 S5 model 또는 Signal hash에 영향을 주지 않는지
실제 LightGBM mini-fit으로 회귀 검증한다.

## 월별 PIT universe schedule

`effectiveMonth`는 strict `YYYY-MM`이다. schedule은 pinned XKRX calendar에서 다음 값을 직접
파생한다.

1. 적용 월의 첫 XKRX 정규 session
2. 첫 session의 `08:10:00 Asia/Seoul` evidence cutoff
3. 첫 session 바로 전 XKRX 정규 session인 selection session
4. selection session을 포함한 직전 20 XKRX 정규 sessions

dataset cutoff는 timezone-aware여야 하며 KST로 정규화한다. schedule evidence cutoff가 dataset
cutoff보다 미래면 거부한다. selector는 위 schedule 객체만 받고 independent selection date,
trailing sessions, effective month 또는 cutoff를 받지 않는다. evidence는
`availableAt <= evidenceCutoff`인 latest vintage만 사용한다.

기존 prior-month top 30, permanent instrument identity join, 월중 replacement 0, 고정 ETF
`132030`, ETN 제외와 horizon union 180 제한은 유지한다.

## Feature bundle v1

승인 root의 파일명은 서버가 정의한 `manifest.json`과 `features.parquet`로 고정한다. manifest가
경로나 artifact ID를 선택할 수 없다. reader는 caller가 별도 승인 경계에서 얻은
`expectedManifestSha256`을 필수로 받고, 누락되거나 manifest content digest와 다르면 Parquet을
읽지 않는다.

manifest exact root field는 다음과 같다.

```text
manifestVersion
schemaVersion
parquetFile
parquetSha256
logicalDatasetHash
rowCount
columnCount
featureColumns
provenance
```

고정값은 다음과 같다.

```text
manifestVersion=s5-feature-bundle-v1
schemaVersion=s5-feature-table-v1
parquetFile=features.parquet
featureColumns=CORE_FEATURE_COLUMNS exact order
```

`provenance` exact field는 다음과 같다.

```text
producer=decision-platform
sourceWorkspace=decision-platform
datasetCutoff
exchangeMic=XKRX
calendarName=XKRX
calendarVersion=4.13.2
universePolicyVersion=s5-pit-universe-v1
featurePolicyVersion=s5-core-features-v1
rawSessionStart/rawSessionEnd/rawSessionCount
eligibleSessionStart/eligibleSessionEnd/eligibleSessionCount
universeScheduleSha256
pitInputSha256
optionalFeatureGroups=[]
```

`universeScheduleSha256`은 정렬된 월별 calendar-derived schedule의 canonical receipt를 묶는다.
`pitInputSha256`은 선택된 universe, 가격, 시장과 거시 PIT input value 및 provenance의 canonical
receipt만 묶는다. cross-market, analyst, news, cause, RAG, LLM과 HMM 저장 상태는 두 digest에
포함하지 않는다. optional group은 v1에서 비어 있어야 하며 도입 시 versioned contract 변경이
필요하다.

reader 순서는 bounded no-follow manifest read, external digest, duplicate/nonfinite/unknown/version/
canonical JSON 검증, 고정 Parquet no-follow read, physical/metadata/decoded/schema 검증, row/column/
logical hash 대조다. source row 0은 `DATASET_UNAVAILABLE`이다. 전체 검증이 끝난 뒤에만 immutable
bundle receipt를 반환한다. manifest를 우회하는 public direct Parquet reader는 두지 않는다.

## Cross-market 격리 회귀

서로 다른 저장 snapshot을 가진 strict cross-market reader 두 개를 동일 PIT pipeline에 주입한다.
두 reader의 call count는 모두 0이어야 한다. 실제 LightGBM의 고정 첫 candidate `(15,NONE)` 하나를
각 실행에 사용하고 다음 값이 동일해야 한다.

```text
feature table
logical dataset hash
logical training dataset hash
feature manifest bytes/hash
LightGBM text model/hash
OVR Platt canonical bytes/hash
Signal semantic hash
```

threads 4 drift 시 기존 project lock대로 threads 1을 재검증하며 threads 1도 다르면 FAIL한다. 이
회귀는 exact four-grid 후보 수나 tuning policy를 변경하지 않는다.

## 승인 범위

이 hardening은 현재 S5 fixture-first merge candidate의 내부 Python 경계와 회귀 테스트만 변경한다.
실제 데이터 취득, 실제 model `AVAILABLE`, production pointer 활성화, provider/account/order 호출,
RiskDecision/order wiring과 S6.6 join은 계속 승인되지 않는다.
