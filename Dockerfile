# syntax=docker/dockerfile:1

# 1. Build the web UI.
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

# 2. The Python app serves both the API and the built UI.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# tzdata for local times in the agent's shell; ripgrep for searching the course library.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ripgrep && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install . uv  # uv: prime-agent builds its Python kernel with it
COPY --from=web /web/dist ./static

# docker-compose.yml runs both services as your own user (PUID), so ./data stays yours.
RUN useradd --system --uid 10001 app && mkdir /data && chown app /data
USER app

ENV CONFIG_PATH=/config/config.yaml \
    DATA_DIR=/data \
    STATIC_DIR=/app/static \
    HOST=0.0.0.0
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host \"$HOST\" --port 8000 --proxy-headers"]
