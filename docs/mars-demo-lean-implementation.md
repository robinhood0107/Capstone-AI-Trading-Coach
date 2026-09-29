# MARS 경량 DEMO 구현·운영 기록

## 기준과 조사 결과

| 기준 | 조사 결과 |
|---|---|
| 작업 기준 | `origin/develop` `a5919107a86ac01239216690492a9530c71cc9a7`에서 독립 worktree `feature/mars-demo-lean`을 시작했습니다. 원래 checkout의 사용자 파일은 건드리지 않았습니다. |
| main 기준 | 조사 시 `origin/main` `5d72e35f36d8a630b6c01a43ea9507ee817c1864`, `v1.0.11`이었습니다. |
| 이전 release 묶음 | 당시 `.github/workflows/mars-dockerhub-release.yml`은 DEMO/FULL의 api/web/postgres/redis 8개 이미지를 한 release에 묶었습니다. product image workflow와 `render_mars_release_compose.py`도 두 제품의 image inventory를 같이 요구했습니다. |
| 이전 DEMO | `compose.public-demo.yml`은 Spring API, PostgreSQL, Redis, actor/migration runtime, Python supervisor를 포함했습니다. dashboard는 `NEXT_PUBLIC_MARS_PRODUCT=demo` 분기, 단일 Agent 화면과 Full API demo route를 사용했습니다. 개발 mock transport는 production build의 대체물이 아니었습니다. |
| 화면 경계 | `workspaces/mars-demo`가 별도 Next app·API route·server adapter·fixture를 소유합니다. MARS FULL의 `AppShell`, 페이지, feature view와 공통 UI를 같은 소스에서 마운트하고, `@/shared/api/client`·`session` alias만 DEMO adapter로 연결합니다. FULL 앱은 DEMO adapter를 가져오지 않습니다. 로그인 카드의 action만 visitor session 발급으로 주입합니다. |
| 운영/API 경계 | 브라우저의 FULL API 계약 경로는 DEMO Next app 내부 adapter에서 fixture·세션별 SQLite overlay로 처리합니다. FULL API host, KIS 물리 API, FULL PostgreSQL/Redis volume은 연결하지 않습니다. KIS credential, Full account-login, Owner Vertex credential screens are not imported into the DEMO routes. |

현재 공유 UI를 바꾸면 FULL·DEMO 후보가 각각 만들어집니다. FULL-only 변경은 main merge 때 같은 SHA로 DEMO sync 평가를 실행하며 DEMO용 UI/contract 변화가 없으면 `NO_DEMO_DELTA`를 남기고 이전 DEMO digest를 유지합니다. DEMO-only 변경은 FULL 이미지·FULL semver·FULL Portainer release asset 작업을 생략합니다. 분류하지 못한 경로는 product build/publish 전에 실패합니다.

## 제품별 릴리스 계약

| 제품 | image set | 저장소·버전 | 운영 경계 |
|---|---|---|---|
| FULL | api, web, postgres, redis | 기존 `pjjpjj111/mars-full`; 이 변경의 FULL 후보 버전 `1.0.12` | 기존 release workflow와 Portainer 경로, 계정/KIS 계약, PostgreSQL/KEK volume 계약 유지 |
| DEMO | web 1개 | 기존 `pjjpjj111/mars-demo`; 독립 후보 버전 `2.0.0` | 별도 digest manifest, env/secret root, network, loopback port, `mars-demo_state` volume |

후보 manifest는 이미지 집합, source SHA, semver/tag, digest와 SBOM을 제품별로만 가집니다. FULL release workflow에는 DEMO job/artifact/dependency가 없습니다. DEMO merge 평가도 해당 PR의 immutable merge SHA를 checkout하고, 같은 SHA를 manifest에 씁니다. 재실행은 기존 GitHub release·asset·registry digest를 비교하며 다른 tag를 덮어쓰지 않습니다.

