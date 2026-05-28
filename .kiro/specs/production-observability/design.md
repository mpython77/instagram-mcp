# Design Document

## Overview

This feature adds five mostly-independent observability subsystems to `instagram-mcp` — Prometheus metrics, structured JSON logs, idempotency for write operations, persistent runtime state, and OpenTelemetry tracing plus health probes. They share a common backbone: a single Tool_Wrapper that decorates every registered MCP tool at registration time, sets ContextVars, starts a span, runs idempotency checks, calls the tool, emits metrics, and tears everything down in reverse order. Every subsystem is opt-in and degrades to a no-op shim when its third-party dependency is missing. A single `INSTAGRAM_MCP_OBSERVABILITY_DISABLED` env var disables the whole stack.

The design preserves the existing public API surface and imposes zero behaviour change on a v0 user who sets none of the new env vars.

## Architecture

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                          instagram_mcp.__init__                                 │
│  create_mcp_server()                                                            │
│   ├── lifespan startup                                                          │
│   │   ├── log_config.configure_logging()        ← Track 2                       │
│   │   ├── metrics.start_endpoint()              ← Track 1 (HTTP transport)      │
│   │   ├── tracing.configure_tracer()            ← Track 5                       │
│   │   ├── state_store.load() → restore CB/RL/Account states                     │
│   │   ├── idempotency.start_cleanup_loop()      ← Track 3                       │
│   │   ├── state_store.start_flush_loop()        ← Track 4                       │
│   │   └── health.mount_routes(starlette_app)    ← Track 5 (HTTP transport)      │
│   └── lifespan shutdown                                                         │
│       └── reverse order: stop loops, final flush, close DBs, shutdown tracer    │
└────────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                      instagram_mcp.tools.__init__                                │
│  register_tools(mcp, client, config, exporter):                                  │
│    for each ToolDescriptor d returned by submodule registrars:                   │
│       wrap d.handler with _tool_wrapper(d) →                                     │
│         (set ContextVars) → (start span) → (idempotency check) →                 │
│         (call inner) → (record outcome) → (tear down)                            │
└────────────────────────────────────────────────────────────────────────────────┘
                                     │
            ┌────────────────────────┼────────────────────────┐
            ▼                        ▼                        ▼
   ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
   │  metrics.py      │    │ log_config.py    │    │  idempotency.py  │
   │  REGISTRY        │    │  ContextVars     │    │  IdempotencyStore│
   │  Counters/Gauges │    │  JSON formatter  │    │  SQLite store    │
   │  Histograms      │    │  Logging filter  │    │  cleanup loop    │
   └────────┬─────────┘    └────────┬─────────┘    └────────┬─────────┘
            │                       │                       │
            ▼                       ▼                       ▼
   ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
   │  state_store.py  │    │  tracing.py      │    │   health.py      │
   │  StateStore      │    │  Tracer_Provider │    │  /healthz        │
   │  StateSnapshot   │    │  start_span()    │    │  /readyz         │
   │  flush loop      │    │  no-op fallback  │    │  Starlette mount │
   └────────┬─────────┘    └──────────────────┘    └──────────────────┘
            │
            ▼ (load on startup, periodic save, final save on shutdown)
   ┌────────────────────────────────────────────────────────────┐
   │   .state/state.db          .state/idempotency.db            │
   │   ┌───────────────────┐    ┌────────────────────┐           │
   │   │ proxies           │    │ idempotency_keys   │           │
   │   │ rate_limiters     │    │  PRIMARY KEY=key   │           │
   │   │ accounts          │    │  status, result,   │           │
   │   │ metadata          │    │  expires_at        │           │
   │   └───────────────────┘    └────────────────────┘           │
   └────────────────────────────────────────────────────────────┘
