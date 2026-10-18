# syntax=docker/dockerfile:1.7
# =============================================================================
# UASI v5 Enterprise Production — imagen multi-stage
#   Etapa 1 (builder): toolchain de compilación + venv con todas las dependencias
#   Etapa 2 (runtime): python:3.11-slim, solo el venv y el código, usuario no-root
# Construir (desde la raíz del repo):  docker build -f docker/Dockerfile -t uasi-v5:5.2.1 .
# =============================================================================

ARG PYTHON_VERSION=3.11

# -----------------------------------------------------------------------------
# Etapa 1: builder
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential gcc g++ libffi-dev libssl-dev \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build
COPY requirements.txt .
# redis: cliente necesario para RATE_LIMIT_STORAGE_URI=redis://... (multi-worker)
RUN pip install --upgrade pip setuptools wheel \
 && pip install -r requirements.txt \
 && pip install redis==8.1.0

# -----------------------------------------------------------------------------
# Etapa 2: runtime
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

LABEL org.opencontainers.image.title="UASI v5 Enterprise Production" \
      org.opencontainers.image.version="5.2.1-enterprise" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.description="Agentes de ML gobernados con ledger anclado en Bitcoin vía OpenTimestamps"

# libgomp1: runtime OpenMP que necesita scikit-learn; tini: init mínimo (señales/zombis)
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ARG UID=10001
ARG GID=10001
RUN groupadd --system --gid ${GID} uasi \
 && useradd --system --uid ${UID} --gid uasi --home-dir /app --shell /usr/sbin/nologin uasi

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UASI_ENV=production \
    PATH_WORKSPACE=/data \
    PORT=8000 \
    GUNICORN_WORKERS=4

WORKDIR /app
COPY --chown=uasi:uasi src/ /app/src/

RUN mkdir -p /data && chown -R uasi:uasi /data
VOLUME ["/data"]

USER uasi
EXPOSE 8000

# Health check sin curl (no incluido en slim): usa urllib de la stdlib
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request,sys; r=urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/v5/health', timeout=4); sys.exit(0 if r.status==200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
# La aplicación es src/uasi_v5/main.py → módulo uasi_v5.main, objeto `app`
CMD ["sh", "-c", "exec gunicorn uasi_v5.main:app -k uvicorn.workers.UvicornWorker -w ${GUNICORN_WORKERS} -b 0.0.0.0:${PORT} --timeout 300 --graceful-timeout 30 --access-logfile -"]
