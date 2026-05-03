# syntax=docker/dockerfile:1.7
FROM python:3.14-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir --target=/install .


FROM python:3.14-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 bot

COPY --from=builder /install /usr/local/lib/python3.14/site-packages
COPY src/ ./src/
COPY templates/ ./templates/

RUN mkdir -p /app/data/output && chown -R bot:bot /app

USER bot

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/usr/local/lib/python3.14/site-packages

ENTRYPOINT ["python", "-m", "pravohelp.bot"]