```

Touched existing modules:

- `instagram_mcp/__init__.py` — lifespan startup and shutdown integration.
- `instagram_mcp/tools/__init__.py` — Tool_Wrapper inserted into `register_tools` dispatch.
- `instagram_mcp/proxy_manager.py` — `to_snapshot()` / `restore_from_snapshot()` methods plus per-state metric callbacks.
- `instagram_mcp/rate_limiter.py` — `to_snapshot()` / `restore_from_snapshot()` methods plus rps gauge.
- `instagram_mcp/account_pool.py` — same pattern.
- `instagram_mcp/cache.py` — emit `cache_operations_total` counter and optional cache spans.
- `instagram_mcp/client.py` — emit `proxy_requests_total` and `proxy_latency_seconds`; open `http.fetch` and `proxy.retry` nested spans.

## Module designs

### `instagram_mcp/metrics.py` (Track 1)

**Public API**

```python
REGISTRY: prometheus_client.CollectorRegistry          # singleton
TOOL_CALLS: Counter                                    # {tool, toolset, auth_tier, outcome}
TOOL_DURATION: Histogram                               # {tool}, buckets [0.005..30]
PROXY_REQUESTS: Counter                                # {proxy_id, outcome}
PROXY_LATENCY: Histogram                               # {proxy_id}
PROXY_STATE: Gauge                                     # {proxy_id, state}
RATE_LIMITER_RPS: Gauge                                # {scope}
RATE_LIMITER_429S: Counter                             # no labels
CIRCUIT_BREAKER_OPENS: Counter                         # {scope}
CACHE_OPERATIONS: Counter                              # {op, result}
ACCOUNT_POOL_STATE: Gauge                              # {alias, state}

def start_endpoint(host: str, port: int) -> None: ...  # blocks until task scheduled
def stop_endpoint() -> None: ...
def push_to_gateway(url: str, job: str = "instagram_mcp", instance: str | None = None) -> None: ...
def is_enabled() -> bool: ...                          # checks Observability_Kill_Switch + dep
```

**No-op shim strategy** — at module import time:

```python
try:
    from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, generate_latest
    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False

if _PROM_AVAILABLE and not _kill_switch():
    REGISTRY = CollectorRegistry()
    TOOL_CALLS = Counter(...)
    ...
else:
    class _NoOpMetric:
        def labels(self, **kw): return self
        def inc(self, n=1): pass
        def observe(self, v): pass
        def set(self, v): pass
    REGISTRY = None
    TOOL_CALLS = TOOL_DURATION = PROXY_REQUESTS = ... = _NoOpMetric()
```

This way every call site can write `TOOL_CALLS.labels(tool="x", outcome="success").inc()` without guarding.

**Label cardinality budget**

| Metric | Label cardinality (max) |
|---|---|
| `tool_calls_total` | tools (76) × outcomes (4) × tiers (3) ≈ 912 |
| `tool_duration_seconds` | 76 |
| `proxy_requests_total` | proxies (≤50 typical) × outcomes (4) ≈ 200 |
| `proxy_latency_seconds` | 50 |
| `proxy_state` | 50 × 3 states = 150 |
| `account_pool_state` | aliases (≤20) × 4 states = 80 |

Total active series ≤ ~1500, well within Prometheus single-instance budget.

**HTTP `/metrics` endpoint** — when transport is HTTP, mount on FastMCP's underlying Starlette app:

```python
async def _metrics_handler(request):
    body = generate_latest(REGISTRY)
    return Response(body, media_type=CONTENT_TYPE_LATEST)

starlette_app.add_route("/metrics", _metrics_handler, methods=["GET"])
```

When the FastMCP version doesn't expose Starlette, fall back to launching a tiny `aiohttp.web` server on the metrics port.

### `instagram_mcp/log_config.py` (Track 2)

**Public API**

```python
current_correlation_id: ContextVar[str | None]
current_tool_name: ContextVar[str | None]
current_account_alias: ContextVar[str | None]

def configure_logging(level: str | None = None, fmt: str | None = None) -> None: ...
def new_correlation_id() -> str: ...                   # uuid4 hex
class ContextFilter(logging.Filter): ...
class JSONFormatter(logging.Formatter): ...            # pulls from ContextVars
```

**ContextVar set/reset pattern** used by Tool_Wrapper:

```python
tokens = []
tokens.append(current_correlation_id.set(corr_id))
tokens.append(current_tool_name.set(name))
tokens.append(current_account_alias.set(alias))
try:
    return await fn(*args, **kwargs)
