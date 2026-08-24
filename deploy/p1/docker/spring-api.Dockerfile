# syntax=docker/dockerfile:1.10@sha256:865e5dd094beca432e8c0a1d5e1c465db5f998dca4e439981029b3b81fb39ed5
FROM --platform=linux/amd64 eclipse-temurin:25-jdk-jammy@sha256:89565961a318534f01c971c7b1d030e60713c66995b887c94010cef938dbc53e AS build
WORKDIR /source
COPY workspaces/decision-platform/spring-api/gradlew workspaces/decision-platform/spring-api/gradlew
COPY workspaces/decision-platform/spring-api/gradle workspaces/decision-platform/spring-api/gradle
COPY workspaces/decision-platform/spring-api/settings.gradle.kts workspaces/decision-platform/spring-api/build.gradle.kts workspaces/decision-platform/spring-api/
COPY contracts contracts
COPY workspaces/decision-platform/spring-api/src workspaces/decision-platform/spring-api/src
RUN --mount=type=cache,target=/root/.gradle \
    workspaces/decision-platform/spring-api/gradlew \
      -p workspaces/decision-platform/spring-api --no-daemon bootJar

FROM --platform=linux/amd64 cgr.dev/chainguard/wolfi-base:latest@sha256:a31344ab2cb8618db84f535eec56f76f6178b142cb92cb2e48676cc2dcebea72
ARG SOURCE_REVISION=unknown
ARG RELEASE_VERSION=dev
LABEL org.opencontainers.image.title="Capstone Decision Platform API" \
      org.opencontainers.image.description="P1_OFFLINE_DEMO Spring API" \
      org.opencontainers.image.source="https://github.com/robinhood0107/Capstone-AI-Trading-Coach" \
      org.opencontainers.image.revision="$SOURCE_REVISION" \
      org.opencontainers.image.version="$RELEASE_VERSION" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
RUN apk add --no-cache openjdk-25-jre=25.0.4.1-r0
ENV JAVA_HOME=/usr/lib/jvm/java-25-openjdk \
    PATH=/usr/lib/jvm/java-25-openjdk/bin:$PATH
WORKDIR /app
COPY --from=build --chown=65532:65532 /source/workspaces/decision-platform/spring-api/build/libs/decision-platform-api-0.0.1-SNAPSHOT.jar /app/app.jar
COPY --chown=65532:65532 deploy/p1/docker/secret-entrypoint.sh /usr/local/bin/p1-secret-entrypoint
RUN chmod 0555 /usr/local/bin/p1-secret-entrypoint
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/p1-secret-entrypoint", "spring"]
CMD ["java", "-XX:MaxRAMPercentage=75", "-Djava.io.tmpdir=/tmp", "-jar", "/app/app.jar"]
