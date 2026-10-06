FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt requirements-solver.txt ./
RUN pip install --no-cache-dir -r requirements-solver.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY original_algorithm_overlay/ ./original_algorithm_overlay/
COPY resources/seed/ ./resources/seed/
COPY resources/migrations/ ./resources/migrations/

RUN mkdir -p /app/runtime/db \
    /app/runtime/content \
    /app/runtime/runs \
    /app/runtime/logs \
    /app/runtime/temp \
    /app/resources/tiles

CMD ["python", "-m", "backend", "serve"]
