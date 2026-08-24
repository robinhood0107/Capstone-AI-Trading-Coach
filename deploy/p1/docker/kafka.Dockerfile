# syntax=docker/dockerfile:1.10@sha256:865e5dd094beca432e8c0a1d5e1c465db5f998dca4e439981029b3b81fb39ed5
FROM apache/kafka:4.3.1@sha256:ccd1314e47ec76909e01f86308b4dcf2064f19f7c89759234322314b0e319e26

ARG SOURCE_REVISION=unknown
ARG RELEASE_VERSION=dev
LABEL org.opencontainers.image.title="Capstone Kafka Broker" \
      org.opencontainers.image.description="SASL/ACL Kafka 4.3.1 with pinned security patches" \
      org.opencontainers.image.source="https://github.com/robinhood0107/Capstone-AI-Trading-Coach" \
      org.opencontainers.image.revision="$SOURCE_REVISION" \
      org.opencontainers.image.version="$RELEASE_VERSION" \
      org.opencontainers.image.licenses="Apache-2.0"

USER 0:0
ADD --checksum=sha256:cbd8ae7af319512615ae546970112667136ac88e6c18dcb2059bd0dfceb877fb https://dl-cdn.alpinelinux.org/alpine/v3.24/main/x86_64/libexpat-2.8.2-r0.apk /tmp/libexpat.apk
ADD --checksum=sha256:134d653aeeb4ada32746fbc3e2a7ae4201f584725de9263e4f32503658f3ddd8 https://dl-cdn.alpinelinux.org/alpine/v3.24/main/x86_64/p11-kit-0.26.2-r0.apk /tmp/p11-kit.apk
ADD --checksum=sha256:f79c696b960d974832723215431b93e21ecb29b2449892b3fd6587713f1f2f50 https://dl-cdn.alpinelinux.org/alpine/v3.24/main/x86_64/p11-kit-trust-0.26.2-r0.apk /tmp/p11-kit-trust.apk
RUN apk add --no-cache --no-network --allow-untrusted \
      /tmp/libexpat.apk /tmp/p11-kit.apk /tmp/p11-kit-trust.apk \
    && rm -f /tmp/libexpat.apk /tmp/p11-kit.apk /tmp/p11-kit-trust.apk \
    && rm -f \
      /opt/kafka/libs/jackson-core-2.21.2.jar \
      /opt/kafka/libs/jackson-databind-2.21.2.jar \
      /opt/kafka/libs/jetty-security-12.0.34.jar \
      /opt/kafka/libs/jline-3.30.4.jar
ADD --checksum=sha256:b64b5874162b503a0e58a8f7758266e8dd9f91bf49e3a59ae0b5f47589a231b7 https://repo1.maven.org/maven2/com/fasterxml/jackson/core/jackson-core/2.21.5/jackson-core-2.21.5.jar /opt/kafka/libs/jackson-core-2.21.5.jar
ADD --checksum=sha256:507418c0fafd38b2b2cfb704521630da613a8e4cc8381195a6f418017883e2c0 https://repo1.maven.org/maven2/com/fasterxml/jackson/core/jackson-databind/2.21.5/jackson-databind-2.21.5.jar /opt/kafka/libs/jackson-databind-2.21.5.jar
ADD --checksum=sha256:e6fb70974291312c58ac84d8bf28c909d1800d1b3104020e7cd4db77391f9fb2 https://repo1.maven.org/maven2/org/eclipse/jetty/jetty-security/12.0.36/jetty-security-12.0.36.jar /opt/kafka/libs/jetty-security-12.0.36.jar
RUN chown 1000:1000 \
      /opt/kafka/libs/jackson-core-2.21.5.jar \
      /opt/kafka/libs/jackson-databind-2.21.5.jar \
      /opt/kafka/libs/jetty-security-12.0.36.jar

USER 1000:1000
