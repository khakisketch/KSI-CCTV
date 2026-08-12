# KSI-CCTV 웹 프론트엔드(Next.js) — 설치형 Docker 이미지(standalone, 비root).
# 빌드: docker build -t ksi-cctv-web ./frontend  (또는 docker compose --profile web build)
# ── deps ───────────────────────────────────────────────────────────────
FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ── builder ────────────────────────────────────────────────────────────
FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY package.json package-lock.json next.config.ts next-env.d.ts next-with-auth-guard.mjs postcss.config.mjs tsconfig.json components.json ./
COPY public/ ./public/
COPY src/ ./src/
COPY docker-entrypoint.sh ./docker-entrypoint.sh
COPY scripts/reset-local-auth.mjs ./scripts/reset-local-auth.mjs
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ── runner ─────────────────────────────────────────────────────────────
FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS runner
ARG OCI_SOURCE=""
ARG OCI_REVISION=""
ARG OCI_VERSION=""
WORKDIR /app
RUN node -e "const [source,revision,version]=process.argv.slice(1); const u=new URL(source); if(u.protocol!=='https:'||u.username||u.password||u.search||u.hash||!/^[0-9a-f]{40}$/.test(revision)||!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(version)) process.exit(1)" "${OCI_SOURCE}" "${OCI_REVISION}" "${OCI_VERSION}"
LABEL org.opencontainers.image.source="${OCI_SOURCE}" \
      org.opencontainers.image.revision="${OCI_REVISION}" \
      org.opencontainers.image.version="${OCI_VERSION}"
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
# 비root 실행
RUN addgroup -g 1001 -S nodejs && adduser -u 1001 -S nextjs -G nodejs && \
    mkdir -p /var/lib/ksi-cctv-web && \
    chown nextjs:nodejs /var/lib/ksi-cctv-web && \
    chmod 0700 /var/lib/ksi-cctv-web
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder /app/docker-entrypoint.sh /usr/local/bin/ksi-cctv-web
COPY --from=builder /app/scripts/reset-local-auth.mjs /usr/local/bin/ksi-cctv-reset-local-auth
RUN chmod 0555 /usr/local/bin/ksi-cctv-web /usr/local/bin/ksi-cctv-reset-local-auth
USER nextjs
EXPOSE 3000
# standalone 서버. 인증 상태는 owner-only KSI_CCTV_WEB_AUTH_DIR volume에만 저장한다.
CMD ["/usr/local/bin/ksi-cctv-web"]
