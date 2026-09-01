# Two stages: the compiler and the headers that psycopg needs to build stay in
# the first one, and the image that ships carries the installed packages
# without the toolchain that produced them.
#
# INSTALL_DEV builds an image with the test and lint extras, for running the
# suite inside a container. It is off by default because a deployment has no
# use for pytest and every megabyte is transferred on every release.

FROM python:3.12-slim AS build

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Installed into a virtualenv so the whole dependency tree can be copied to the
# runtime stage as one directory.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

ARG INSTALL_DEV=false
COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip \
    && if [ "$INSTALL_DEV" = "true" ]; then pip install -e ".[dev]"; else pip install -e "."; fi


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# libpq for psycopg at runtime, curl for the healthcheck. No compiler.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/venv /opt/venv
COPY . .

# Run as a non-root user. A container that does not need root should not have it.
RUN useradd --create-home --uid 10001 autointel && chown -R autointel:autointel /app
USER autointel

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
