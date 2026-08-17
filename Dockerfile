FROM python:3.13-slim AS builder

ENV POETRY_VERSION=2.4.1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_NO_INTERACTION=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN pip install "poetry==${POETRY_VERSION}"

# Copied first so the dependency layer is cached until the lockfile changes.
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root


FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Runs as an unprivileged user: a payment service should never be root.
RUN useradd --create-home --uid 1000 gateway

COPY --from=builder /app/.venv /app/.venv
COPY payment_gateway_api ./payment_gateway_api

USER gateway

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')"

# uvicorn is invoked directly rather than through main.py, which enables the
# auto-reloader intended for local development only.
CMD ["uvicorn", "payment_gateway_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
