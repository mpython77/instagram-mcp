# Implementation Plan: Production Observability

## Overview

Convert the feature design into a series of prompts for a code-generation LLM that will implement each step with incremental progress. Make sure that each prompt builds on the previous prompts, and ends with wiring things together. There should be no hanging or orphaned code that isn't integrated into a previous step. Focus ONLY on tasks that involve writing, modifying, or testing code.

The plan splits into seven waves that progress from standalone foundation modules through integration layers to final testing:

0. **Foundation modules** — `metrics.py`, `log_config.py`, `idempotency.py`, `state_store.py`, `tracing.py`, `health.py` as standalone modules with no-op shim fallbacks and no cross-dependencies.
1. **Snapshot methods** — `to_snapshot()` / `restore_from_snapshot()` on `proxy_manager.py`, `rate_limiter.py`, `account_pool.py`.
2. **Tool_Wrapper** — wraps every tool with ContextVars + metrics + spans + idempotency in `tools/__init__.py`.
3. **Lifespan integration** — startup/shutdown ordering in `__init__.py`.
4. **Metric emission in existing modules** — `client.py` and `cache.py` emit counters, histograms, and nested spans.
5. **Packaging & docs** — `pyproject.toml` observability extra, README update, env var documentation.
6. **Tests** — unit tests for each module, property tests for idempotency dedup and state crash recovery, smoke tests for `/metrics`, `/healthz`, `/readyz`.

The implementation language is **Python 3.10+** to match the existing codebase. Tests use `pytest`, property-based tests use `hypothesis`.

## Tasks

- [ ] 1. Create `instagram_mcp/metrics.py` — Prometheus metrics module with no-op shim
  - [ ] 1.1 Implement metrics module foundation and no-op shim pattern
    - Create `instagram_mcp/metrics.py` with `_kill_switch()` helper reading `INSTAGRAM_MCP_OBSERVABILITY_DISABLED`
    - Wrap `from prometheus_client import ...` in `try/except ImportError`; set `_PROM_AVAILABLE` flag
    - When `_PROM_AVAILABLE` is False or kill switch is on, provide `_NoOpMetric` class with `.labels(**kw)`, `.inc()`, `.observe()`, `.set()` methods that return `self` or `None`
    - Declare singleton `REGISTRY = CollectorRegistry()` (or `None` for no-op)
    - Declare all counters: `TOOL_CALLS` `{tool, toolset, auth_tier, outcome}`, `PROXY_REQUESTS` `{proxy_id, outcome}`, `RATE_LIMITER_429S` (no labels), `CIRCUIT_BREAKER_OPENS` `{scope}`, `CACHE_OPERATIONS` `{op, result}`
    - Declare histograms: `TOOL_DURATION` `{tool}` with buckets `[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30]`, `PROXY_LATENCY` `{proxy_id}` same buckets
    - Declare gauges: `PROXY_STATE` `{proxy_id, state}`, `RATE_LIMITER_RPS` `{scope}`, `ACCOUNT_POOL_STATE` `{alias, state}`
    - Export `is_enabled() -> bool`, `start_endpoint(host, port)`, `stop_endpoint()`, `push_to_gateway(url, job, instance)`
    - Expose all public symbols via `__all__`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 4.1, 4.2, 4.3_

  - [ ] 1.2 Implement HTTP `/metrics` endpoint and Pushgateway exporter
    - Add `start_endpoint(host: str = "0.0.0.0", port: int = 9090) -> None` that launches an async HTTP server serving `generate_latest(REGISTRY)` at `GET /metrics` with `Content-Type: text/plain; version=0.0.4; charset=utf-8`
    - Handle port-bind failures gracefully: log WARNING and continue without endpoint
    - Add `stop_endpoint() -> None` to shut down the HTTP server
    - Add `push_to_gateway(url, job="instagram_mcp", instance=None)` using `prometheus_client.push_to_gateway`; on failure log WARNING and continue
    - Respect `INSTAGRAM_MCP_METRICS_DISABLED` env var to skip endpoint start
    - Skip endpoint when transport is not `http`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.1, 6.2, 6.3, 6.4_

