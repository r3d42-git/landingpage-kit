FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/index.html web/vite.config.js ./
COPY web/src/ ./src/
COPY web/public/ ./public/
RUN npm run build

FROM python:3.13-slim-bookworm
LABEL org.opencontainers.image.title="Landingpage Kit" \
      org.opencontainers.image.source="https://github.com/r3d42-git/landingpage-kit" \
      org.opencontainers.image.licenses="GPL-3.0-only"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WEB_DIR=/app/web/dist DATA_DIR=/data SEED_DIR=/data/seed
WORKDIR /app
COPY tools/requirements-configure.txt ./tools/
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r tools/requirements-configure.txt \
    && install -d -m 0700 -o 10001 -g 10001 /data /data/config /data/seed
COPY LICENSE THIRD_PARTY_NOTICES.txt ./
COPY server.py integrations.py public_youtube.py localization.py ./
COPY tools/manage.py tools/configure.py tools/cli_language.py ./tools/
COPY deploy/start.py ./deploy/
RUN ln -s tools/manage.py /app/manage.py
COPY --from=frontend /build/dist/ ./web/dist/
USER 10001:10001
EXPOSE 8080 8443
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3).read()"]
CMD ["python", "deploy/start.py"]
