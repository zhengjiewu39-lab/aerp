FROM python:3.11-slim

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./backend/
COPY backend/aerp ./backend/aerp
RUN pip install --no-cache-dir ./backend

ENV AERP_HOST=0.0.0.0
ENV AERP_PORT=8000
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "aerp.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "backend"]
