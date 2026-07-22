FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 10001 cam \
    && useradd --uid 10001 --gid cam --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin cam

WORKDIR /app
COPY --chown=cam:cam apps/api/requirements.txt /app/apps/api/requirements.txt
RUN python -m pip install --requirement /app/apps/api/requirements.txt

COPY --chown=cam:cam apps/api /app/apps/api
COPY --chown=cam:cam apps/worker /app/apps/worker
COPY --chown=cam:cam database /app/database
COPY --chown=cam:cam deploy/tools /app/deploy/tools

WORKDIR /app/apps/api
USER 10001:10001

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/live', timeout=3).read()"]

CMD ["python", "/app/apps/worker/main.py"]
