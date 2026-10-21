FROM python:3.12-slim

# git lets the indexer honour .gitignore (it lists files with `git ls-files`)
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    # Mounted repos belong to your host user, not root: let git read them anyway
    && git config --system --add safe.directory '*'

WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend
RUN pip install --no-cache-dir .

ENV CODELENS_DB_PATH=/data
EXPOSE 8000
# Bind to all interfaces inside the container; docker-compose publishes it on localhost only
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
