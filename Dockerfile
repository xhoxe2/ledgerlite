FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY wallet ./wallet
COPY provider_mock ./provider_mock

RUN useradd --system app
USER app
EXPOSE 8000 8001
CMD ["uvicorn", "--factory", "wallet.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