finally:
    for tok, var in zip(reversed(tokens), [current_account_alias, current_tool_name, current_correlation_id]):
        var.reset(tok)
```

**JSON formatter reserved keys** — `timestamp` (ISO 8601), `level`, `logger`, `message`, `correlation_id`, `tool`, `account_alias`. Extras passed via `extra={"k": v}` become top-level JSON keys.

**Fallback** — if `python-json-logger` unavailable, install plain text formatter and log a single WARNING. Code uses a hand-rolled JSONFormatter (just `logging.Formatter` subclass calling `json.dumps`); the dep is preferred for performance and edge cases (NaN, datetime).

**Cookie redaction filter** — adds a separate `RedactingFilter(logging.Filter)` that replaces any substring matching `r'Cookie:\s*[^\r\n]+'` or `r'sessionid=[^;\s]+'` in the formatted record with `Cookie: <redacted>`. Applied after Context_Filter.

### `instagram_mcp/idempotency.py` (Track 3)

**Public API**

```python
@dataclass
class IdempotencyEntry:
    key: str
    tool: str
    status: Literal["in_progress", "completed", "error"]
    result_json: str | None
    error_json: str | None
    created_at: int       # epoch seconds
    expires_at: int

class IdempotencyStore:
    def __init__(self, db_path: Path, ttl_seconds: int = 86400): ...
    async def get(self, key: str) -> IdempotencyEntry | None: ...
    async def begin(self, key: str, tool: str) -> bool: ...      # True if newly inserted, False if already in_progress
    async def complete(self, key: str, result_json: str) -> None: ...
    async def fail(self, key: str, error_json: str) -> None: ...
    async def cleanup_expired(self) -> int: ...
    async def close(self) -> None: ...
    def is_enabled(self) -> bool: ...
```

**SQLite schema**

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key         TEXT PRIMARY KEY,
    tool        TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'error')),
    result_json TEXT,
    error_json  TEXT,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_idempotency_expires ON idempotency_keys(expires_at);
CREATE INDEX IF NOT EXISTS idx_idempotency_tool    ON idempotency_keys(tool);
```

**Atomic begin** uses `INSERT OR ABORT` and catches `sqlite3.IntegrityError`:

```python
try:
    cur.execute(
        "INSERT INTO idempotency_keys(key, tool, status, created_at, expires_at) "
        "VALUES (?, ?, 'in_progress', ?, ?)",
        (key, tool, now, now + ttl),
    )
    return True   # newly inserted; caller proceeds with tool execution
except sqlite3.IntegrityError:
    return False  # row exists; caller should call get() and react
```

**Async wrapper** — sqlite3 is sync; we wrap each method body in `await asyncio.to_thread(...)` to avoid blocking the event loop. WAL mode allows concurrent reads.

**`idempotency_key` field on Destructive_Tool inputs** — design choice: **import-time mutation of Pydantic input models**. The Tool_Wrapper, before binding the registrar's `@mcp.tool` callable, consults `DESTRUCTIVE_TOOLS`; for every match, it walks `descriptor.input_model.model_fields` and adds an optional `idempotency_key: Annotated[str | None, Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_\-]+$")]`. We use `pydantic.create_model(<original>, idempotency_key=(str | None, Field(...)))` to produce a subclass without mutating the original class. The descriptor's `input_model` is replaced with the subclass before `@mcp.tool` registers it, so the JSON schema MCP advertises includes the new field automatically.

Justification: this keeps tool source files free of boilerplate, the field appears in the tool's input schema for LLM discoverability, and there's a single source of truth (`DESTRUCTIVE_TOOLS`).

### `instagram_mcp/state_store.py` (Track 4)

**Public API**