새 DEMO image tag가 검증·registry publish되더라도 NAS promotion이 되지는 않습니다. 제품 전환은 사람이 현재 stack을 stop한 뒤 한 제품만 active로 두고, target 제품의 digest·env·secret·stack을 적용합니다. 전환 후에는 운영 확인과 rollback window를 완료하고 모든 소비자 참조를 갱신한 다음 별도 승인을 받아 오래된 tag만 제거합니다. Docker Hub 저장소 `pjjpjj111/mars-demo` 자체는 유지합니다.

## fixture·수익 receipt

| 항목 | 값 |
|---|---|
| 가격 자료 | Yahoo Finance via yfinance 0.2.66; `auto_adjust=false`, `actions=true`, `repair=true` |
| 원본 수집 시각·범위 | 2026-09-21 11:26:13 KST 수집; 요청 구간 2000-01-01~2026-09-18, 시나리오 2026-08-18~2026-09-18 |
| 원본 SHA-256 | `b70b46b41012b640d8c81b9ade4f31d324e198c593441466ec20d1fa56625377` |
| fixture SHA-256 | `a2898a041b2f9b472272c95772be5ac5931b1632d585a3ce317ecf62d54b6f19` |
| 이용 범위 확인 | 사용자가 2026-09-29 세션에서 원자료/파생자료의 공개 표시와 이미지 포함 권한을 모두 확인했습니다. 공급자 계약 문서는 local receipt 묶음에 포함되지 않아 계약 조항을 독립적으로 주장하지 않습니다. 확인 범위는 fixture metadata에도 보존합니다. |
| 데이터 폭·한계 | 일별 OHLCV 816 bar, 34 series, 24 XKRX 세션, 고정 현재 31종목의 생존편향, 공급자의 수정된 과거 자료, 장중 주문 체결 순서는 증명 불가 |

사후 구성 포트폴리오는 평가 기간 종료 뒤 종가 수익 상위 2종목을 고른 **사후 구성 가상 사례**입니다. 10,000,000원 시작, 현금 유입·유출 0원, 마지막 순자산 10,460,650원(+461bp), 현금 6,746,650원, 실현손익 221,998원, 미실현손익 238,007원, 세후 배당 645원입니다. 이벤트 31개와 일별 receipt 24개를 같은 event log에서 재계산하고 final cash/equity/realized/dividend 합계를 validator가 대사합니다. 이 결과는 실운용 성과나 사전 예측력이 아닙니다.

독립 고정 규칙 예시는 직전 거래일까지의 SMA20/SMA50으로 다음 XKRX 거래일 시가에서 결정합니다. 24일 전체 중 활성 12일, 무행동 12일을 포함해 -12bp, 최대 낙폭 -98bp, 종료 순자산 9,987,756원입니다. 미래 정보를 신호에 쓰지 않습니다. 이 지표는 사후 구성 사례의 결과와 분리합니다.

계산은 편도 수수료 1.5bp, 편도 슬리피지 10bp, KOSPI 매도세 20bp, 배당 국세 원천징수 14% 가정을 사용합니다. 배당 재투자·개인별 최종/지방 배당세는 모델링하지 않습니다. 주식분할은 원본 action ratio로 보유 주식에 반영하고, 실제 분할 사건은 시나리오 기간에 없습니다. 일별 바만으로 장중 고가/저가 접촉 순서나 지정가 체결을 추정하지 않습니다. 가상 시각은 재생 표기일 뿐입니다.

시세/원장 생성: [build_fixture.py](../workspaces/mars-demo/scripts/build_fixture.py). 검증: [validate_fixture.py](../workspaces/mars-demo/scripts/validate_fixture.py). 원본 parquet/collection receipt는 이미지와 Git에서 제외하고, 허가받은 버전 fixture만 이미지에 둡니다.

## 시간·세션·Agent

