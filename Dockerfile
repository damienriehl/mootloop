# MootLoop public demo server — read-only, synthetic matter only.
# The demo vault is baked at BUILD time (FakeLLMProvider; zero LLM calls, zero
# secrets, no network). At runtime the container serves that vault read-only.
# HARD RULE: this image never hosts real matter data.

FROM python:3.12-slim

LABEL org.opencontainers.image.title="mootloop-demo" \
      org.opencontainers.image.description="MootLoop public demo — agentic law firm arc on a synthetic matter" \
      org.opencontainers.image.source="https://github.com/damienriehl/mootloop" \
      org.opencontainers.image.licenses="MIT"

# System deps: curl for the healthcheck, pandoc so the baked export renders DOCX.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    pandoc \
    && rm -rf /var/lib/apt/lists/*

# uv via the official binary (pip-installing uv has been flaky on constrained
# build networks — alea-intake pattern).
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer first so it caches across source-only changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra web --no-install-project

# The repo proper (fixtures/, personas/, config/, rubrics/ are all bake inputs).
COPY . .
RUN uv sync --frozen --no-dev --extra web

ENV PATH="/app/.venv/bin:$PATH" \
    MOOTLOOP_DEMO_VAULT=/app/demo-vault

# Bake the demo vault at build time: full pipeline on the synthetic matter,
# deterministic, offline. (.dockerignore excludes .git, so the vault-boundary
# preflight's repo check does not apply inside the image.)
RUN mootloop web bake /app/demo-vault

# Non-root runtime user; the vault is served read-only.
RUN useradd -m -r appuser && chown -R appuser:appuser /app/demo-vault
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Most PaaS platforms inject PORT; fall back to 8000 for local use.
CMD ["sh", "-c", "uvicorn mootloop.web.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
