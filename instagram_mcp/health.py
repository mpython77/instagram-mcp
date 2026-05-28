"""
Health probe endpoints — liveness and readiness checks.

Provides /healthz (liveness) and /readyz (readiness) HTTP endpoints for
container orchestrators (Kubernetes, ECS, Docker Compose health checks).

Liveness: always returns 200 {"status": "ok"} — confirms the process is alive.
Readiness: evaluates conditional checks (cookies, proxies, state DB) and returns
200 when all pass, 503 when any fail.

The module wraps Starlette imports in try/except; if Starlette is unavailable
(e.g. STDIO transport only), mount_routes becomes a no-op with a WARNING log.

Respects INSTAGRAM_MCP_HEALTH_DISABLED env var to skip route mounting entirely.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

__all__ = [
    "liveness_handler",
    "readiness_handler",
    "mount_routes",
]

logger = logging.getLogger("instagram_mcp.health")


# ── Kill switch ──────────────────────────────────────────────────────────────

def _health_disabled() -> bool:
    """Return True when health endpoints are explicitly disabled."""
    return os.environ.get("INSTAGRAM_MCP_HEALTH_DISABLED", "").lower() in ("1", "true")


# ── Starlette dependency probe ───────────────────────────────────────────────

try:
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    _STARLETTE_AVAILABLE = True
except ImportError:
    _STARLETTE_AVAILABLE = False


# ── Module-level state (set by mount_routes) ─────────────────────────────────

_client: Any = None
_config: Any = None
_state_store: Any = None
_inventory: Optional[list] = None


# ── Handlers ─────────────────────────────────────────────────────────────────

async def liveness_handler(request: Any) -> Any:
    """
    Liveness probe — always returns HTTP 200 with {"status": "ok"}.

    This confirms the process is alive and the event loop is responsive.
    Response target: <100ms.
    """
    if _STARLETTE_AVAILABLE:
        return JSONResponse({"status": "ok"}, status_code=200)
    # Fallback dict for testing without Starlette
    return {"status": "ok"}


async def readiness_handler(request: Any) -> Any:
    """
    Readiness probe — evaluates conditional health checks.

    Checks (only evaluated when relevant):
      1. cookies: True when cookie_manager exists and is_authenticated
         (only checked when at least one auth-tier tool is registered)
      2. proxy_active: True when at least one proxy is in CLOSED state
         (only checked when proxies are configured)
      3. state_db: True when state store is writable
         (only checked when state store is enabled)

    Returns:
      - HTTP 200 with check results JSON when all pass
      - HTTP 503 with check results + failure reasons when any fail

    Response target: <500ms.
    """
    checks: dict[str, bool] = {}
    failures: list[str] = []

    # Check 1: Cookies presence (only when auth-tier tools registered)
    if _inventory and _has_auth_tier_tools():
        cookie_manager = getattr(_client, "cookie_manager", None)
        cookies_ok = bool(
            cookie_manager and getattr(cookie_manager, "is_authenticated", False)
        )
        checks["cookies"] = cookies_ok
        if not cookies_ok:
            failures.append("cookies: no authenticated session available")

    # Check 2: At least one proxy in CLOSED state (only when proxies configured)
    if _config and getattr(_config, "proxy_urls", None):
        proxy_manager = getattr(_client, "proxy_manager", None)
        if proxy_manager:
            try:
                statuses = await proxy_manager.get_all_status()
                # A proxy is considered healthy if is_active=True (CLOSED state)
                proxy_ok = any(
                    getattr(s, "is_active", False) for s in statuses
                )
                checks["proxy_active"] = proxy_ok
                if not proxy_ok:
                    failures.append("proxy_active: no proxy in CLOSED state")
            except Exception as exc:
                checks["proxy_active"] = False
                failures.append(f"proxy_active: check failed ({exc})")
        else:
            checks["proxy_active"] = False
            failures.append("proxy_active: proxy manager not available")

    # Check 3: State DB writable (only when state store enabled)
    if _state_store and getattr(_state_store, "is_enabled", lambda: False)():
        try:
            writable = _state_store.is_writable()
            checks["state_db"] = writable
            if not writable:
                failures.append("state_db: state store is not writable")
        except Exception as exc:
            checks["state_db"] = False
            failures.append(f"state_db: check failed ({exc})")

    # Determine overall status
    ok = all(checks.values()) if checks else True
    status_code = 200 if ok else 503

    body: dict[str, Any] = {"checks": checks}
    if failures:
        body["failures"] = failures

    if _STARLETTE_AVAILABLE:
        return JSONResponse(body, status_code=status_code)
    # Fallback dict for testing without Starlette
    body["_status_code"] = status_code
    return body


# ── Helpers ──────────────────────────────────────────────────────────────────

def _has_auth_tier_tools() -> bool:
    """Check if any registered tool requires authentication."""
    if not _inventory:
        return False
    for descriptor in _inventory:
        auth_tier = getattr(descriptor, "auth_tier", None)
        if auth_tier == "auth":
            return True
    return False


# ── Route mounting ───────────────────────────────────────────────────────────

def mount_routes(
    app: Any,
    *,
    client: Any,
    config: Any,
    state_store: Any,
    inventory: Optional[list] = None,
) -> None:
    """
    Add GET /healthz and GET /readyz routes to the Starlette app.

    Parameters:
        app: The Starlette application instance (from FastMCP's HTTP transport)
        client: The InstagramClient instance
        config: The MCPConfig instance
        state_store: The StateStore instance (or None)
        inventory: The tool inventory list (mcp._instagram_tool_inventory)

    Respects INSTAGRAM_MCP_HEALTH_DISABLED env var — when set, routes are not
    mounted and a DEBUG log is emitted.

    If Starlette is not available, logs a WARNING and returns (no-op).
    """
    global _client, _config, _state_store, _inventory

    # Check if health endpoints are disabled
    if _health_disabled():
        logger.debug("Health endpoints disabled via INSTAGRAM_MCP_HEALTH_DISABLED")
        return

    # Check Starlette availability
    if not _STARLETTE_AVAILABLE:
        logger.warning(
            "Health endpoints unavailable: starlette not installed. "
            "Install with: pip install starlette"
        )
        return

    # Store references for handlers
    _client = client
    _config = config
    _state_store = state_store
    _inventory = inventory

    # Add routes to the Starlette app
    health_routes = [
        Route("/healthz", endpoint=liveness_handler, methods=["GET"]),
        Route("/readyz", endpoint=readiness_handler, methods=["GET"]),
    ]

    # Starlette apps typically have a .routes attribute we can extend
    if hasattr(app, "routes"):
        app.routes.extend(health_routes)
        logger.info("Health endpoints mounted: GET /healthz, GET /readyz")
    else:
        # Fallback: try add_route if available
        try:
            app.add_route("/healthz", liveness_handler, methods=["GET"])
            app.add_route("/readyz", readiness_handler, methods=["GET"])
            logger.info("Health endpoints mounted: GET /healthz, GET /readyz")
        except AttributeError:
            logger.warning(
                "Health endpoints could not be mounted: "
                "app has no 'routes' attribute or 'add_route' method"
            )
