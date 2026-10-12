FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/requirements.lock.txt ./
COPY backend/app ./app
RUN pip install --no-cache-dir -r requirements.lock.txt && pip install --no-cache-dir --no-deps .
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY skills /app/skills
COPY evals /app/evals
COPY scripts /app/scripts
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown -R appuser:appuser /data /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
