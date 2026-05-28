# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir --upgrade pip build

# Security: COPY uses an explicit allowlist of build inputs (no `COPY . .`).
# Sensitive artefacts (cookies.json, cookies.txt, cookie.txt, *.env, secrets.*,
# MagicMock/, exports/, data/, dist/, *.mcpb) are additionally excluded by
# `.dockerignore` at the repo root. See SECURITY.md for the full secret list.
COPY pyproject.toml requirements.txt ./
COPY instagram_mcp/ instagram_mcp/
COPY README.md LICENSE ./

RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -e .

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="instagram-mcp"
LABEL org.opencontainers.image.description="World-class Instagram intelligence MCP server"
LABEL org.opencontainers.image.source="https://github.com/mpython77/instagram-mcp"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Copy installed packages and the app from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/instagram-mcp /usr/local/bin/instagram-mcp
COPY --from=builder /build/instagram_mcp /app/instagram_mcp

# Create non-root user for security
RUN useradd -m -u 1000 mcp && \
    mkdir -p /app/exports /app/data && \
    chown -R mcp:mcp /app

USER mcp

# Default environment
ENV INSTAGRAM_MCP_EXPORT_DIR=/app/exports
ENV INSTAGRAM_MCP_TRANSPORT=stdio

# Volumes for persistent data
VOLUME ["/app/exports", "/app/data"]

# Health check (only useful with HTTP transport)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import instagram_mcp; print('ok')" || exit 1

# Default: STDIO transport (for Claude Desktop / Claude Code)
# Override with: docker run -e INSTAGRAM_MCP_TRANSPORT=http -p 8000:8000 ...
ENTRYPOINT ["instagram-mcp"]
