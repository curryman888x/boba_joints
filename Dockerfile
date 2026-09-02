# Image for the Streamlit dashboard service (see docker-compose.yml).
# The pipeline itself still runs on the host; this container only reads the DB.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# dependency layer (cached unless pyproject/uv.lock change)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --group dashboard

COPY boba ./boba
COPY dashboard ./dashboard
RUN uv sync --frozen --group dashboard

EXPOSE 8501
CMD ["uv", "run", "--group", "dashboard", "streamlit", "run", "dashboard/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