- [ ] 2. Create `instagram_mcp/log_config.py` — Structured logging with ContextVars
  - [ ] 2.1 Implement logging configuration and ContextVars
    - Create `instagram_mcp/log_config.py` with three module-level `ContextVar` instances: `current_correlation_id`, `current_tool_name`, `current_account_alias` (default `None`)
    - Implement `new_correlation_id() -> str` using `uuid.uuid4().hex`
    - Implement `configure_logging(level: str | None = None, fmt: str | None = None) -> None`
    - Read `INSTAGRAM_MCP_LOG_FORMAT` (default `"text"`); when `"json"`, install JSON formatter; when `"text"` or unset, install plain-text formatter
    - Read `INSTAGRAM_MCP_LOG_LEVEL` (default `"INFO"`); reject unrecognised values with WARNING fallback to INFO
    - Wrap `from pythonjsonlogger import ...` in `try/except ImportError`; fall back to plain-text with WARNING log
    - Implement `ContextFilter(logging.Filter)` that copies ContextVar values onto every `LogRecord` as `correlation_id`, `tool`, `account_alias` (use `None`/JSON `null` when unset)
    - Implement `RedactingFilter(logging.Filter)` that strips `Cookie:` headers and `sessionid=` patterns from formatted records
    - Install both filters on root logger
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 8.1, 8.2, 8.3, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 10.1, 10.2, 10.3, 11.1, 11.2, 11.3_

- [ ] 3. Create `instagram_mcp/idempotency.py` — Idempotency store with SQLite backend
  - [ ] 3.1 Implement IdempotencyStore class and SQLite schema
    - Create `instagram_mcp/idempotency.py` with `_kill_switch()` check
    - Define `IdempotencyEntry` dataclass with fields: `key`, `tool`, `status` (Literal), `result_json`, `error_json`, `created_at`, `expires_at`
    - Implement `IdempotencyStore(db_path: Path, ttl_seconds: int = 86400)` class
    - On init, create parent directory with mode `0o700` on POSIX if missing
    - Open SQLite with `journal_mode=WAL`, `synchronous=NORMAL`
    - Create table `idempotency_keys` with schema: `key TEXT PRIMARY KEY`, `tool TEXT NOT NULL`, `status TEXT NOT NULL CHECK(...)`, `result_json TEXT`, `error_json TEXT`, `created_at INTEGER NOT NULL`, `expires_at INTEGER NOT NULL`
    - Create indexes on `expires_at` and `tool`
    - Set file mode `0o600` on POSIX
    - Implement `is_enabled() -> bool`
    - When kill switch on or `INSTAGRAM_MCP_IDEMPOTENCY_DISABLED=1`, all methods behave as no-op
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 16.1, 16.2, 16.3, 16.4, 34.2_

  - [ ] 3.2 Implement async CRUD methods and cleanup loop
    - Implement `async get(key) -> IdempotencyEntry | None` via `asyncio.to_thread`
    - Implement `async begin(key, tool) -> bool` using `INSERT OR ABORT` + `IntegrityError` catch for atomic dedup
    - Implement `async complete(key, result_json) -> None` updating status to `"completed"`
    - Implement `async fail(key, error_json) -> None` updating status to `"error"`
    - Implement `async cleanup_expired() -> int` deleting rows where `expires_at < now`; enforce 50MB disk cap by evicting oldest expired entries
    - Implement `async start_cleanup_loop() -> None` running cleanup every 60 seconds
    - Implement `async stop_cleanup_loop() -> None` cancelling within 3 seconds
    - Implement `async close() -> None` closing the SQLite connection
    - TTL defaults to `INSTAGRAM_MCP_IDEMPOTENCY_TTL_HOURS * 3600` (default 24h)
    - _Requirements: 12.3, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 15.1, 15.2, 15.3, 15.4, 15.5, 32.1, 32.3_

