#!/bin/bash
# 실행 중인 FULL 이미지 배포를 NAS(Portainer) 로 옮길 묶음 두 개를 만든다.
#   ~/mars-nas-config.tgz : full-secrets/, full-kek/   (비밀값)
#   ~/mars-nas-db.tgz     : postgres 볼륨 통째 (역할·비밀번호·데이터 그대로)
# 두 파일 모두 비밀값을 담는다. scp 로만 옮기고 옮긴 뒤 지운다.
#
# 사용: MARS_FULL_HOME=<compose·secret 폴더> bash deploy/p1/portainer/nas-bundle.sh
# Ubuntu(WSL) 터미널 안에서 실행한다. PowerShell 에서 wsl -- 로 파이프하면 tar 가 엉뚱한 곳으로 흐른다.
set -euo pipefail
R=${MARS_FULL_HOME:-$HOME/.local/share/mars-full}
VOLUME=${MARS_FULL_DB_VOLUME:-mars-full_postgres-data}
umask 077

[ -d "$R/full-secrets" ] || { echo "full-secrets 가 없습니다: $R" >&2; exit 1; }
docker volume inspect "$VOLUME" >/dev/null

"$R/mars-full.sh" stop              # DB 를 멈춘 상태에서 떠야 일관된 사본이 된다
# full-kek 는 uid 65532 소유라 sudo 로 소유자·권한을 보존해 묶는다.
sudo tar czpf ~/mars-nas-config.tgz -C "$R" full-secrets full-kek
# Docker Desktop 에서는 -v "$HOME" 가 WSL 홈이 아닌 Docker VM 경로로 붙는다. stdout 으로 받는다.
docker run --rm -v "$VOLUME":/from:ro alpine tar czpf - -C /from . > ~/mars-nas-db.tgz
# grep -q 는 찾자마자 끝나 tar 를 SIGPIPE 로 끊고 pipefail 이 그걸 실패로 읽는다. 개수로 판단한다.
[ "$(tar tzf ~/mars-nas-db.tgz | grep -c 'PG_VERSION$')" -gt 0 ] || { echo "DB 묶음이 비었습니다" >&2; exit 1; }
sudo chown "$(id -u):$(id -g)" ~/mars-nas-config.tgz
chmod 600 ~/mars-nas-config.tgz ~/mars-nas-db.tgz
ls -lh ~/mars-nas-config.tgz ~/mars-nas-db.tgz
echo "PC 스택은 멈춘 상태입니다. NAS 가 뜨기 전까지 다시 켜려면: $R/mars-full.sh start"
