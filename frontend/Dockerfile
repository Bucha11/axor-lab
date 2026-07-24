# syntax=docker/dockerfile:1
# The Axor Lab SPA served by nginx, which also reverse-proxies the API to the
# lab-server service and terminates TLS with a Cloudflare Origin Certificate
# (Full-strict). This is the only container Cloudflare talks to.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM nginx:1.27-alpine
# our reverse-proxy + SPA + Cloudflare-only hardening config
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80 443
