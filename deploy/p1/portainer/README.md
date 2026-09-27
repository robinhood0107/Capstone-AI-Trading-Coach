# MARS FULL · NAS / Portainer 배포

릴리스 이미지를 NAS 에서 Portainer 스택 하나로 운영한다. 비밀값은 파일로만 읽고, DB 는 미리 복원한 외부 볼륨을 쓴다.

| 파일 | 쓰는 곳 |
|---|---|
| `mars-full.stack.yml` | Portainer → Stacks → Web editor |
| `stack.env.example` | Portainer → Environment variables → Advanced mode |
| `nas-bundle.sh` | 기존 PC 배포를 옮길 때 비밀 폴더와 DB 볼륨을 묶는다 |

## 준비

NAS 에 폴더 하나를 정하고(`MARS_SECRETS_ROOT`, 예: `/volume1/docker/mars-full`) 아래 두 폴더를 둔다.

- `full-secrets/`: 서비스별 비밀 파일 16개. 새 배포는 `deploy/p1/assemble_mars_public_secrets.py` 로 만든다
- `full-kek/`: KIS·Vertex 자격증명 암호화 키. 소유자 uid 65532, 권한 700. **DB 와 반드시 한 세트로 옮긴다**

```bash
sudo chgrp -R $(id -g) /volume1/docker/mars-full/full-secrets
sudo chmod 700 /volume1/docker/mars-full && sudo chmod -R o-rwx /volume1/docker/mars-full/full-secrets
```

## 기존 PC 배포 옮기기

PC(Ubuntu 터미널):

```bash
bash deploy/p1/portainer/nas-bundle.sh
scp ~/mars-nas-config.tgz ~/mars-nas-db.tgz <nas>:/volume1/docker/mars-full/
```

NAS(SSH):

```bash
cd /volume1/docker/mars-full && sudo tar xzpf mars-nas-config.tgz
sudo docker volume create mars-full_postgres-data
sudo docker run --rm -v mars-full_postgres-data:/to -v /volume1/docker/mars-full:/from:ro alpine tar xzpf /from/mars-nas-db.tgz -C /to
sudo docker run --rm -v mars-full_postgres-data:/v:ro alpine ls /v/pgdata/PG_VERSION
sudo rm mars-nas-config.tgz mars-nas-db.tgz
```

새 배포라면 볼륨만 만들고(`docker volume create mars-full_postgres-data`) 스택을 올리면 첫 기동에서 마이그레이션이 채운다.

## 스택

1. Stacks → Add stack → 이름 `mars-full` → Web editor 에 `mars-full.stack.yml` 붙여넣기
2. Environment variables → Advanced mode 에 `stack.env.example` 을 붙여넣고 값 채우기
3. Deploy

| 변수 | 설명 |
|---|---|
| `MARS_FULL_TAG` | 이미지 버전(GitHub Release). 업그레이드는 이 값만 바꾸고 Re-pull 로 Update |
| `MARS_PUBLIC_ORIGIN` | 브라우저 주소와 글자까지 같아야 한다. 다르면 로그인이 403 |
| `MARS_SECRETS_ROOT` | `full-secrets/`, `full-kek/` 가 든 폴더(절대경로) |
| `MARS_FULL_SECRET_GID` | 호스트에서 `id -g` |
| `MARS_VERTEX_PROJECT_ID` | Vertex 프로젝트 ID |
| `MARS_VERTEX_SERVICE_ACCOUNT_SHA256` | 서비스 계정 JSON 의 `sha256sum` |
| `MARS_PORT` | NAS 에서 열 웹 포트(기본 3002). 역방향 프록시 대상과 같게 |

DSM 역방향 프록시로 `https://<도메인>` → `http://localhost:<MARS_PORT>` 를 연결하고, Google·Kakao 콘솔의 리디렉션 URI 를 `<MARS_PUBLIC_ORIGIN>/api/v1/auth/oidc/callback/{google,kakao}` 로 바꾼다.

## 시놀로지에서 알려진 차이

- 커널이 CPU CFS 를 지원하지 않아 `cpus:` 제한을 두지 않는다(`NanoCPUs can not be set`). `mem_limit` 은 동작한다
- 느린 디스크에서는 기동 시 Hibernate 스키마 검사가 `decision_app` 의 `statement_timeout`(2s)을 넘겨 API 가 unhealthy 가 된다(`SQLState 57014`, `Error accessing tables metadata`). 스택은 `SPRING_JPA_HIBERNATE_DDL_AUTO=none` 으로 검사를 끈다. 스키마는 `migrate` 가 맞춘다
- Portainer 가 비밀 파일을 못 찾으면(`secret file not found`) Portainer 컨테이너에 `MARS_SECRETS_ROOT` 를 같은 경로로 마운트한다
- DB 볼륨은 `@docker` 아래라 Hyper Backup 에 잡히지 않는다. `pg_dump -Fc` 를 작업 스케줄러로 공유 폴더에 떠서 백업한다
- 같은 KIS 계좌로 두 배포를 동시에 켜지 않는다
