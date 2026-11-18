FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py answers.py openrouter_provider.py property_search.py ./
COPY static/ ./static/
COPY deploy_data/property_pilot.jsonl ./snapshots/property_pilot.jsonl

USER 10001:10001
EXPOSE 10000
CMD ["sh", "-c", "exec python -m uvicorn main:app --host 0.0.0.0 --port \"${PORT:-10000}\""]
