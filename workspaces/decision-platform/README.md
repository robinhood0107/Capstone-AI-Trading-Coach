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
첫 handoff의 `authentication_failed(401_or_403)`로 physical `1`·Redis `2→3`을 기록했고 성공
산출물은 없다. 세 실패 packet과 기존 S1.3 A4/B1 승인은 재사용하지 않는다. `.env`의 인증키, 현재 HEAD,
기준일, 2회 호출, Redis 기준값, TTL에 결속한 새 KRX 실행 승인을 받은 경우에만 다음 명령을
정확히 1회 실행한다.

```bash
cd python-services
uv run krx-openapi-universe-refresh --online --as-of YYYY-MM-DD --data-dir data/kis
```

`--online`은 로컬 안전 gate일 뿐 사용자 실행 승인을 대체하지 않는다. API 실패를 CSV나 이전
manifest 성공으로 바꾸지 않으며, 수동 CSV는 기존 `kis-universe-refresh`를 별도 명령으로 실행할
때만 사용한다. ASCII `YYYY-MM-DD`와 approved ignored `data/` root 내부 output만 허용한다.
성공·실패 출력에는 caller argv·로컬 경로 대신 안정 code와 physical attempt 수만 남고,
client cleanup이 성공한 뒤에만 서로 다른 report와 manifest target이 게시된다. 이 PR은 주기
scheduler를 추가하지 않는다.
