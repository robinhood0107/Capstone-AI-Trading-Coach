# decision-platform

담당: 박종진 (`robinhood0107`)

투자 원칙(Principle) → 평가(Decision/RiskEngine) → 모의 주문(Brokerage) → RAG 설명까지를 담당하는 워크스페이스. Kotlin(Spring) API 서버와 Python(gRPC/FastAPI) 서비스 두 축으로 구성된다.

## 구조

```
spring-api/            # Gradle Kotlin 프로젝트 — Controller/Application/Domain/Infrastructure
python-services/        # uv 프로젝트 — LightGBM/RAG/금융공학/데이터클라이언트/브로커리지 어댑터
```

## 세팅

공개 레포에는 최종 명세/API 계약과 구현 코드를 두고, 상세 개인 참고 노트는 루트의 ignored `private-reference/` 폴더에서만 관리한다. 요약:

1. `cp ../../.env.example ../../.env` 후 PostgreSQL/Redis/JWT/demo password와 필요한 provider secret을 채운다.
2. `docker compose --env-file ../../.env -f ../../infra/docker-compose.infra.yml up -d`로 loopback-only PostgreSQL/Redis를 기동한다.
3. `spring-api/`는 커밋된 Gradle wrapper로 `./gradlew ktlintCheck build`를 실행한다.
4. `python-services/`는 `uv sync --frozen` 후 `uv run pytest`, `uv run ruff check .`, `uv run mypy app`으로 검증한다.

기존 PostgreSQL volume을 유지하는 경우 루트 README의 one-time application role bootstrap 절차를 먼저 따른다. Redis는 password+AOF+`noeviction`이며 OpenDART quota 원장으로는 사용하지 않는다.

KIS outbound는 이 workspace가 단일 owner다. S1.1 client는 실전 18/s hard cap·기본 120ms 간격, 모의 1/s·1,000ms 간격을 같은 opaque credential/appkey scope의 Redis 원자 limiter로 공유한다. `/oauth2/tokenP` physical send는 mock/live 합산 deployment-global 1/s를 보수 적용하고 token cache/singleflight만 mode별로 분리한다. Return Engine과 후속 S1.6/S3 adapter는 별도 limiter를 만들지 않고 이 경계를 재사용한다.

## KRX universe 자동화

S1.3K는 KRX OPEN API의 `유가증권 일별매매정보`와 `코스닥 일별매매정보`만 사용해
내부 top-30 universe를 만든다. 운영 계정은 31개 서비스 entitlement를 모두 승인받았지만
runtime allowlist는 NOW 두 개로 고정한다. KRX1은 physical `0`·Redis `0→1`, KRX2는 첫 NOW
endpoint handoff 뒤 원인 미분류 `collection_failed`로 physical `1`·Redis `1→2`, KRX3는 같은
첫 handoff의 `authentication_failed(401_or_403)`로 physical `1`·Redis `2→3`을 기록했고 online 성공
산출물은 없다. KRX4는 HEAD `971ea39418ba`, 기준일 `2026-07-15`의 첫 handoff에서
`transport_unavailable`, physical `1`, Redis `3→4`로 중단했고 KOSDAQ·retry·online artifact는 `0`이다.
예약 뒤 `5.279초`라 당시 5초 read timeout 가능성이 가장 높지만 예외 타입이 소실되어 확정하지
않는다. 별도 sanitized RCA evidence SHA-256은
`30326a713ab1c638a2897412ccb50dc3fce44408e73ef47a1ad4db3d9b468033`이다. KRX5는 HEAD
`9d2dcdea937d`, 같은 기준일의 첫 endpoint에서 HTTP `200` 뒤 기존 validation 관측성 축약의
`invalid_response`, physical `1`, Redis `4→5`로 중단했고 KOSDAQ·retry·online artifact는 `0`이다.
failure/RCA SHA-256은 각각 `969711e95c12fdd4e51bc1a3fdbaa7983f36c5c46d622cbe406b3b7775d217b4`와
`d08eac2d2c443f39b1ff940ccea7fefe130775ce76c7d789375b45e64c16ca56`이다. 공식 성공 shape는
strict parser가 그대로 수용하므로 계약을 느슨하게 하지 않고 media/body/JSON/envelope/row typed
diagnostic만 보강했다. KRX6/7은 TTL 만료로 provider `0`이다. KRX8은 HEAD `4783432ad7de`,
첫 endpoint `read_timeout`, physical `1`, Redis `5→6`이며 KOSDAQ·retry·online artifact는 `0`이다.
failure/RCA SHA-256은 `a2547290e39fe63c1ceda9171beb4dd701c9db8938182e6e93d08e5aacf23dca`와
`53bc9d4e001b839fe2692be61271f5954fc9e9703b6f94501ac782b128029d62`이다. KRX9는 TTL 만료로
provider `0`이며 packet SHA-256은 `7aae38e0cc3b721557d93ca16fdd4576f890b2d257b743bab953c01a33304364`다.
KRX10은 HEAD `cd212c8e22ac`에서 KOSPI probe를 1회 실행해 HTTP `200`, physical `1`, Redis rolling
`3→4`까지 도달했지만 row `10`의 `ISU_CD`를 숫자 6자리로만 제한한 `row_symbol_invalid`에서
중단했다. KOSDAQ·final refresh·retry·파일 생성은 `0`이고 failure evidence SHA-256은
`3acefd3b5f772050b58ece93397db1123f4412355fe5bf3982385e8e639bd320`이다.
KRX1~5/KRX8/KRX10 실패, KRX6/7/9 만료 packet, 기존 S1.3 A4/B1 승인은 재사용하지 않는다.

