# P1 Offline Demo 배포 및 검증

## 배포 경계

`P1_OFFLINE_DEMO`는 Decision Platform의 단독 수행 범위를 재현하는 로컬 교육용 배포다. 기본 DB
adapter와 선택 Kafka adapter를 제공하지만 provider, live account, live order, cross-market runtime은
열지 않는다. 두 bundle은 동일한 Spring/Python image digest를 사용하며 현재 지원 플랫폼은
`linux/amd64`뿐이다.

이 배포는 synthetic artifact와 offline fixture를 사용한다. Team B의 실물 Return Engine artifact,
Team A Dashboard consumer 통합, 실제 사용자 연구, 24시간 soak를 대신하지 않는다.

## 신뢰 경계

bundle 안의 스크립트는 자기 자신을 신뢰할 근거가 될 수 없다. archive를 풀기 전에 신뢰하는
host에서 release commit/tree SHA와 외부 checksum 서명을 먼저 검증한다. 각 archive에는 같은 이름의
`.sha256`과 `.sha256.bundle.json`이 함께 배포된다.

```bash
archive='capstone-p1-offline-demo-db-<version>-amd64.tar.zst'
identity='https://github.com/robinhood0107/Capstone-AI-Trading-Coach/.github/workflows/p1-offline-demo-release.yml@refs/heads/main'
cosign verify-blob --offline --bundle "$archive.sha256.bundle.json" \
  --certificate-identity "$identity" \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  "$archive.sha256"
sha256sum --check --strict "$archive.sha256"
```

두 명령이 모두 성공한 뒤에만 archive를 풀고 내부 `verify-release`를 실행한다. 내부 검증기는
`SHA256SUMS`, bundle root 서명, GitHub attestation, image digest를 다시 검증한다. tag는 편의를 위한
이름일 뿐 실행 권위가 아니며 Compose와 release manifest의 `name@sha256:...`가 실제 권위다.

## GHCR 배포

공식 이미지는 private GHCR package에 게시한다.

```text
ghcr.io/robinhood0107/capstone-spring-api
ghcr.io/robinhood0107/capstone-python-services
ghcr.io/robinhood0107/capstone-postgres-pgvector
```

팀원은 자기 계정에 부여된 package read 권한과 본인이 관리하는 `read:packages` token으로 로그인한다.
token을 `.env`, command argument, 문서, 메신저에 붙여 넣지 않는다.

```bash
printf '%s' "$GHCR_READ_TOKEN" | docker login ghcr.io -u '<GITHUB_USER>' --password-stdin
docker pull 'ghcr.io/robinhood0107/capstone-spring-api@sha256:<DIGEST>'
docker pull 'ghcr.io/robinhood0107/capstone-python-services@sha256:<DIGEST>'
docker pull 'ghcr.io/robinhood0107/capstone-postgres-pgvector@sha256:<DIGEST>'
unset GHCR_READ_TOKEN
```

공식 workflow는 exact merge SHA에서 amd64 이미지를 packages-write 권한이 없는 별도 job으로 build하고
Critical/High vulnerability와 secret을 검사한다. scan을 통과한 image archive와 SBOM/tool bytes는 SHA-256에
묶어 다음 job으로 전달한다. packages-write/OIDC 권한이 있는 publish job은 이 archive만 load하며 Dockerfile을
실행하거나 package를 설치하지 않는다. 같은 digest에 provenance/SBOM attestation을 붙이고 DB/Kafka offline
bundle도 그 digest로 생성한다. scan과 signature 사이에 이미지를 다시 build하지 않는다.

## Offline bundle

배포물은 mode별 archive와 외부 검증 파일 세 개의 묶음이다.

```text
capstone-p1-offline-demo-<mode>-<version>-amd64.tar.zst
capstone-p1-offline-demo-<mode>-<version>-amd64.tar.zst.sha256
capstone-p1-offline-demo-<mode>-<version>-amd64.tar.zst.sha256.bundle.json
```

archive에는 application 및 pinned infrastructure image, `pull_policy: never` Compose, 운영 스크립트,
non-secret template, SPDX/CycloneDX SBOM, provenance/attestation, checksum, third-party notice와 저장소의
AGPL-3.0-only `LICENSE` 원문이 들어간다. DB volume/dump, `.env`, signing private key,
account/provider/order data, raw RAG/artifact는 들어가지 않는다.

