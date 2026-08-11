FROM node:24-bookworm-slim AS frontend-build
WORKDIR /app
COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package.json
RUN npm ci
COPY frontend frontend
RUN mkdir -p backend/app/static && npm run build

FROM python:3.12-slim AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS runtime
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080
WORKDIR /app
RUN groupadd --system trialopt && useradd --system --gid trialopt --home-dir /app trialopt
COPY --from=python-build /app/.venv /app/.venv
COPY backend backend
COPY --from=frontend-build /app/backend/app/static backend/app/static
COPY config config
COPY data/demo data/demo
COPY data/seeds data/seeds
COPY prompts prompts
COPY schemas schemas
USER trialopt
EXPOSE 8080
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]

