FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend
RUN pip install --no-cache-dir .
ENV CODELENS_DB_PATH=/data
EXPOSE 8000
# Bind to all interfaces inside the container; docker-compose publishes it on localhost only
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