외부 검증을 통과한 archive를 푼 뒤 실행한다.

```bash
cd deploy/p1
./verify-release
./p1ctl init
./p1ctl verify
./p1ctl up db
./p1ctl smoke
./p1ctl status
```

`verify-release`와 `p1ctl`은 원본 경로를 직접 활성화하지 않는다. owner-only destination에 symlink, hardlink,
special file, group/other-writable file을 거부하면서 전체 bundle을 descriptor-relative로 복사하고, 복사 전후
inode/size/time identity가 같은 sealed snapshot만 검증한다. 이후 manifest, release env, Compose, guard,
backup/restore script와 image archive는 모두 그 snapshot 경로에서만 다시 열린다.

Kafka 경로는 먼저 DB mode를 중지하고 선택한다.

```bash
./p1ctl stop
./p1ctl up kafka
./p1ctl smoke
```

Kafka UI는 P1 배포물에 포함하지 않는다. 검토 시점의 후보 이미지가 High 취약점 0 조건을 통과하지
못했기 때문에 `P1_KAFKA_UI=DEFERRED_SECURITY_GATE`다. Kafka broker와 exact topic initializer는 CLI와
상태 API로 검증하며, 인증·loopback·High 0을 모두 검증한 UI digest가 생기기 전까지 UI를 추가하지 않는다.

## Approval trust policy

provider 실행기는 환경변수로 public key를 교체하지 않는다. 운영자가 관리하는 고정 경로
`/etc/capstone-p1/approval-trust-root.json`만 읽으며 policy와 public key는 root 소유 regular file,
symlink 아님, group/other writable 아님이어야 한다. 설치 예시는 다음과 같다.

```bash
sudo install -d -o root -g root -m 0755 /etc/capstone-p1
sudo install -o root -g root -m 0644 issuer-public.pem /etc/capstone-p1/issuer-public.pem
sudo install -o root -g root -m 0644 approval-trust-root.json /etc/capstone-p1/approval-trust-root.json
```

policy는 `contractId`, `issuerKeyId`, `publicKeyPath`, `publicKeySha256` 네 key만 가진 closed JSON이며
`contractId`는 정확히 `p1-approval-trust-root.v1`이어야 하고 다른 key는 허용되지 않는다.
`publicKeyPath`는 위 고정 directory의 absolute path, SHA256은 public key bytes의 64자리 소문자 hex다.
`P1_APPROVAL_*` 환경변수는 trust root가 아니며 실행기가 무시한다. private key는 이 directory,
repository, image, container 또는 executor environment에 두지 않는다.

## Secret과 초기화

`p1ctl init`은 새 state directory에서만 성공한다. PostgreSQL 역할별 credential, JWT/HMAC/gRPC secret,
demo password 및 credential bundle은 서로 다른 랜덤 값으로 생성된다. state와 secret directory는 0700,
runtime env는 0600, service별 secret file은 0640이다. 현재 host group만 해당 service의 보조 group으로
부여하며 container를 root로 전환하지 않는다. symlink, 예상 외 파일, 과도한 크기, 잘못된 mode는 거부된다.

초기화는 destructive reset이 아니다. 기존 state나 volume이 있으면 자동으로 덮어쓰거나 회전하지 않는다.
`reset`과 volume 삭제 명령은 제공하지 않는다.

## DB 설치와 upgrade

새 DB는 `B86__p1_offline_demo_baseline.sql`로 baseline schema를 설치한 뒤 V87 forward migration을
적용한다. 기존 DB는 V1~V86 history를 보존한 채 V87을 적용한다. 두 경로 모두 `role-bootstrap`이 전용 `decision_auth` 역할과 최소 권한을 먼저
보장한 뒤 Flyway가 실행되므로 PostgreSQL fresh-init script에만 의존하지 않는다. baseline은
credential/verifier와 runtime/audit/outbox/RAG/user data를 포함하지 않으며 one-shot identity bootstrap이
demo identity를 별도로 설치한다.

다음 동등성은 자동 검증 대상이다.

