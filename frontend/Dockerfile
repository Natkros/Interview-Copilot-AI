# InterviewOS frontend - Next.js standalone build
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
# Public, non-secret build-time setting: where the browser opens the live WebSocket.
# Leave empty to use the page's own origin (recommended behind the nginx proxy).
ARG NEXT_PUBLIC_WS_URL=""
# Where the /api/* rewrite forwards to. Rewrites are compiled into the build
# manifest, so this is a build argument (behind nginx, /api is routed directly).
ARG BACKEND_URL="http://backend:8000"
ENV NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL \
    BACKEND_URL=$BACKEND_URL \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -S app && adduser -S -G app -u 10001 app
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
COPY --from=build --chown=app:app /app/public ./public
USER app
EXPOSE 3000
CMD ["node", "server.js"]
