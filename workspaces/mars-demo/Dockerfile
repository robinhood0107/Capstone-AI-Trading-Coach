# Build context: repository root. This image contains one DEMO web/Agent service.
FROM node:22-bookworm-slim AS deps
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3=3.11.2-1+b1 make=4.3-4.1 g++=4:12.2.0-3 \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHON=/usr/bin/python3
WORKDIR /repo/workspaces/mars-demo
COPY workspaces/mars-demo/package.json workspaces/mars-demo/package-lock.json ./
RUN npm ci

FROM deps AS build
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /repo
COPY workspaces/mars-demo ./workspaces/mars-demo
COPY workspaces/experience-dashboard/tailwind.config.ts ./workspaces/experience-dashboard/tailwind.config.ts
COPY workspaces/experience-dashboard/src/app/globals.css ./workspaces/experience-dashboard/src/app/globals.css
COPY workspaces/experience-dashboard/src/app/page.tsx ./workspaces/experience-dashboard/src/app/page.tsx
COPY workspaces/experience-dashboard/src/app/principles ./workspaces/experience-dashboard/src/app/principles
COPY workspaces/experience-dashboard/src/app/strategy ./workspaces/experience-dashboard/src/app/strategy
COPY workspaces/experience-dashboard/src/app/model-evaluation ./workspaces/experience-dashboard/src/app/model-evaluation
COPY workspaces/experience-dashboard/src/app/backtest ./workspaces/experience-dashboard/src/app/backtest
COPY workspaces/experience-dashboard/src/app/automation ./workspaces/experience-dashboard/src/app/automation
COPY workspaces/experience-dashboard/src/app/order-review ./workspaces/experience-dashboard/src/app/order-review
COPY workspaces/experience-dashboard/src/app/rag ./workspaces/experience-dashboard/src/app/rag
COPY workspaces/experience-dashboard/src/app/journal ./workspaces/experience-dashboard/src/app/journal
COPY workspaces/experience-dashboard/src/app/report ./workspaces/experience-dashboard/src/app/report
COPY workspaces/experience-dashboard/src/shared/ui ./workspaces/experience-dashboard/src/shared/ui
COPY workspaces/experience-dashboard/src/shared/lib ./workspaces/experience-dashboard/src/shared/lib
COPY workspaces/experience-dashboard/src/shared/api/endpoints.ts ./workspaces/experience-dashboard/src/shared/api/endpoints.ts
COPY workspaces/experience-dashboard/src/shared/api/wire.ts ./workspaces/experience-dashboard/src/shared/api/wire.ts
COPY workspaces/experience-dashboard/src/shared/api/envelope.ts ./workspaces/experience-dashboard/src/shared/api/envelope.ts
COPY workspaces/experience-dashboard/src/shared/api/latestRun.ts ./workspaces/experience-dashboard/src/shared/api/latestRun.ts
COPY workspaces/experience-dashboard/src/shared/api/nullableRead.ts ./workspaces/experience-dashboard/src/shared/api/nullableRead.ts
COPY workspaces/experience-dashboard/src/features/intro ./workspaces/experience-dashboard/src/features/intro
COPY workspaces/experience-dashboard/src/features/overview ./workspaces/experience-dashboard/src/features/overview
COPY workspaces/experience-dashboard/src/features/principles ./workspaces/experience-dashboard/src/features/principles
COPY workspaces/experience-dashboard/src/features/strategy ./workspaces/experience-dashboard/src/features/strategy
COPY workspaces/experience-dashboard/src/features/model-evaluation ./workspaces/experience-dashboard/src/features/model-evaluation
COPY workspaces/experience-dashboard/src/features/backtest-report ./workspaces/experience-dashboard/src/features/backtest-report
COPY workspaces/experience-dashboard/src/features/automation ./workspaces/experience-dashboard/src/features/automation
COPY workspaces/experience-dashboard/src/features/order-review ./workspaces/experience-dashboard/src/features/order-review
COPY workspaces/experience-dashboard/src/features/rag-source ./workspaces/experience-dashboard/src/features/rag-source
COPY workspaces/experience-dashboard/src/features/journal ./workspaces/experience-dashboard/src/features/journal
COPY workspaces/experience-dashboard/src/features/report ./workspaces/experience-dashboard/src/features/report
COPY workspaces/experience-dashboard/src/features/system ./workspaces/experience-dashboard/src/features/system
COPY workspaces/experience-dashboard/src/features/admin ./workspaces/experience-dashboard/src/features/admin
COPY workspaces/experience-dashboard/public/fonts.css ./workspaces/mars-demo/public/fonts.css
COPY workspaces/experience-dashboard/public/ui-fonts.css ./workspaces/mars-demo/public/ui-fonts.css
COPY workspaces/experience-dashboard/public/fonts ./workspaces/mars-demo/public/fonts
COPY workspaces/experience-dashboard/public/mascot.png ./workspaces/mars-demo/public/mascot.png
ARG SOURCE_REVISION=unknown
ARG RELEASE_VERSION=dev
ENV SOURCE_REVISION=${SOURCE_REVISION}
ENV RELEASE_VERSION=${RELEASE_VERSION}
WORKDIR /repo/workspaces/mars-demo
RUN npm run typecheck && npm test && python3 scripts/validate_fixture.py data/scenario.v1.json && python3 scripts/validate_reports.py data/reports.v1.json data/scenario.v1.json && python3 scripts/validate_signals.py data/signals.v1.json data/scenario.v1.json data/krx-calendar.v1.json && npm run build

FROM node:22-bookworm-slim AS runner
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_OPTIONS=--max-old-space-size=512 \
    HOSTNAME=0.0.0.0 \
    PORT=3000 \
    MARS_DEMO_STATE_DB_PATH=/data/mars-demo-state.sqlite
ARG SOURCE_REVISION=unknown
ARG RELEASE_VERSION=dev
LABEL org.opencontainers.image.title="MARS DEMO" \
      org.opencontainers.image.description="Independent low-resource MARS DEMO" \
      org.opencontainers.image.version=${RELEASE_VERSION} \
      org.opencontainers.image.revision=${SOURCE_REVISION} \
      org.opencontainers.image.source="https://github.com/robinhood0107/Capstone-AI-Trading-Coach"
WORKDIR /repo
COPY --from=build --chown=1000:1000 /repo/workspaces/mars-demo/.next/standalone ./
COPY --from=build --chown=1000:1000 /repo/workspaces/mars-demo/.next/static ./workspaces/mars-demo/.next/static
COPY --from=build --chown=1000:1000 /repo/workspaces/mars-demo/public ./workspaces/mars-demo/public
RUN mkdir -p /data && chown 1000:1000 /data
USER 1000:1000
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/healthz').then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))"]
CMD ["node", "workspaces/mars-demo/server.js"]