- historical V1→V87과 B86→V87 fresh schema/ACL/RLS/function/trigger/static seed parity
- V86→V87 upgrade
- 기존 DB에서 baseline 적용 0
- fresh DB에서 하위 V migration 적용 0
- 양 경로에서 다음 synthetic V migration 적용 가능
- credential bootstrap/rotation/login parity

## 운영 명령

```text
./p1ctl verify
./p1ctl init
./p1ctl up db
./p1ctl up kafka
./p1ctl smoke
./p1ctl status
./p1ctl logs --redacted
./p1ctl stop
./p1ctl backup
./p1ctl restore-test
```

`stop`은 volume을 보존한다. `logs`는 redacted form만 제공한다. release 검증은 같은 project와 volume에서
DB→Kafka→DB로 adapter를 전환하고 매 단계 smoke/restart를 수행하며 secret fingerprint가 바뀌지 않았음을
확인한다. 이 과정과 cleanup은 container만 중지하고 volume을 삭제하지 않는다.

backup은 owner metadata를 보존하는 custom dump를 exact Compose project에서 만들고 owner-only mode로
저장한다. `restore-test`는 별도 격리 project/volume에 ownership을 포함해 복원한 뒤 Flyway version 87,
핵심 table/function owner `flyway`, active demo trust root, active approval authority를 확인한다. 운영 DB에
restore하지 않으며 격리 volume도 자동 삭제하지 않고 출력된 project 이름으로 회수 가능하게 남긴다.

## Network와 container 경계

- API는 secret 없는 최소 TCP edge relay를 통해서만 `127.0.0.1`에 publish한다. Kafka UI는 배포하지 않는다.
- PostgreSQL, Redis, Kafka, gRPC port는 host에 publish하지 않는다.
- Spring과 Python은 공용 network namespace에서 loopback gRPC를 사용한다.
- Kafka mode도 numeric loopback broker guard를 유지한다.
- Spring/Python/DB/Kafka runtime network는 Docker `internal: true`다. edge relay만 internal network와
  별도 edge network에 연결되며 secret/DB credential을 받지 않는다. provider/live env는 exact OFF다.
- application container는 migration/bootstrap credential을 받지 않는다.
- application은 non-root, read-only root filesystem, tmpfs, `cap_drop: ALL`, no-new-privileges로 실행한다.
- Kafka named-volume initializer만 네트워크와 secret 없이 one-shot으로 `CHOWN,FOWNER`를 사용한 뒤 종료한다.

## 실패 대응

- `P1_ERROR=not_initialized`: `p1ctl init`을 한 번 실행한다.
- `P1_ERROR=already_initialized`: 기존 state를 덮어쓰지 않는다. 현재 project/state 선택이 맞는지 확인한다.
- `P1_ERROR=stop_before_adapter_switch`: `p1ctl stop` 뒤 adapter를 바꾼다.
- `P1_ERROR=release_*`: release manifest/image digest/checksum 불일치다. 실행하지 말고 배포자에게 확인한다.
- health/login/synthetic smoke 실패: `p1ctl logs --redacted`를 보존하고 volume 삭제 없이 원인을 조사한다.
- backup/restore-test 실패: 원본 volume을 변경하지 말고 별도 restore project의 실패만 조사한다.

## 현재 완료와 남은 gate

이 문서의 marker는 실제 test/image/release 영수증이 모두 green인 merge SHA에서만 최종화한다.

```text
P1_ARM64=DEFERRED_UNVERIFIED
S8_1_REAL_ARTIFACT_BLOCKED=TRUE
TEAM_A_INTEGRATED=FALSE
PARTICIPANT_IRB_EXECUTION=NONE
KIS_MOCK_LIVE_PROVIDER_EXECUTION=0
PROVIDER_LIVE_ACCOUNT_ORDER_CALLS=0
24H_SOAK=NOT_RUN
CROSS_MARKET_RUNTIME=RETIRED_NOT_APPLICABLE
P1_OVERALL=INCOMPLETE_EXTERNAL_ARTIFACT
```

실물 Return Engine 검증 절차는
[P1 실물 artifact 잔여 체크리스트](P1_실물_artifact_잔여_체크리스트.md)를 따른다.
