# MARS FULL · NAS / Portainer 배포

릴리스 이미지를 NAS 에서 Portainer 스택 하나로 운영한다. 비밀 파일은 폴더에서 읽고, DB 와 KEK 는 미리 만든 외부 볼륨을 쓴다.

| 파일 | 쓰는 곳 |
|---|---|
| `mars-full.stack.yml` | Portainer → Stacks → Web editor |
| `stack.env.example` | Portainer → Environment variables → Advanced mode |
| `nas-bundle.sh` | 기존 PC 배포를 옮길 때 비밀 폴더와 DB 볼륨을 묶는다 |

## 무엇을 어디에 두나

| 무엇 | 위치 | 이유 |
|---|---|---|
| `full-secrets/` 비밀 파일 16개 | NAS 폴더 `MARS_SECRETS_ROOT/full-secrets` | 서비스마다 필요한 파일만 받는다 |
| `brokerage-kek-v1.key` (KIS·Vertex 자격증명 암호화 키, 32바이트) | 외부 볼륨 `mars-full_brokerage-kek` | API 는 KEK 폴더 `0700`·파일 `0600`·소유자 uid 65532 일 때만 기동한다. 시놀로지 공유 폴더는 ACL 때문에 이 모드를 지키지 못한다 |
| DB | 외부 볼륨 `mars-full_postgres-data` | 스택을 지워도 남는다 |

**KEK 와 DB 는 반드시 한 세트로 옮긴다.** DB 안의 KIS·Vertex 자격증명은 이 키로만 풀린다.

스택의 `kek-permissions` 가 매 기동마다 KEK 볼륨의 소유자·모드를 다시 맞춘다. 키가 없거나 32바이트가 아니면 `KEK_MISSING` 으로 멈춘다.

## 기존 PC 배포 옮기기

PC(Ubuntu 터미널):

```bash
bash deploy/p1/portainer/nas-bundle.sh
scp ~/mars-nas-config.tgz ~/mars-nas-db.tgz <nas>:/volume1/docker/mars-full/
```

NAS(SSH), 한 번만:

```bash
cd /volume1/docker/mars-full && sudo tar xzpf mars-nas-config.tgz
sudo chgrp -R $(id -g) full-secrets && sudo chmod -R o-rwx full-secrets

# DB
sudo docker volume create mars-full_postgres-data
sudo docker run --rm -v mars-full_postgres-data:/to -v /volume1/docker/mars-full:/from:ro alpine tar xzpf /from/mars-nas-db.tgz -C /to
sudo docker run --rm -v mars-full_postgres-data:/v:ro alpine ls /v/pgdata/PG_VERSION

# KEK: 공유 폴더에서 볼륨으로 옮기고 폴더 사본은 지운다
sudo docker volume create mars-full_brokerage-kek
sudo docker run --rm -v mars-full_brokerage-kek:/k -v /volume1/docker/mars-full/full-kek:/src:ro alpine cp /src/brokerage-kek-v1.key /k/
sudo docker run --rm -v mars-full_brokerage-kek:/k:ro alpine wc -c /k/brokerage-kek-v1.key   # 32 이어야 한다
sudo rm -rf full-kek mars-nas-config.tgz mars-nas-db.tgz
```

새 배포라면 `mars-full_postgres-data` 볼륨은 비워서 만들고(첫 기동에서 마이그레이션이 채운다), `mars-full_brokerage-kek` 에는 `head -c 32 /dev/urandom` 으로 만든 32바이트 키를 넣는다.

## 스택

1. Stacks → Add stack → 이름 `mars-full` → Web editor 에 `mars-full.stack.yml` 붙여넣기
2. Environment variables → Advanced mode 에 `stack.env.example` 을 붙여넣고 값 채우기
3. Deploy

| 변수 | 설명 |
|---|---|
| `MARS_FULL_TAG` | 이미지 버전(GitHub Release). 업그레이드는 이 값만 바꾸고 Re-pull 로 Update |
| `MARS_PUBLIC_ORIGIN` | 브라우저 주소와 글자까지 같아야 한다. 다르면 로그인이 403 |
| `MARS_SECRETS_ROOT` | `full-secrets/` 가 든 폴더(절대경로) |
| `MARS_FULL_SECRET_GID` | 호스트에서 `id -g` |
| `MARS_VERTEX_PROJECT_ID` | Vertex 프로젝트 ID |
| `MARS_VERTEX_SERVICE_ACCOUNT_SHA256` | 서비스 계정 JSON 의 `sha256sum` |
| `MARS_PORT` | NAS 에서 열 웹 포트(기본 3002). 역방향 프록시 대상과 같게 |

DSM 역방향 프록시로 `https://<도메인>` → `http://localhost:<MARS_PORT>` 를 연결하고, Google·Kakao 콘솔의 리디렉션 URI 를 `<MARS_PUBLIC_ORIGIN>/api/v1/auth/oidc/callback/{google,kakao}` 로 바꾼다.

## 시놀로지에서 알려진 차이

| 증상 | 원인 | 스택이 하는 일 |
|---|---|---|
| `NanoCPUs can not be set` | 커널이 CPU CFS 미지원 | `cpus:` 제한을 두지 않는다. `mem_limit` 은 동작한다 |
| API unhealthy, `SQLState 57014`, `Error accessing tables metadata` | 느린 디스크에서 기동 시 Hibernate 스키마 검사가 `decision_app` statement_timeout(2s) 초과 | `SPRING_JPA_HIBERNATE_DDL_AUTO=none`. 스키마는 `migrate`(Flyway)가 맞춘다 |
| API unhealthy, `BROKERAGE_KEK_UNAVAILABLE` | 공유 폴더 ACL 이 KEK 모드 `0700/0600` 을 깨뜨림 | KEK 를 외부 볼륨에 두고 `kek-permissions` 가 매 기동마다 맞춘다 |
| 느린 CPU 에서 앱보다 헬스체크가 먼저 포기 | 기본 유예가 짧음 | `start_period` 를 postgres 5분·api 15분 등으로 넉넉히 둔다. 먼저 healthy 가 되면 즉시 통과 |
| `secret file not found` | Portainer 가 compose 를 자기 컨테이너 안에서 실행 | Portainer 컨테이너에 `MARS_SECRETS_ROOT` 를 같은 경로로 마운트한다 |

- DB·KEK 볼륨은 `@docker` 아래라 Hyper Backup 에 잡히지 않는다. `pg_dump -Fc` 와 KEK 사본을 작업 스케줄러로 공유 폴더에 떠서 **암호화 백업**한다
- 같은 KIS 계좌로 두 배포를 동시에 켜지 않는다
