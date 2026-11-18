FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROPERTY_DATA_MODE=live \
    CHAT_MODE=live

WORKDIR /app
ARG PROPERTY_RETRIEVAL_MODE=jsonl
ARG PROPERTY_CORPUS_BACKEND=jsonl
ENV HF_HOME=/opt/huggingface
COPY requirements.txt requirements-qdrant.txt requirements-vector-store.txt ./
COPY requirements-import.txt ./
COPY app/search/property_vectors.py ./app/search/
RUN if [ "$PROPERTY_RETRIEVAL_MODE" = "hybrid" ]; then \
        pip install --no-cache-dir -r requirements-qdrant.txt && \
        python -c 'from app.search.property_vectors import Embedder; Embedder()' && \
        chmod -R a+rX "$HF_HOME"; \
    elif [ "$PROPERTY_RETRIEVAL_MODE" = "nvidia" ]; then \
        pip install --no-cache-dir -r requirements.txt -r requirements-vector-store.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi
RUN if [ "$PROPERTY_CORPUS_BACKEND" = "postgres" ]; then \
        pip install --no-cache-dir -r requirements-import.txt; \
    fi

COPY main.py ./
COPY app/ ./app/
COPY static/ ./static/
COPY deploy_data/property_live.jsonl ./snapshots/property_live.jsonl
COPY deploy_data/property_live_v2.jsonl ./snapshots/property_live_v2.jsonl

USER 10001:10001
EXPOSE 10000
CMD ["sh", "-c", "exec python -m uvicorn main:app --host 0.0.0.0 --port \"${PORT:-10000}\""]
