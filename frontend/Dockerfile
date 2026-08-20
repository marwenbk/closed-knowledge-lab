FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS dependencies

ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

RUN corepack enable && corepack prepare pnpm@10.28.0 --activate

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

FROM dependencies AS builder

ARG BACKEND_HOSTPORT
ARG BACKEND_ORIGIN
ARG NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_WIDGET_ASSISTANT_KEY
ARG TOPMED_FRAME_ANCESTORS
ARG TOPMED_PUBLIC_ORIGIN
ENV BACKEND_HOSTPORT=$BACKEND_HOSTPORT
ENV BACKEND_ORIGIN=$BACKEND_ORIGIN
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_WIDGET_ASSISTANT_KEY=$NEXT_PUBLIC_WIDGET_ASSISTANT_KEY
ENV TOPMED_FRAME_ANCESTORS=$TOPMED_FRAME_ANCESTORS
ENV TOPMED_PUBLIC_ORIGIN=$TOPMED_PUBLIC_ORIGIN

COPY frontend ./
RUN pnpm build

FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS runner

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV HOSTNAME=0.0.0.0
ENV PORT=10000
WORKDIR /app

RUN groupadd --system topmed && useradd --system --gid topmed topmed

COPY --from=builder --chown=topmed:topmed /app/public ./public
COPY --from=builder --chown=topmed:topmed /app/.next/standalone ./
COPY --from=builder --chown=topmed:topmed /app/.next/static ./.next/static

USER topmed

EXPOSE 10000

CMD ["node", "server.js"]
