FROM python:3.12-slim-bookworm@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    MALLOC_ARENA_MAX=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    EMBEDDING_PROVIDER=static \
    EMBEDDING_CACHE_DIR=/opt/topmed-models

WORKDIR /app

RUN groupadd --system topmed \
    && useradd --system --gid topmed --home-dir /app topmed

COPY backend /app/backend
COPY data /app/data
COPY evals /app/evals
COPY knowledge_base /app/knowledge_base
COPY demo /app/demo
COPY scripts /app/scripts
COPY requirements-data.txt /app/requirements-data.txt

RUN python -m pip install --requirement requirements-data.txt \
    && python -m pip install ./backend \
    && python -m compileall -q backend/app \
    && python -c "from app.config import Settings; from app.embeddings import ensure_configured_model_artifacts; ensure_configured_model_artifacts(Settings(_env_file=None))" \
    && chown -R topmed:topmed /app /opt/topmed-models

USER topmed

EXPOSE 10000

CMD ["bash", "scripts/start_production.sh"]