- 서버 UTC 시각을 `Asia/Seoul`로 보여 주고 이미지 안의 XKRX calendar로 장전·장중·장마감·야간·주말·휴장·달력 범위 밖 상태를 계산합니다. 가상 사건·체결·현금·포지션·손익·차트·보고서·Agent 문맥은 하나의 원장 projection에서 나옵니다. 사건·bar는 현재 공개 가능한 시각 이후 화면/Agent로 전달하지 않습니다.
- 가상 arm/disarm, 주문, 원칙, 메모는 무작위 8시간 방문자 세션의 SQLite overlay에만 기록합니다. 새 session은 per-session counter를 새로 갖지만 session issuance rate limit과 DEMO 전체 KST 일일 counter가 제한합니다. daily counter는 named volume의 SQLite에 있어 image redeploy 뒤에도 유지됩니다.
- 기본값: 세션 5회/일, 전체 50회/일, 동시 생성 1, 세션 3회/분, 전체 15회/분, 질문 1,500자, UTF-8 요청 24,576 byte, context 4,000자, 출력 512 token, timeout 45초. 환경 변경은 DEMO 재배포로 적용하며 오늘의 사용량보다 낮게 limit을 내리면 그날 추가 요청을 거부합니다.
- Vertex `gemini-2.5-flash`, `us-central1` standard text rates는 2026-09-30에 검토해 입력 $0.30/M token, 출력 $2.50/M token을 계산에 씁니다. `50 × (24,576×0.30 + 512×2.50) / 1,000,000 = $0.43264/day`가 최대 token 범위에 대한 추정 상방입니다. 금액 hard cap은 없습니다. provider success는 실제 prompt/candidate token count와 latency를 기록하고, provider에 닿았을 수 있는 실패·timeout·재시작·usage 누락은 상한 예약 1회로 계산합니다. prompt/answer 본문은 기록하지 않습니다.
- `MARS_DEMO_AI_DAILY_HARD_CAP_USD`는 새 제품에서 읽지 않는 retired legacy setting입니다. 나중 cutover에서 old shared amount cap을 이 숫자·token policy와 혼동하지 않게 제거합니다. 공개 관리자 설정 UI는 없습니다.
- DEMO 전용 service account와 session signing key는 별도 DEMO Docker secret files로 공급합니다. Full credential·KIS key·FULL API endpoint는 DEMO app/runtime에서 참조하지 않습니다. 현재 작업 환경에 DEMO service account가 없어 실제 Vertex request는 실행하지 않았습니다. 설정 미비 경로만 로컬에서 검증하고, 실제 provider call/token/cost 결과는 0으로 기록합니다.

가격 source: [Google Cloud Vertex AI model pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing).

## 실패 격리와 검증 상태

| 단계 | 범위와 상태 |
|---|---|
| Path classification | `FULL-only`, `DEMO-only`, `shared-ui/contract`, `deployment-only`; 미분류 경로 실패. 분류기 테스트로 scope/no-op 확인 |
| DEMO app | 독립 TypeScript typecheck/lint/runtime contract. fake clock, session signature/isolation, 미래 사건 필터, 원장 reconciliation, SQLite lower-limit/restart unknown outcome 포함 |
| FULL app/API | FULL login frame refactor와 old DEMO route removal 후 scoped checks 실행 |
| Compose/OpenAPI | 하나의 DEMO web service, DEMO-only cookie/routes/secrets/volume/network, digest-pinned Portainer stack, v2 route contract 검사 |
| failure injection | GitHub DAG/path contract에 DEMO typecheck/build/contract/scan/publish 실패, FULL release 실패, DEMO-only no-op, 동일 SHA trigger를 모델로 실행. GitHub-hosted Actions workflow 자체를 임의의 실패로 실행한 결과와는 구분 |
| KIS/FULL boundary | DEMO code/image/compose에 KIS/FULL route, app, service, volume, secret reference가 없어야 함. HTTP/browser network trace는 기능 검수와 함께 확인 |
| Registry state | candidate build, scan, publish, manifest digest readback 상태를 별도 기록합니다. 로컬 adapter build와 registry publish를 같은 상태로 취급하지 않습니다. |
| NAS | 전용 NAS 접근이 없어 배포·promotion·운영 수치가 확인되지 않았습니다. NAS resource 수치로 주장하지 않습니다. local cgroup 비교 측정은 실제 NAS 측정과 분리해 기록합니다. |

Codex Security Deep Scan은 사용자 범위 밖이므로 실행하지 않습니다: `CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT`.
