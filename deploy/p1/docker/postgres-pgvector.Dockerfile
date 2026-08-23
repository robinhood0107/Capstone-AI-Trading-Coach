# syntax=docker/dockerfile:1.7

ARG POSTGRES_IMAGE=postgres:16.14-alpine3.24@sha256:7a396fd264a2067788b6551122b50f162bf6136312c7fc9d74381cb92c648382

FROM ${POSTGRES_IMAGE} AS pgvector-build

ARG PGVECTOR_VERSION=0.8.6
ARG PGVECTOR_SHA256=10bf9938906e5d643bbc4a7eea104b6f57ba4898e5b76b20e60484ea1d5a7f8f

RUN set -eux; \
    apk add --no-cache --virtual .pgvector-build-deps \
        build-base \
        clang21 \
        llvm21-dev; \
    wget -O /tmp/pgvector.tar.gz \
        "https://github.com/pgvector/pgvector/archive/refs/tags/v${PGVECTOR_VERSION}.tar.gz"; \
    echo "${PGVECTOR_SHA256}  /tmp/pgvector.tar.gz" | sha256sum -c -; \
    mkdir /tmp/pgvector; \
    tar -xzf /tmp/pgvector.tar.gz --strip-components=1 -C /tmp/pgvector; \
    make -C /tmp/pgvector clean; \
    make -C /tmp/pgvector OPTFLAGS=""; \
    make -C /tmp/pgvector install; \
    install -d -m 0755 /usr/local/share/doc/pgvector; \
    install -m 0644 /tmp/pgvector/LICENSE /usr/local/share/doc/pgvector/LICENSE; \
    apk del .pgvector-build-deps; \
    rm -rf /tmp/pgvector /tmp/pgvector.tar.gz

FROM ${POSTGRES_IMAGE}

ARG SOURCE_REVISION=unknown
ARG RELEASE_VERSION=0.0.0

LABEL org.opencontainers.image.title="Capstone P1 PostgreSQL with pgvector" \
      org.opencontainers.image.description="PostgreSQL 16 runtime with the pinned pgvector extension for P1_OFFLINE_DEMO" \
      org.opencontainers.image.source="https://github.com/robinhood0107/Capstone-AI-Trading-Coach" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.version="${RELEASE_VERSION}" \
      org.opencontainers.image.licenses="PostgreSQL"

COPY --from=pgvector-build /usr/local/lib/postgresql/vector.so /usr/local/lib/postgresql/vector.so
COPY --from=pgvector-build /usr/local/lib/postgresql/bitcode/vector/ /usr/local/lib/postgresql/bitcode/vector/
COPY --from=pgvector-build /usr/local/lib/postgresql/bitcode/vector.index.bc /usr/local/lib/postgresql/bitcode/vector.index.bc
COPY --from=pgvector-build /usr/local/share/postgresql/extension/vector* /usr/local/share/postgresql/extension/
COPY --from=pgvector-build /usr/local/share/doc/pgvector/ /usr/local/share/doc/pgvector/

RUN rm -f /usr/local/bin/gosu

USER 70:70