- [ ] 4. Create `instagram_mcp/state_store.py` — Persistent state with periodic flush
  - [ ] 4.1 Implement StateStore class, snapshot dataclasses, and SQLite schema
    - Create `instagram_mcp/state_store.py` with `_kill_switch()` check
    - Define dataclasses: `ProxySnapshot`, `RateLimiterSnapshot`, `AccountSnapshot`, `StateSnapshot`
    - Implement `StateStore(db_path: Path, flush_interval_seconds: int = 30)` class
    - On init, create parent directory with mode `0o700` on POSIX if missing
    - Open SQLite with `journal_mode=WAL`, `synchronous=NORMAL`
    - Create tables: `metadata` (key/value), `proxies`, `rate_limiters`, `accounts` per design schema
    - Insert initial `schema_version = 1` row via `INSERT OR IGNORE`
    - Set file mode `0o600` on POSIX
    - Implement schema version check and forward migration support
    - On version mismatch (future > current), enter Degraded_Fallback mode with ERROR log
    - On corrupt/missing/locked DB, enter Degraded_Fallback mode with WARNING log
    - Implement `is_enabled() -> bool` and `is_writable() -> bool`
    - When kill switch on or `INSTAGRAM_MCP_STATE_DISABLED=1`, all methods behave as no-op
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 19.1, 19.2, 19.3, 19.4, 19.5, 22.1, 22.2, 22.3, 22.4, 22.5, 34.2, 34.3_

  - [ ] 4.2 Implement load, save, and flush loop methods
    - Implement `async load() -> StateSnapshot` reading all tables via `asyncio.to_thread`; return empty snapshot in degraded mode
    - Implement `async save(snapshot: StateSnapshot) -> None` using `INSERT OR REPLACE` for each table within a transaction; enforce 100MB disk cap with `VACUUM` if exceeded
    - Implement `async start_flush_loop(snapshot_provider: Callable[[], StateSnapshot]) -> None` invoking `save` every `flush_interval_seconds`; on exception log WARNING and retry next interval
    - Implement `async stop_flush_loop() -> None` cancelling the loop task
    - Implement `async close() -> None` closing the SQLite connection
    - _Requirements: 18.3, 20.1, 20.2, 20.3, 20.4, 32.2, 32.3_

- [ ] 5. Create `instagram_mcp/tracing.py` — OpenTelemetry tracing with no-op fallback
  - [ ] 5.1 Implement tracing module with configure/start_span/shutdown
    - Create `instagram_mcp/tracing.py` with `_kill_switch()` check
    - Wrap `from opentelemetry import trace` and SDK imports in `try/except ImportError`; set `_OTEL_AVAILABLE` flag
    - Implement `configure_tracer() -> None`: when `OTEL_EXPORTER_OTLP_ENDPOINT` is set and kill switch off, create `TracerProvider` with `Resource(service.name, service.version)`, configure sampler from `OTEL_TRACES_SAMPLER` (default `parentbased_always_on`), add `BatchSpanProcessor(OTLPSpanExporter())`
    - When OTLP endpoint unset or deps missing, log INFO and use no-op tracer
    - Implement `get_tracer() -> Tracer` returning configured or no-op tracer
    - Implement `start_span(name, kind=INTERNAL, **attributes)` as context manager; no-op via `contextlib.nullcontext` when disabled
    - Implement `shutdown() -> None` to flush and shut down the tracer provider
    - Implement `is_enabled() -> bool`
    - _Requirements: 24.1, 24.2, 24.3, 24.4, 24.5, 25.1, 25.2, 25.5_

- [ ] 6. Create `instagram_mcp/health.py` — Health probe endpoints
  - [ ] 6.1 Implement liveness and readiness handlers with route mounting
    - Create `instagram_mcp/health.py`
    - Implement `async liveness_handler(request)` returning HTTP 200 `{"status": "ok"}` unconditionally
    - Implement `async readiness_handler(request)` evaluating three checks: cookies presence (when auth-tier tools registered), at least one proxy CLOSED (when proxies configured), state DB writable (when state store enabled)
    - Return HTTP 200 with check results JSON when all pass; HTTP 503 with failure reasons when any fails
    - Implement `mount_routes(app, *, client, config, state_store) -> None` adding `GET /healthz` and `GET /readyz` routes to the Starlette app
    - Respect `INSTAGRAM_MCP_HEALTH_DISABLED` env var and transport mode (HTTP only)
    - Ensure no Sensitive_Material in response bodies
    - _Requirements: 27.1, 27.2, 27.3, 28.1, 28.2, 29.1, 29.2, 29.3, 29.4, 29.5, 34.4_