```python
@dataclass
class ProxySnapshot:
    proxy_url: str
    cb_state: Literal["closed", "open", "half_open"]
    cb_until_epoch: int
    consecutive_failures: int
    total_requests: int
    total_failures: int

@dataclass
class RateLimiterSnapshot:
    scope: str  # "global" or proxy URL
    current_rps: float
    max_rate: float
    consecutive_429s: int
    consecutive_successes: int

@dataclass
class AccountSnapshot:
    alias: str
    status: Literal["active", "rate_limited", "checkpoint", "expired"]
    cooldown_until_epoch: int
    consecutive_failures: int

@dataclass
class StateSnapshot:
    proxies: list[ProxySnapshot]
    rate_limiters: list[RateLimiterSnapshot]
    accounts: list[AccountSnapshot]
    schema_version: int = CURRENT_SCHEMA_VERSION

class StateStore:
    def __init__(self, db_path: Path, flush_interval_seconds: int = 30): ...
    async def load(self) -> StateSnapshot: ...
    async def save(self, snapshot: StateSnapshot) -> None: ...
    async def start_flush_loop(self, snapshot_provider: Callable[[], StateSnapshot]) -> None: ...
    async def stop_flush_loop(self) -> None: ...
    def is_enabled(self) -> bool: ...
```

**SQLite schema**

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', '1');

