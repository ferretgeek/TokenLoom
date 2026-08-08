FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system --gid 10001 tokenloom \
    && useradd --system --uid 10001 --gid tokenloom --home-dir /nonexistent --shell /usr/sbin/nologin tokenloom \
    && install -d -m 0750 -o tokenloom -g tokenloom /var/lib/token-loom /var/lib/token-loom/imports

COPY requirements.txt ./
RUN python -m pip install --requirement requirements.txt

COPY --chown=tokenloom:tokenloom app ./app
USER tokenloom:tokenloom

EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3).read()"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "1", "--no-access-log"]
