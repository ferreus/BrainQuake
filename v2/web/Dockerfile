# Multi-stage build: compile the Vite/React SPA, then serve the static
# output with nginx. VITE_API_BASE_URL is baked in at build time (Vite env
# vars are compile-time constants) -- the default below points at the host
# port docker-compose publishes the api service on, so `docker compose up`
# works out of the box. The app's Server Settings dialog can still override
# the base URL at runtime via localStorage (see src/api/serverConfig.ts) if
# the server lives elsewhere.
# node:22-slim (Debian/glibc), not -alpine: the committed package-lock.json
# records glibc-platform optional deps (e.g. @emnapi/*), which `npm ci` on
# musl/Alpine rejects as an out-of-sync lockfile.
FROM node:22-slim AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
