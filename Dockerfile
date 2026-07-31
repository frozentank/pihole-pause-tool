# ===============================================
# 1. BUILD STAGE - Full toolchain
# ===============================================
FROM python:3.13-slim AS build

# Install system dependencies needed for building
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Configure Python for containerized environments
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_CACHE_DIR=/tmp/uv-cache

# ===============================================
# 2. INSTALL STAGE - Dependencies and app
# ===============================================
FROM build AS install

WORKDIR /app

# Create a fresh virtual environment inside the image
RUN uv venv .venv
ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# Install dependencies (leverage uv's speed and caching)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application files and install the project
COPY . .
RUN uv pip install --no-deps .

# ===============================================
# 3. RUNTIME STAGE - Minimal production image
# ===============================================
FROM python:3.13-slim AS runtime

# Install only runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid 1001 --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy the virtual environment and app files from the install stage
COPY --from=install --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=install --chown=appuser:appuser /app /app

# Set up environment
ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

USER appuser

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
