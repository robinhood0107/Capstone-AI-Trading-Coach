# MARS — AI 트레이딩 코치와 모의 자동매매

MARS는 투자 근거를 검토하고 사용자별 규칙에 따라 KIS 모의투자를 실행하는 프로젝트입니다. 공개 Docker Hub 이미지는 로그인 없이 제한된 Agent만 볼 수 있는 **DEMO**와, Google 로그인 및 사용자별 계좌 연결을 제공하는 **FULL** 두 제품으로 나뉩니다.

자동매매는 수익을 보장하지 않습니다. 공개 이미지 발행은 서비스의 실운영 준비나 대규모 동시 사용자 검증을 의미하지 않습니다.

## 공개 제품

| 제품 | 접근 방식 | 기능 경계 |
|---|---|---|
| DEMO | 로그인 없이 이용 | 제한된 Agent를 제공합니다. 사용자 KIS 자격증명, 계좌 조회, 주문과 자동운용은 제공하지 않습니다. |
| FULL | Google OIDC 로그인 | 첫 로그인에 USER를 만들고, 사용자가 직접 준비한 KIS 모의투자 자격증명을 웹 UI에서 등록합니다. 자동운용과 대사는 해당 사용자의 계좌 범위에서 실행됩니다. |

FULL의 기존 password 로그인은 공개 외부 경로에 노출되지 않습니다. KIS Live 주문은 이 공개 제품의 범위가 아니며, KIS_MOCK만 사용합니다. 사용자는 본인이 준비한 App Key, App Secret, 계좌번호를 본인 계정에서만 사용해야 합니다.

AI provider 사용량과 추정 비용은 계측용으로 기록합니다. 현재 이미지에는 MARS 차원의 일일 비용 hard cap이 없어 해당 금액으로 요청을 차단하지 않습니다. 실제 사용 가능량과 청구는 Google Cloud·Voyage 계정의 한도와 과금 설정에 따릅니다.

## 이미지와 Release

공개 저장소는 다음 두 개입니다.

