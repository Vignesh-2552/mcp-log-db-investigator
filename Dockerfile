FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (separate layer, cached unless deps change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then install the project itself
COPY src ./src
COPY README.md LICENSE ./
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Listen on all interfaces inside the container, on the port EXPOSEd below.
# Publish it on loopback on the host (`-p 127.0.0.1:8000:8000`): the HTTP
# transport has no built-in auth.
ENV SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8000

EXPOSE 8000

CMD ["investigation-server"]