- [ ] 7. Checkpoint - All foundation modules compile and import independently
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. Add snapshot methods to existing components
  - [ ] 8.1 Add `to_snapshot()` / `restore_from_snapshot()` to `instagram_mcp/proxy_manager.py`
    - Add `to_snapshot() -> list[ProxySnapshot]` returning per-proxy `cb_state`, `cb_until_epoch`, `consecutive_failures`, `total_requests`, `total_failures`
    - Add `restore_from_snapshot(proxies: list[ProxySnapshot]) -> None` restoring circuit breaker state; treat past `cb_until_epoch` as expired (transition to baseline)
    - Add metric callback: on proxy state transition, update `PROXY_STATE` gauge so exactly one `state` label per `proxy_id` holds value `1`
    - Add `snapshot_for_health() -> list[dict]` returning proxy states for readiness check
    - _Requirements: 4.4, 19.1, 21.1, 21.2, 21.5_

  - [ ] 8.2 Add `to_snapshot()` / `restore_from_snapshot()` to `instagram_mcp/rate_limiter.py`
    - Add `to_snapshot() -> list[RateLimiterSnapshot]` returning global and per-proxy `current_rps`, `max_rate`, `consecutive_429s`, `consecutive_successes`
    - Add `restore_from_snapshot(snapshot: RateLimiterSnapshot) -> None` clamping `current_rps` into `[min_rate, base_rate * 2.5]`
    - Add metric callback: on `current_rps` change, update `RATE_LIMITER_RPS` gauge with corresponding `scope` label
    - _Requirements: 4.5, 19.2, 19.3, 21.3, 21.4_

  - [ ] 8.3 Add `to_snapshot()` / `restore_from_snapshot()` to `instagram_mcp/account_pool.py`
    - Add `to_snapshot() -> list[AccountSnapshot]` returning per-account `alias`, `status`, `cooldown_until_epoch`, `consecutive_failures`
    - Add `restore_from_snapshot(accounts: list[AccountSnapshot]) -> None` restoring status; treat past `cooldown_until_epoch` as expired
    - Add metric callback: on account status change, update `ACCOUNT_POOL_STATE` gauge so exactly one `state` label per `alias` holds value `1`
    - Ensure no cookies, tokens, or session IDs are included in snapshot
    - _Requirements: 4.6, 19.4, 19.5, 21.4, 21.5_

- [ ] 9. Implement Tool_Wrapper in `instagram_mcp/tools/__init__.py`
  - [ ] 9.1 Add `_tool_wrapper` decorator integrating ContextVars, metrics, spans, and idempotency
    - Implement `_tool_wrapper(descriptor, fn, idempotency_store)` as an async wrapper function
    - On entry: generate/pick correlation ID, set `current_correlation_id`, `current_tool_name`, `current_account_alias` ContextVars with token tracking
    - Start `tool.<tool_name>` span with `kind=SERVER` and attributes `instagram_mcp.tool`, `instagram_mcp.toolset`, `instagram_mcp.auth_tier`, `instagram_mcp.correlation_id`
    - For destructive tools with `idempotency_key`: lookup in store, return cached result (outcome=`"cached"`), raise `ToolError` if `"in_progress"`, or `begin()` and proceed
    - Invoke the real tool function
    - On success: mark idempotency `complete`, set outcome=`"success"`
    - On `ToolError`: mark idempotency `fail`, set span status ERROR
    - In finally block: observe `TOOL_DURATION`, increment `TOOL_CALLS` with resolved labels, set span outcome attribute, reset all ContextVars via tokens
    - _Requirements: 2.6, 3.3, 3.4, 9.2, 9.3, 9.4, 9.5, 13.1, 13.2, 13.3, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 25.1, 25.2, 25.3, 25.4, 30.1, 30.2_

  - [ ] 9.2 Add `idempotency_key` field injection for Destructive_Tools
    - For each tool in `DESTRUCTIVE_TOOLS`, use `pydantic.create_model` to produce a subclass of the original input model adding `idempotency_key: Annotated[str | None, Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_\\-]+$")]`
    - Replace the descriptor's `input_model` with the subclass before MCP registration
    - Validate key length (1-128) in wrapper; raise `ToolError(error_type="validation_error")` on violation
    - Non-destructive tools do NOT get the field
    - _Requirements: 13.1, 13.2, 13.3, 13.4_

