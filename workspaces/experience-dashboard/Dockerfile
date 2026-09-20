FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_PUBLIC_API_MODE=live
# 화면별 503 복구를 검증하는 QA 빌드에서만 1 로 준다. 기본은 꺼 두어 프로덕션 번들에
# 장애 주입 스위치를 남기지 않는다.
ARG QA_FAULT_INJECTION=0
ENV NEXT_PUBLIC_QA_FAULT_INJECTION=${QA_FAULT_INJECTION}
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY . .
RUN npm run typecheck && npm run lint && npm test && npm run build

FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS runtime
WORKDIR /app
RUN apk add --no-cache libcrypto3=3.5.8-r0 libssl3=3.5.8-r0 \
    && rm -rf /usr/local/lib/node_modules/npm \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_PUBLIC_API_MODE=live
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
# 팀 B 수신 프리뷰. 이것이 없으면 /healthz 가 teamBPreview=UNAVAILABLE 로 503 을 준다.
# 개발용 compose 는 이 경로에 볼륨을 덮어써 preview 잡의 결과를 쓴다.
COPY --chown=node:node seed/team-b/005930.KS.json /artifacts/team-b/005930.KS.json
USER 1000:1000
EXPOSE 3000
CMD ["node", "server.js"]
