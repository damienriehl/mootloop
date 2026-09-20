# syntax=docker/dockerfile:1.7
# Supply the reviewed, pinned release.tar through the external demo_release context.
FROM python:3.12-slim AS preparation
COPY --from=ghcr.io/astral-sh/uv:0.9.22 /uv /usr/local/bin/uv
WORKDIR /build
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra web --no-install-project
COPY src ./src
COPY config/demos ./config/demos
COPY tools/validate_demo_release.py ./tools/validate_demo_release.py
COPY --from=demo_release /release.tar /tmp/release.tar
ENV PYTHONPATH=/build/src
RUN /opt/venv/bin/python tools/validate_demo_release.py /tmp/release.tar \
    --pin config/demos/release.json --stage /staged/public

FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.title="mootloop-demo" \
      org.opencontainers.image.description="Reviewed, read-only MootLoop demo library" \
      org.opencontainers.image.source="https://github.com/damienriehl/mootloop" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /app
COPY --from=preparation /opt/venv /opt/venv
# Deliberate runtime allowlist: no vault, writer, CLI, provider or preparation modules.
COPY src/mootloop/__init__.py ./src/mootloop/__init__.py
COPY src/mootloop/models/__init__.py src/mootloop/models/common.py src/mootloop/models/demo.py src/mootloop/models/matter.py src/mootloop/models/config.py ./src/mootloop/models/
COPY src/mootloop/web/__init__.py src/mootloop/web/app.py src/mootloop/web/catalog.py ./src/mootloop/web/
COPY src/mootloop/web/static ./src/mootloop/web/static
COPY --from=preparation /staged/public ./public
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
    && chmod -R a+rX /app/public \
    && chmod -R a-w /app/public
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/app/src \
    MOOTLOOP_PUBLIC_ROOT=/app/public PYTHONDONTWRITEBYTECODE=1
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/ready',timeout=8).read()"
CMD ["sh", "-c", "exec uvicorn mootloop.web.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