CREATE TABLE IF NOT EXISTS proxies (
    proxy_url            TEXT PRIMARY KEY,
    cb_state             TEXT NOT NULL,
    cb_until_epoch       INTEGER NOT NULL,
    consecutive_failures INTEGER NOT NULL,
    total_requests       INTEGER NOT NULL,
    total_failures       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_limiters (
    scope                  TEXT PRIMARY KEY,
    current_rps            REAL NOT NULL,
    max_rate               REAL NOT NULL,
    consecutive_429s       INTEGER NOT NULL,
    consecutive_successes  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    alias                  TEXT PRIMARY KEY,
    status                 TEXT NOT NULL,
    cooldown_until_epoch   INTEGER NOT NULL,
    consecutive_failures   INTEGER NOT NULL
);
```

**Schema migrations** — `_MIGRATIONS: dict[int, Callable[[Connection], None]]`. On open, read the version row; for each `n` in range(version+1, CURRENT+1), run `_MIGRATIONS[n]`. v1 is the initial schema above.

**Snapshot provider** — `instagram_mcp/__init__.py` lifespan builds a closure that calls `proxy_manager.to_snapshot()`, `rate_limiter.to_snapshot()`, `account_pool.to_snapshot()` and assembles a `StateSnapshot`.

**Restore on startup** — after `load()`, iterate the snapshot:

```python
for p in snapshot.proxies:
    proxy_manager.restore_proxy(p)
rate_limiter.restore_from_snapshot([s for s in snapshot.rate_limiters if s.scope == "global"][0])
for a in snapshot.accounts:
    account_pool.restore_account(a)
```

Past `cb_until_epoch` / `cooldown_until_epoch` values relative to `time.time()` are reset to baseline state.

### `instagram_mcp/tracing.py` (Track 5)

**Public API**

```python
def configure_tracer() -> None: ...                    # idempotent; reads OTEL_* env vars
def get_tracer() -> opentelemetry.trace.Tracer: ...    # returns no-op tracer when not configured
@contextmanager
def start_span(name: str, kind: trace.SpanKind = trace.SpanKind.INTERNAL, **attributes) -> trace.Span: ...
def is_enabled() -> bool: ...
```

**Initialization** (only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set and kill switch off):

```python
provider = TracerProvider(
    resource=Resource.create({
        "service.name": os.getenv("OTEL_SERVICE_NAME", "instagram-mcp"),
        "service.version": instagram_mcp.__version__,
    }),
    sampler=_resolve_sampler(),
)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
```

**No-op fallback** — if any `opentelemetry.*` import fails, `get_tracer()` returns `trace.NoOpTracer()` (always available in the API stub) and `start_span()` becomes a no-op context manager via `contextlib.nullcontext`.

### `instagram_mcp/health.py` (Track 5)

**Public API**

```python
async def liveness_handler(request): ...  # always 200 {"status": "ok"}
async def readiness_handler(request): ...
def mount_routes(app, *, client, config, state_store) -> None: ...
```

**Readiness logic**:

```python
async def readiness_handler(request):
    checks = {}
    # cookies — only required when at least one auth tier tool registered
    if any(d.auth_tier == "auth" for d in request.app.state.inventory):
        checks["cookies"] = bool(client.cookie_manager and client.cookie_manager.is_authenticated)
    # proxies — only required when proxies configured
    if config.proxy_urls:
        checks["proxy_active"] = any(
            p["state"] == "closed"
            for p in await client.proxy_manager.snapshot_for_health()
        )
    # state DB writable
    if state_store.is_enabled():
        checks["state_db"] = state_store.is_writable()
    ok = all(checks.values())
    status = 200 if ok else 503
    return JSONResponse(checks, status_code=status)
```

**Mounting** — when FastMCP constructs its Starlette app for HTTP transport, `_lifespan` calls `health.mount_routes(starlette_app, client=client, config=config, state_store=state_store)`. If FastMCP's API doesn't expose the Starlette app directly, fall back to launching a small aiohttp server on a separate port (default 8080).

## Tool_Wrapper integration

The orchestrator wraps every `@mcp.tool`-decorated callable returned by submodule registrars. Pseudocode:

```python
def _tool_wrapper(descriptor: ToolDescriptor, fn: Callable) -> Callable:
    is_destructive = descriptor.name in DESTRUCTIVE_TOOLS

    @functools.wraps(fn)
    async def wrapped(params, ctx):
        # 1. Generate / pick correlation ID
        corr_id = current_correlation_id.get() or new_correlation_id()
        # 2. Set ContextVars
        tokens = (
            current_correlation_id.set(corr_id),
            current_tool_name.set(descriptor.name),
            current_account_alias.set(getattr(client, "_active_alias", None)),
        )
        # 3. Start span
        with start_span(
            f"tool.{descriptor.name}",
            kind=trace.SpanKind.SERVER,
            **{
                "instagram_mcp.tool": descriptor.name,
                "instagram_mcp.toolset": descriptor.toolset,
                "instagram_mcp.auth_tier": descriptor.auth_tier,
                "instagram_mcp.correlation_id": corr_id,
            },
        ) as span:
            t0 = time.perf_counter()
            outcome = "error"  # default until proven otherwise
            try:
                # 4. Idempotency lookup (only for destructive tools)
                if is_destructive and (key := getattr(params, "idempotency_key", None)):
                    cached = await idempotency_store.get(key)
                    if cached and cached.status == "completed" and cached.expires_at > time.time():
                        outcome = "cached"
                        span.set_attribute("instagram_mcp.outcome", outcome)
                        return cached.result_json
                    if cached and cached.status == "in_progress" and cached.expires_at > time.time():
                        outcome = "error"
                        raise _tool_error("operation_in_progress", "rate_limited",
                                          "A previous call with this idempotency_key is still in flight.")
                    inserted = await idempotency_store.begin(key, descriptor.name)
                    if not inserted:  # race: another worker just won; recurse
                        return await wrapped(params, ctx)

                # 5. Invoke real tool
                result = await fn(params, ctx)
                outcome = "success"
                # 6. Record idempotency outcome
                if is_destructive and key:
                    await idempotency_store.complete(key, result)
                return result
            except ToolError as e:
                outcome = "rate_limited" if "rate_limit" in str(e).lower() else "error"
                if is_destructive and (key := getattr(params, "idempotency_key", None)):
                    await idempotency_store.fail(key, json.dumps({"error_type": getattr(e, "error_type", "unexpected_error"), "message": str(e)}))
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                span.set_attribute("instagram_mcp.error_type", getattr(e, "error_type", "unexpected_error"))
                raise
            finally:
                # 7. Emit metrics + tear down ContextVars
                duration = time.perf_counter() - t0
                TOOL_DURATION.labels(tool=descriptor.name).observe(duration)
                TOOL_CALLS.labels(
                    tool=descriptor.name,
                    toolset=descriptor.toolset,
                    auth_tier=descriptor.auth_tier,
                    outcome=outcome,
                ).inc()
                span.set_attribute("instagram_mcp.outcome", outcome)
                for var, tok in zip(
                    (current_account_alias, current_tool_name, current_correlation_id),
                    reversed(tokens),
                ):
                    var.reset(tok)

    return wrapped
```

The wrapper sits between the registrar's `@mcp.tool` decorator and the underlying tool body. Since `@mcp.tool` returns the function unchanged (it just registers it with FastMCP), the orchestrator wraps the function before passing it through registration: descriptors are returned with their `handler` field already wrapped.

## Configuration matrix

| Env var | Default | Valid values | Source requirement |
|---|---|---|---|
| `INSTAGRAM_MCP_OBSERVABILITY_DISABLED` | `""` (off) | `1`, `true` | R31, R33 |
| `INSTAGRAM_MCP_METRICS_DISABLED` | `""` | `1`, `true` | R5.4 |
| `INSTAGRAM_MCP_METRICS_PORT` | `9090` | int 1-65535 | R5.2 |
| `INSTAGRAM_MCP_PUSHGATEWAY_URL` | `""` | URL | R6.1 |
| `INSTAGRAM_MCP_PUSHGATEWAY_INTERVAL` | `30` | int seconds | R6.1 |
| `INSTAGRAM_MCP_PUSHGATEWAY_INSTANCE` | `<hostname>` | string | R6.2 |
| `INSTAGRAM_MCP_LOG_FORMAT` | `text` | `text`, `json` | R7.3 |
| `INSTAGRAM_MCP_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | R8.1 |
| `INSTAGRAM_MCP_IDEMPOTENCY_DISABLED` | `""` | `1`, `true` | R12.4 |
| `INSTAGRAM_MCP_IDEMPOTENCY_TTL_HOURS` | `24` | int | R15.1 |
| `INSTAGRAM_MCP_STATE_DIR` | `.state` | path | R16.1, R18 |
| `INSTAGRAM_MCP_STATE_DISABLED` | `""` | `1`, `true` | R18.4 |
| `INSTAGRAM_MCP_STATE_FLUSH_SECONDS` | `30` | int | R20.2 |
| `INSTAGRAM_MCP_HEALTH_DISABLED` | `""` | `1`, `true` | R27.3 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | URL | R24.2 |
| `OTEL_SERVICE_NAME` | `instagram-mcp` | string | R24.3 |
| `OTEL_TRACES_SAMPLER` | `parentbased_always_on` | OTel sampler name | R24.4 |

## Lifespan integration

Updated skeleton (additions marked `+`):

```python
@contextlib.asynccontextmanager
async def _lifespan(server):
    # +  log_config + metrics + tracing first (cheap, no async)
    +  configure_logging()
    +  metrics.start_endpoint(host="0.0.0.0", port=cfg.metrics_port) if http else None
    +  tracing.configure_tracer()

    # +  Open state and idempotency stores
    +  state = StateStore(state_dir / "state.db", flush_interval_seconds=cfg.state_flush_seconds)
    +  idem = IdempotencyStore(state_dir / "idempotency.db", ttl_seconds=cfg.idempotency_ttl_hours * 3600)

    # +  Restore previous state BEFORE any traffic
    +  snapshot = await state.load()
    +  proxy_manager.restore(snapshot.proxies)
    +  rate_limiter.restore(next((s for s in snapshot.rate_limiters if s.scope == "global"), None))
    +  account_pool.restore(snapshot.accounts)

    # existing background tasks
    cleanup_task = asyncio.ensure_future(_cache_cleanup_loop())
    proxy_manager.start_health_checks()
    scheduler.start()
    monitor.start()

    # +  Observability background tasks
    +  await state.start_flush_loop(_build_snapshot)
    +  await idem.start_cleanup_loop()

    # +  Health routes once Starlette app exists
    +  if http and not cfg.health_disabled:
    +      health.mount_routes(server.starlette_app, client=client, config=cfg, state_store=state)

    try:
        yield
    finally:
        # reverse order
    +    await idem.stop_cleanup_loop()
    +    await state.stop_flush_loop()
    +    await state.save(_build_snapshot())  # final flush
        cleanup_task.cancel()
    +    metrics.stop_endpoint()
    +    tracing.shutdown()
        ...
    +    await state.close()
    +    await idem.close()
```

## Database schemas

See `idempotency.py` and `state_store.py` sections above for full DDL.

## Correctness Properties (PBT contracts)

**Property 1 — Idempotency dedup (R17)**
For all `(key, tool_body, N)` where `N ∈ [2, 64]`: launching N concurrent invocations of `tool_wrapper(key)` causes `tool_body` to execute exactly once, and every other caller observes either the cached result or `operation_in_progress`.

**Property 2 — State store crash recovery (R23)**
For all `(snapshot, partial_writes)`: `save(snapshot)` followed by simulated crash (close connection without commit of any pending writes after `save`) followed by `StateStore(same_path).load()` returns the same snapshot. WAL mode means committed transactions are durable; partial writes never appear.

**Property 3 — ContextVar isolation**
For all parent/child tool call pairs: a nested call inside an existing tool call uses its own correlation_id while the parent's correlation_id is restored on return. After both calls return, all three ContextVars are back to their pre-parent values.

**Property 4 — Metric label cardinality bounded**
For all sequences of tool calls: the total number of distinct label combinations registered in `tool_calls_total` is bounded by `len(inventory) * 4 outcomes * 3 tiers`. No tool argument value escapes into a label.

## Backwards-compatibility plan

**Optional dependencies** in `pyproject.toml`:

```toml
[project.optional-dependencies]
observability = [
    "prometheus-client>=0.20",
    "python-json-logger>=2.0",
    "opentelemetry-api>=1.25",
    "opentelemetry-sdk>=1.25",
    "opentelemetry-exporter-otlp>=1.25",
]
```

**No-op shim pattern** — every observability module starts with:

```python
import os
def _kill_switch() -> bool:
    return os.environ.get("INSTAGRAM_MCP_OBSERVABILITY_DISABLED", "").lower() in ("1", "true")
```

and gates every public function on it. Imports of optional libraries are wrapped in `try/except ImportError`. When the import fails or kill switch is on, the public API surface stays the same but every method is a no-op.

**v0 user**: with no env vars set, the server registers tools, starts lifespan, and serves requests exactly as today. Metrics emission flows through no-op shims (since neither HTTP transport nor metrics endpoint is implied). State store falls back to in-memory degraded mode if disabled. Idempotency simply ignores the `idempotency_key` field (which doesn't exist on the input model since destructive tools opt in via DESTRUCTIVE_TOOLS — a v0 user never sees the field unless they call a destructive tool, and even then it's optional).

## Security posture

| Concern | Enforcement |
|---|---|
| Cookies in logs | `RedactingFilter` regex-strips `Cookie:` and `sessionid=` from formatted records |
| OAuth tokens in logs | Same filter; tokens never logged in code (already enforced by Sprint 0 redaction) |
| Correlation_ID PII | `uuid.uuid4().hex` only |
| Idempotency_Key PII | Pydantic `pattern=r"^[A-Za-z0-9_\-]+$"` rejects anything that could carry PII |
| File mode | `os.chmod(db_path, 0o600)` on first create on POSIX; on Windows, default ACL |
| State DB contents | Snapshot dataclasses contain only `str` proxy URLs (already masked downstream), `int` epochs, `float` rates, `str` account aliases, `str` enum values — no cookies, no tokens |
| Metrics labels | Only enum-bounded values: tool name, toolset, auth_tier, outcome, masked proxy_id, alias |
| Span attributes | Same enum-bounded values; `cache.key_hash` instead of raw cache keys |
| Health response body | Boolean check results plus short reason strings; no sensitive material |

## Out of Scope

- Multi-process or distributed tracing aggregation across multiple `instagram-mcp` instances.
- Pre-built Grafana dashboards or alert rules.
- Log shipping integrations such as Loki, Elasticsearch, or Splunk.
- Application Performance Monitoring (APM) integrations and Real User Monitoring (RUM).
- Rotating log files; operators are expected to handle rotation through `logrotate` or an equivalent system tool.