- DEMO: [`pjjpjj111/mars-demo`](https://hub.docker.com/r/pjjpjj111/mars-demo)
- FULL: [`pjjpjj111/mars-full`](https://hub.docker.com/r/pjjpjj111/mars-full)

[GitHub Releases](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/releases/latest)의 각 Release는 동일한 main merge commit에서 만든 이미지 세트를 가리킵니다. 제품별로 `api`, `web`, `postgres`, `redis` 네 이미지 태그가 있으며, Release에는 아래 자산이 함께 있습니다.

- `mars-images.json`: 8개 이미지의 저장소, 태그, 검증한 digest와 원본 commit
- `mars-public-demo.compose.yml`, `mars-public-full.compose.yml`: digest로 고정된 실행 Compose 파일
- 각 이미지의 SPDX SBOM 5개

이미지를 검사하거나 내려받으려면 Docker와 GitHub CLI가 필요합니다.

```bash
TAG="$(gh release view --repo robinhood0107/Capstone-AI-Trading-Coach --json tagName --jq .tagName)"

for product in mars-demo mars-full; do
  for component in api web postgres redis; do
    docker pull "pjjpjj111/${product}:${TAG}-${component}"
  done
done

mkdir -p mars-release
gh release download "$TAG" --repo robinhood0107/Capstone-AI-Trading-Coach --pattern 'mars-images.json' --pattern 'mars-public-demo.compose.yml' --pattern 'mars-public-full.compose.yml' --dir mars-release
```

실행 Compose 파일은 이동 가능한 `latest` 태그 대신 Release manifest에서 확인한 `sha256` digest를 사용합니다. 한 제품의 네 이미지를 한 Release의 Compose 파일과 함께 사용하세요.

## Compose 실행

Compose 파일은 Docker Engine, Docker Compose v2, 제품별 비밀값 디렉터리와 비공개 키 자료를 요구합니다. Compose는 이 비밀값을 만들어 주지 않습니다. DEMO도 운영자가 관리하는 Vertex 서비스 계정이 필요합니다. FULL에는 Google OIDC 설정, Vertex 프로젝트와 서비스 계정, 브로커리지 자격증명 암호화 키 디렉터리 등 추가 설정이 필요합니다.

먼저 Release 파일에 맞는 secret 파일과 비밀이 아닌 경로·포트·모델 ID를 `.env`에 준비한 뒤 실행합니다.

```bash
docker compose --env-file .env -f mars-release/mars-public-demo.compose.yml up -d --wait
```

```bash
docker compose --env-file .env -f mars-release/mars-public-full.compose.yml up -d --wait
```

기본 포트는 DEMO `3001`, FULL `3002`이며 둘 다 호스트 loopback에만 바인딩됩니다. 외부 접속에는 별도의 TLS reverse proxy가 필요합니다. FULL의 공개 origin과 Google OIDC redirect URI는 `https://mars.royaljellynas.org`와 `https://mars.royaljellynas.org/api/v1/auth/oidc/callback/google`입니다. Google OAuth 설정에도 같은 redirect URI를 등록해야 합니다.

필수 secret 파일은 다음과 같습니다.

- DEMO: `postgres.env`, `redis.env`, `role-bootstrap.env`, `migration.env`, `actor-capability-authority.env`, `actor-server.p12`, `actor-client.p12`, `actor-tls-ca.crt`, `mars-public-demo.env`, `rag-history-kek-v1.key`, `vertex-service-account.json`
- FULL: 위 공통 DB·actor·RAG·Vertex 파일과 `seed-import.env`, `mars-public-full.env`

Compose의 비밀이 아닌 필수 설정은 DEMO에서 `MARS_DEMO_SECRET_GID`, `MARS_DEMO_SECRETS_DIR`, `MARS_VERTEX_MODEL_ID`; FULL에서 `MARS_FULL_SECRET_GID`, `MARS_FULL_SECRETS_DIR`, `MARS_FULL_BROKERAGE_KEK_DIR`, `MARS_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256`, `MARS_VERTEX_MODEL_ID`, `MARS_VERTEX_PROJECT_ID`입니다. 포트 변수는 선택입니다. 각 설정의 세부 형식은 저장소의 [`compose.public-demo.yml`](deploy/p1/compose.public-demo.yml)과 [`compose.public-full.yml`](deploy/p1/compose.public-full.yml)에 있습니다.

DEMO와 FULL은 각자 다른 Compose 프로젝트, secret 디렉터리와 데이터 볼륨으로 실행하세요. API 키, 계좌번호, 서비스 계정 JSON, OAuth secret, 암호화 키는 Git·`.env`·로그·채팅에 넣지 마세요.

## 발행과 검증 상태

main에 병합된 develop PR만 Docker Hub에 공개 이미지를 발행하고 GitHub Release를 만듭니다. 승격 PR에서 전체 CI와 다섯 이미지 후보의 취약성 검사를 실행합니다. Release workflow는 같은 main merge commit으로 이미지를 다시 빌드하고 스캔한 뒤 Docker Hub digest와 Release manifest를 대조합니다.

현재 공개 Release의 `imagePublicationReady`는 `true`, `serviceReady`는 `false`입니다. 이는 이미지 빌드·공개를 검증했다는 뜻이며, NAS에 실제 배포했거나 N=10/50/100 동시 부하 및 운영 준비를 검증했다는 뜻은 아닙니다. NAS 배포, 자동 배포, KIS Live는 이 범위에 포함하지 않습니다.

## 개발 및 계약 문서

- [최종 프로젝트 명세](docs/최종_프로젝트_명세서.md)
- [API 명세](docs/API_명세서.md)
- [문서 인덱스](docs/README.md)
- [공개 DEMO Compose 원본](deploy/p1/compose.public-demo.yml)
- [공개 FULL Compose 원본](deploy/p1/compose.public-full.yml)

`.env`, provider 자격증명, 계좌번호, OAuth secret, JWT secret과 private key는 저장소에 추가하지 마세요.