- [ ] 10. Integrate observability into lifespan in `instagram_mcp/__init__.py`
  - [ ] 10.1 Wire startup sequence: logging → metrics → tracing → state restore → background loops → health
    - In `_lifespan` startup, call `configure_logging()` first
    - Call `metrics.start_endpoint(host, port)` when transport is HTTP and metrics not disabled
    - Call `tracing.configure_tracer()`
    - Instantiate `StateStore` and `IdempotencyStore` with paths from `INSTAGRAM_MCP_STATE_DIR`
    - Call `state.load()` and restore snapshot to `proxy_manager`, `rate_limiter`, `account_pool` BEFORE any traffic
    - Start `state.start_flush_loop(_build_snapshot)` and `idem.start_cleanup_loop()`
    - Call `health.mount_routes(app, client=client, config=cfg, state_store=state)` when HTTP and health not disabled
    - Build `_build_snapshot` closure calling `to_snapshot()` on all three components
    - _Requirements: 20.1, 21.1, 21.2, 21.3, 21.4, 21.5, 27.1, 31.1, 31.2, 31.3_

  - [ ] 10.2 Wire shutdown sequence in reverse order
    - Stop idempotency cleanup loop
    - Stop state flush loop
    - Final `state.save(_build_snapshot())` flush
    - Cancel metrics endpoint
    - Call `tracing.shutdown()`
    - Close state store and idempotency store SQLite connections
    - Ensure graceful shutdown completes within reasonable timeout
    - _Requirements: 15.4, 20.3_

- [ ] 11. Checkpoint - Tool_Wrapper and lifespan integration functional
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. Emit metrics and spans in `instagram_mcp/client.py`
  - [ ] 12.1 Add proxy request counter, latency histogram, and nested spans to InstagramClient
    - On each HTTP request completion, increment `PROXY_REQUESTS.labels(proxy_id=..., outcome=...)` where `proxy_id` is the resolved proxy identifier or `"direct"` if no proxy
    - Observe wall-clock duration into `PROXY_LATENCY.labels(proxy_id=...)`
    - When tracing enabled, open `http.fetch` nested span with `kind=CLIENT` and attributes `http.method`, `http.url`, `proxy.id`
    - On proxy retry, open `proxy.retry` nested span with attributes `proxy.id`, `proxy.attempt`
    - Ensure `Cookie` header value is replaced with `"<redacted>"` before any debug logging
    - Increment `RATE_LIMITER_429S` counter on 429 responses
    - Increment `CIRCUIT_BREAKER_OPENS.labels(scope=...)` on circuit breaker open transitions
    - _Requirements: 2.2, 3.4, 4.4, 4.5, 11.2, 26.1, 26.2, 30.1_

- [ ] 13. Emit metrics and spans in `instagram_mcp/cache.py`
  - [ ] 13.1 Add cache operation counter and nested spans to SmartCache
    - On each `get`/`set`/`evict`/`cleanup` operation, increment `CACHE_OPERATIONS.labels(op=..., result=...)` with appropriate result label (`hit`, `miss`, `stored`, `expired`, `evicted`)
    - When tracing enabled, open `cache.get` or `cache.set` nested span with attributes `cache.key_hash` (stable hash of key, NOT raw key) and `cache.outcome`
    - _Requirements: 2.5, 26.3, 26.4_

- [ ] 14. Update `pyproject.toml` and documentation
  - [ ] 14.1 Add `[project.optional-dependencies].observability` to `pyproject.toml`
    - Add pinned dependencies: `prometheus-client>=0.20`, `python-json-logger>=2.0`, `opentelemetry-api>=1.25`, `opentelemetry-sdk>=1.25`, `opentelemetry-exporter-otlp>=1.25`
    - Ensure existing `[project.optional-dependencies].dev` includes `hypothesis` for property tests
    - _Requirements: 31.1, 33.1, 33.2_

  - [ ] 14.2 Update README with observability documentation
    - Add **Observability** section documenting the five tracks (metrics, logging, idempotency, state, tracing+health)
    - Add **Environment Variables** table listing all new `INSTAGRAM_MCP_*` and `OTEL_*` env vars with defaults and descriptions
    - Add **Quick Start** subsection: `pip install instagram-mcp[observability]`
    - Document the kill switch `INSTAGRAM_MCP_OBSERVABILITY_DISABLED=1`
    - Document `/metrics`, `/healthz`, `/readyz` endpoints
    - Document `idempotency_key` field on destructive tools
    - _Requirements: 33.4, 34.4_

