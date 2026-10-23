# Imagen del frontend web (Angular + nginx que proxya el API al scraper).
# Uso: docker compose build web  ·  docker compose up web
# El API se proxya mismo-origen: /products*, /health, /ready, /scrape →
# http://scraper:8000 (ver docker/nginx.conf).

# ---- Stage 1: build ----
FROM node:25-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm install --legacy-peer-deps
COPY . .
RUN npm run build -- --configuration production

# ---- Stage 2: runtime ----
FROM nginx:1.27-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/preciolocal-web/browser /usr/share/nginx/html

EXPOSE 80

# `127.0.0.1` y no `localhost`: en nginx:alpine `localhost` resuelve a ::1
# primero y el busybox-wget no hace fallback a IPv4, dejando el contenedor
# permanentemente unhealthy aunque nginx esté sirviendo bien.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1