공식 KRX source `ISU_CD`는 exact ASCII uppercase alphanumeric 6자 `[0-9A-Z]{6}`로 검증하고
영문 포함 행도 row count·중복검사·canonical hash에 포함한다. 기존 KIS/Naver manifest와
positive candidate/top-30은 exact 숫자 6자리 `[0-9]{6}`만 허용한다.

KRX11 `approval-krx11-81aed4c1fad6-20260716T122917Z`는 HEAD `81aed4c1fad6`, 기준일
`2026-07-15`에서 `KOSPI probe 1 → KOSDAQ probe 1 → full refresh 2`를 순서대로 성공했다.
KOSPI는 row `944`·양수 후보 `887`·SHA
`4f8e4849ac655598d0bb1ce736d7c0ff4436168eeb232c7bfa2364ee830cfda6`·`11,943ms`,
KOSDAQ은 row `1,821`·후보 `1,690`·SHA
`cc2ae17c110196c2daeaa73c1592930d76a2821addab5068c2bd963d5b0350c7`·`14,019ms`를
기록했다. final refresh는 physical `2`로 두 시장을 다시 검증해 source SHA
`f23bbd75c55121c65351fa10f47a86871a8e0082a03cab3df8e816527e18c9d1`, manifest/report
SHA `ed979913de7415146cbb56df97bdf4eddeec3c21bc4792f4c03d802c7596674e`/
`625caa61ab8cb5382b5da7acc84741f38c1cab5dc2edb1ff2901108c27dc8671`, 30종목, rank 1
`005930/삼성전자`를 게시했다. Redis는 `4→8`, retry·추가 호출·cooldown은 `0`이다. success
evidence와 소비 완료 packet SHA는
`57d66380e2a86c928bf21a69d9e626fa697d487cf878378558aa26959e3f64c9`/
`58dc47bf96f644b634d76cec6bd08caedd06cc1c8e829419e6d9bf6f49492619`다.
완료 뒤 KRX `313`, S1.3 matrix `892`, 전체 Python `1086` 테스트와 Ruff, mypy `78` source
files, lock, contracts, JDK 25 Gradle build, Compose, repo hygiene, gitleaks를 모두 통과했다.

이후 online 실행도 probe timeout `2/120/2/1초 + logical 130초`, full refresh
`2/120/2/1초 + shared logical 260초`, retry `0` 계약을 유지한다. 각 프로세스 cap은 `1/1/2`이고
합계 `4`는 approval packet과 executor stop rule이 강제한다. final HEAD·기준일·명령 순서·발급
직전 Redis rolling baseline·TTL에 결속한 새 exact 승인을 받은 경우에만 다음 세 명령을 순차 실행한다.

```bash
cd python-services
uv run krx-openapi-service-probe --online --as-of YYYY-MM-DD --service stk_bydd_trd
uv run krx-openapi-service-probe --online --as-of YYYY-MM-DD --service ksq_bydd_trd
uv run krx-openapi-universe-refresh --online --as-of YYYY-MM-DD --data-dir data/kis
```

probe는 service·기준일·row/양수 후보 수·canonical SHA-256·elapsed ms·physical `1`만 출력하고
manifest/report를 쓰지 않는다. `--online`은 로컬 안전 gate일 뿐 사용자 실행 승인을 대체하지 않는다.
API 실패를 CSV나 이전
manifest 성공으로 바꾸지 않으며, 수동 CSV는 기존 `kis-universe-refresh`를 별도 명령으로 실행할
때만 사용한다. ASCII `YYYY-MM-DD`와 approved ignored `data/` root 내부 output만 허용한다.
성공·실패 출력에는 caller argv·로컬 경로 대신 안정 code, physical attempt 수와 검증된 allowlist
typed diagnostic scalar만 남고,
client cleanup이 성공한 뒤에만 서로 다른 report와 manifest target이 게시된다. 이 PR은 주기
scheduler를 추가하지 않는다.