- [ ] 15. Checkpoint - Full integration complete, ready for testing
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 16. Unit tests for foundation modules
  - [ ] 16.1 Write unit tests for `instagram_mcp/metrics.py` in `tests/test_metrics.py`
    - Test no-op shim: when `prometheus_client` not available, all metric operations succeed silently
    - Test kill switch: when `INSTAGRAM_MCP_OBSERVABILITY_DISABLED=1`, metrics are no-op
    - Test counter/histogram/gauge declarations have correct names and label sets
    - Test `is_enabled()` returns correct boolean based on env and deps
    - Test `start_endpoint` / `stop_endpoint` lifecycle
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 35.1_

  - [ ] 16.2 Write unit tests for `instagram_mcp/log_config.py` in `tests/test_log_config.py`
    - Test `configure_logging` installs JSON formatter when `INSTAGRAM_MCP_LOG_FORMAT=json`
    - Test `configure_logging` installs plain-text formatter when format is `text` or unset
    - Test invalid log level falls back to INFO with WARNING
    - Test `ContextFilter` copies ContextVar values onto LogRecord
    - Test `RedactingFilter` strips Cookie headers and sessionid patterns
    - Test `new_correlation_id()` returns valid UUID hex string
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 8.1, 8.2, 8.3, 10.1, 10.2, 10.3, 11.1, 35.1_

  - [ ] 16.3 Write unit tests for `instagram_mcp/idempotency.py` in `tests/test_idempotency.py`
    - Test `begin()` returns True on first insert, False on duplicate key
    - Test `get()` returns entry after `begin()` + `complete()`
    - Test `complete()` updates status and stores result_json
    - Test `fail()` updates status and stores error_json
    - Test `cleanup_expired()` removes expired entries and returns count
    - Test no-op mode when kill switch enabled
    - Test concurrent `begin()` calls with same key (only one wins)
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 35.1_

  - [ ] 16.4 Write unit tests for `instagram_mcp/state_store.py` in `tests/test_state_store.py`
    - Test `save()` + `load()` round-trip for a complete StateSnapshot
    - Test schema version migration forward
    - Test Degraded_Fallback on corrupt DB file
    - Test Degraded_Fallback on future schema version
    - Test flush loop invokes save at configured interval
    - Test no-op mode when kill switch enabled
    - Test `is_writable()` returns correct status
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 19.1, 19.2, 19.3, 19.4, 19.5, 20.1, 20.2, 20.4, 22.1, 22.2, 22.3, 22.4, 22.5, 35.1_

  - [ ] 16.5 Write unit tests for `instagram_mcp/tracing.py` in `tests/test_tracing.py`
    - Test no-op fallback when `opentelemetry` not installed
    - Test no-op when `OTEL_EXPORTER_OTLP_ENDPOINT` unset
    - Test `configure_tracer()` creates provider when endpoint set
    - Test `start_span()` returns context manager (no-op or real)
    - Test `is_enabled()` reflects configuration state
    - _Requirements: 24.1, 24.2, 24.3, 24.4, 24.5, 35.1_

  - [ ] 16.6 Write unit tests for `instagram_mcp/health.py` in `tests/test_health.py`
    - Test `/healthz` always returns 200 with `{"status": "ok"}`
    - Test `/readyz` returns 200 when all checks pass
    - Test `/readyz` returns 503 when cookies missing (auth-tier tools registered)
    - Test `/readyz` returns 503 when no proxy in CLOSED state
    - Test `/readyz` returns 503 when state DB not writable
    - Test response bodies contain no sensitive material
    - _Requirements: 27.1, 28.1, 28.2, 29.1, 29.2, 29.3, 29.4, 29.5, 35.1_

