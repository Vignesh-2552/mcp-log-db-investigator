FROM python:3.13-slim

# Pinned for reproducible builds; Dependabot (docker ecosystem) proposes bumps.
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /bin/

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

# Run as an unprivileged user; the app only reads /app at runtime.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER app

CMD ["investigation-server"]
