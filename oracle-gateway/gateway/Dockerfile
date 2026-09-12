FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so code edits do not re-resolve the wheel set.
COPY pyproject.toml README.md ./
COPY oracle_gateway ./oracle_gateway
RUN pip install --no-cache-dir .

EXPOSE 8080
CMD ["uvicorn", "oracle_gateway.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