- [ ] 17. Property-based tests
  - [ ]* 17.1 Write property test for idempotency dedup in `tests/properties/test_idempotency_dedup.py`
    - **Property 1: Idempotency dedup — concurrent calls execute tool body exactly once**
    - **Validates: Requirements 14.7, 17.1, 17.2, 17.3**
    - Use `hypothesis` to generate randomized `N` (2 ≤ N ≤ 64) concurrent invocations of a stubbed Destructive_Tool with the same `idempotency_key`
    - Assert the underlying tool body executes exactly once
    - Assert every concurrent caller observes either the cached result or `"operation_in_progress"` ToolError
    - Tag docstring `Feature: production-observability, Property 1: Idempotency dedup`
    - Configure with `@settings(max_examples=200)`

  - [ ]* 17.2 Write property test for state store crash recovery in `tests/properties/test_state_crash_recovery.py`
    - **Property 2: State store crash recovery — committed snapshots survive process kill**
    - **Validates: Requirements 23.1, 23.2**
    - Use `hypothesis` to generate randomized `StateSnapshot` (random proxy states, rate limiter values, account statuses)
    - Write via `save()`, simulate crash by closing connection without flushing pending writes
    - Reopen fresh `StateStore` on same file, call `load()`
    - Assert loaded snapshot equals the most recently saved snapshot
    - Tag docstring `Feature: production-observability, Property 2: State store crash recovery`
    - Configure with `@settings(max_examples=200)`

- [ ] 18. Smoke tests for HTTP endpoints
  - [ ]* 18.1 Write smoke test for `/metrics` endpoint in `tests/test_smoke_metrics.py`
    - Start a live HTTP-transport server instance (or mock Starlette test client)
    - Issue `GET /metrics` and assert response parses as Prometheus text exposition format
    - Assert `Content-Type` header matches `text/plain; version=0.0.4; charset=utf-8`
    - Assert at least one `instagram_mcp_` prefixed metric family is present
    - _Requirements: 5.1, 5.3, 35.2_

  - [ ]* 18.2 Write smoke test for `/healthz` endpoint in `tests/test_smoke_healthz.py`
    - Issue `GET /healthz` against a live HTTP-transport server
    - Assert HTTP 200 response with body `{"status": "ok"}`
    - _Requirements: 28.1, 35.3_

  - [ ]* 18.3 Write smoke test for `/readyz` endpoint in `tests/test_smoke_readyz.py`
    - Issue `GET /readyz` against a live HTTP-transport server in deliberately unprepared state (no cookies, no proxies)
    - Assert HTTP 503 response with JSON body listing failed checks
    - _Requirements: 29.3, 35.4_

- [ ] 19. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP, but the property tests (17.1, 17.2) materialize the two Correctness Properties P1-P2 and SHOULD be implemented for spec confidence.
- Property-to-task map: **P1 → 17.1** (idempotency dedup), **P2 → 17.2** (state crash recovery).
- Each task references granular sub-requirement clauses for traceability.
- Checkpoints (tasks 7, 11, 15, 19) are top-level milestones and are intentionally excluded from the dependency graph.
- All foundation modules (Wave 0) use the same no-op shim pattern: `try/except ImportError` + `_kill_switch()` check. This ensures zero behaviour change for v0 users with no observability env vars set.
- The `INSTAGRAM_MCP_OBSERVABILITY_DISABLED=1` kill switch disables all five tracks simultaneously.
- All new dependencies live under `[project.optional-dependencies].observability` — the base install remains unchanged.
- State store and idempotency store use SQLite with WAL mode for concurrent read safety and `asyncio.to_thread` to avoid blocking the event loop.
- The Tool_Wrapper is the single integration point: it sets ContextVars, starts spans, checks idempotency, calls the tool, emits metrics, and tears down in reverse order.
- Existing 780 tests must continue passing without modification (Requirement 33.3).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "2.1", "3.1", "3.2", "4.1", "4.2", "5.1", "6.1"] },
    { "id": 1, "tasks": ["8.1", "8.2", "8.3"] },
    { "id": 2, "tasks": ["9.1", "9.2"] },
    { "id": 3, "tasks": ["10.1", "10.2"] },
    { "id": 4, "tasks": ["12.1", "13.1"] },
    { "id": 5, "tasks": ["14.1", "14.2"] },
    { "id": 6, "tasks": ["16.1", "16.2", "16.3", "16.4", "16.5", "16.6", "17.1", "17.2", "18.1", "18.2", "18.3"] }
  ]
}
```
