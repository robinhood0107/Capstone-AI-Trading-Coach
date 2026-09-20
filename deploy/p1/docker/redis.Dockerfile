# syntax=docker/dockerfile:1.10@sha256:865e5dd094beca432e8c0a1d5e1c465db5f998dca4e439981029b3b81fb39ed5

# 공식 redis 에 비밀 로더만 얹는다.
#
# 지금은 compose 가 `secret-entrypoint.sh` 를 호스트에서 바인드해 넣는다. 그러면 이미지만
# 가진 사람은 redis 를 띄울 수 없다. 스크립트 한 장을 굽는 것으로 그 의존이 사라진다.

ARG REDIS_IMAGE=redis:7.2-alpine@sha256:dfa18828cbc07b3ae6a95ec7343f6c214fdee2d836197b4be8e9904420762cd8

FROM ${REDIS_IMAGE}

ARG SOURCE_REVISION=unknown
ARG RELEASE_VERSION=0.0.0

LABEL org.opencontainers.image.title="Capstone P1 Redis" \
      org.opencontainers.image.description="Redis 7.2 with the P1 secret loader entrypoint" \
      org.opencontainers.image.source="https://github.com/robinhood0107/Capstone-AI-Trading-Coach" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.version="${RELEASE_VERSION}" \
      org.opencontainers.image.licenses="BSD-3-Clause"

COPY deploy/p1/docker/secret-entrypoint.sh /usr/local/bin/p1-secret-entrypoint
RUN chmod 0555 /usr/local/bin/p1-secret-entrypoint

USER 999:1000
