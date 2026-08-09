# syntax=docker/dockerfile:1.7

FROM python:3.12.8-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    libvips42 \
    libvips-dev \
    build-essential \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 10001 cam \
    && useradd --uid 10001 --gid cam --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin cam

WORKDIR /app
COPY --chown=cam:cam apps/api/requirements.txt /app/apps/api/requirements.txt
RUN python -m pip install --requirement /app/apps/api/requirements.txt

# Copy only backend runtime and migration inputs; local databases, tests,
# frontend files, environment files and deployment secrets stay outside.
COPY --chown=cam:cam apps/api/alembic.ini /app/apps/api/alembic.ini
COPY --chown=cam:cam apps/api/app /app/apps/api/app
COPY --chown=cam:cam apps/worker /app/apps/worker
COPY --chown=cam:cam apps/inventory_worker /app/apps/inventory_worker
COPY --chown=cam:cam database/migrations /app/database/migrations

WORKDIR /app/apps/api
USER 10001:10001
STOPSIGNAL SIGTERM
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
