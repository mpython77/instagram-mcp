# Requirements Document

## Introduction

`instagram-mcp` loyihasi hozirda 79 ta MCP tool, adaptiv rate limiter, per-proxy circuit breaker, multi-account pool va `SmartCache` bilan ishlaydi, biroq production muhitida operatorga ko'rinish bermaydi: metrikalar Prometheus formatida emas, log'lar inson o'qiy oladigan plain text, write operatsiyalar (DM, like, follow, upload, schedule modify) idempotentlikka kafolatlanmagan, restart paytida circuit breaker / rate limiter / account pool holati yo'qoladi va distributed tracing yo'q. Restartlardan keyin tizim "amneziya"ga uchraydi: avval cooldown'da bo'lgan proxy darhol qayta ishlatiladi, banlangan akkaunt yana navbatga qo'shiladi, shu sababli 429 va shadowban xavfi oshadi.

Bu hujjat production-grade observability platformasini qurish uchun talablarni belgilaydi. Feature beshta yo'nalishni qamrab oladi:

1. **Prometheus Metrics** — `instagram_mcp/metrics.py` modulida `prometheus_client` asosida counter / histogram / gauge'lar; `transport=http` rejimida `/metrics` endpoint, `stdio` rejimida ixtiyoriy Pushgateway export'i (Requirement 1–6).
2. **Structured Logging** — `instagram_mcp/log_config.py` modulida `python-json-logger` asosida JSON format va `contextvars` orqali correlation ID propagatsiyasi (Requirement 7–11).
3. **Idempotency** — `instagram_mcp/idempotency.py` modulida SQLite (`.state/idempotency.db`) asosidagi do'kon, har bir destruktiv tool uchun ixtiyoriy `idempotency_key` (Requirement 12–17).
4. **Persistent State** — `instagram_mcp/state_store.py` modulida 30 sekundlik flush bilan circuit breaker / rate limiter / account pool holatini SQLite'ga saqlash va startup'da qayta tiklash (Requirement 18–23).
5. **Tracing & Health** — `opentelemetry-*` ixtiyoriy paketlari yordamida span generatsiyasi va `transport=http` rejimida `/healthz` (liveness) hamda `/readyz` (readiness) endpoint'lari (Requirement 24–29).

Non-functional talablar (performance budgeti, graceful degradation, security, resource budgetlari, backwards compatibility) Requirement 30–35 da ko'rsatiladi. "Out of Scope" bo'limi multi-process tracing aggregation, pre-built Grafana dashboard'lar, log shipping (Loki / Elasticsearch / Splunk), APM / RUM va rotating log fayllarini ushbu feature doirasidan chiqaradi.

Public API muzlatilgan: tool nomlari, `MCPConfig` dataclass field nomlari va defaults, `INSTAGRAM_MCP_*` env var prefiksi, MCP resource URI shablonlari va prompt nomlari shu feature ichida o'zgarmaydi. Barcha yangi uchinchi tomon paketlari `[project.optional-dependencies].observability` ostida joylashadi va paket o'rnatilmagan bo'lsa, modullar no-op shim'larga grafik tarzda tushadi. Yagona master kalit — `INSTAGRAM_MCP_OBSERVABILITY_DISABLED=1` env vari — barcha besh yo'nalishni bir vaqtda o'chiradi. Hech bir env var o'rnatilmagan v0 foydalanuvchi bugungi xulq-atvorni bit darajasida saqlaydi va mavjud 780 ta test o'zgarishsiz o'tadi.

## Glossary

- **Observability_Stack**: ushbu feature qo'shadigan beshta yo'nalishni (metrics, logging, idempotency, state, tracing+health) jamlovchi mantiqiy guruh.
- **Observability_Kill_Switch**: `INSTAGRAM_MCP_OBSERVABILITY_DISABLED` env vari; qiymati `1` yoki `true` bo'lsa, butun Observability_Stack passiv (no-op) rejimga o'tadi.
- **Optional_Extras**: `pyproject.toml` ichidagi `[project.optional-dependencies].observability` ro'yxati — `prometheus_client`, `python-json-logger`, `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp` va shularga bog'liq paketlar.
- **No_Op_Shim**: Optional_Extras o'rnatilmagan vaziyatda Observability_Stack modullari import qilinganda iste'mol qiluvchi kodga bir xil interfeysni taqdim qiluvchi, ammo hech qanday side-effect chiqarmaydigan stub realizatsiyasi.
- **Metrics_Registry**: `prometheus_client.CollectorRegistry` instansi; `instagram_mcp/metrics.py` modulida yagona global registry sifatida saqlanadi.
- **Metric_Label_Set**: bitta metrika nuqtasiga biriktirilgan label kalit-qiymat juftliklari (masalan `{tool, toolset, auth_tier, outcome}`).
- **Metrics_Endpoint**: `transport=http` rejimida `INSTAGRAM_MCP_METRICS_PORT` (default `9090`) portida ochiladigan HTTP `/metrics` route'i; Prometheus exposition formatini qaytaradi.
- **Pushgateway_Exporter**: `stdio` transport ostida ishlatiladigan ixtiyoriy push-rejim eksporter; `INSTAGRAM_MCP_PUSHGATEWAY_URL` o'rnatilganda yoqiladi.
- **Tool_Wrapper**: har bir registratsiya qilingan MCP tool'i atrofida bajariluvchi async dekorator/middleware; correlation ID, ContextVars, metric emit, span boshlash, idempotency tekshirish va outcome belgilash uchun yagona kirish nuqtasi.
- **Outcome_Label**: bitta tool chaqiruvining yakuniy holatini ifodalovchi `Literal["success", "error", "cached", "deduped"]` qiymati.
- **Auth_Tier**: tool uchun avtorizatsiya darajasi belgisi (`anon`, `auth`, `auto`); mavjud tool inventaridan o'qiladi.
- **Correlation_ID**: bitta tool chaqiruvini boshqa barcha log entry'lari, metric label'lari va span'lariga bog'lovchi UUIDv4 string; `current_correlation_id` ContextVar'ida saqlanadi.
- **Context_Vars**: `contextvars` modulidagi `current_correlation_id`, `current_tool_name`, `current_account_alias` o'zgaruvchilari to'plami; har bir tool chaqiruvi boshida Tool_Wrapper tomonidan o'rnatiladi va tugagandan so'ng tiklanadi.
- **Logging_Filter**: `logging.Filter` subclass'i; har bir `LogRecord`'ga Context_Vars qiymatlarini `correlation_id`, `tool`, `account_alias` atributlari sifatida biriktiradi.
- **JSON_Log_Formatter**: `pythonjsonlogger.json.JsonFormatter` (yoki ekvivalent) konfiguratsiyasi; har bir log entry'sini bitta satrlik JSON obyekti sifatida chiqaradi.
- **Idempotency_Key**: mijoz tomonidan taqdim etilgan ixtiyoriy `str` qiymat (1–128 belgi); destruktiv tool chaqiruvini takroran bajarmaslik uchun ishlatiladi.
- **Idempotency_Store**: `instagram_mcp/idempotency.py` modulidagi SQLite asosli persistent do'kon; default joyi `.state/idempotency.db`.
- **Idempotency_Entry**: do'kondagi yagona yozuv; maydonlari kamida `key TEXT PRIMARY KEY`, `tool TEXT`, `status TEXT CHECK(status IN ('in_progress','completed','error'))`, `result_json TEXT`, `error_json TEXT`, `created_at INTEGER`, `expires_at INTEGER`.
- **Idempotency_TTL**: Idempotency_Entry yozuvi do'konda yashash muddati (default 24 soat, `INSTAGRAM_MCP_IDEMPOTENCY_TTL_HOURS` orqali sozlanadi).
- **Idempotency_Cleanup_Loop**: TTL o'tgan yozuvlarni davriy ravishda o'chiradigan async fon vazifasi; default davr 60 sekund.
- **Destructive_Tool**: `instagram_mcp/tools/_audit.py` ichidagi `DESTRUCTIVE_TOOLS` frozenset'iga kiritilgan har qanday tool nomi (DM yuborish, like, follow, upload, comment, block, profile edit, story publish, schedule modify va shu kabilar).
- **State_Store**: `instagram_mcp/state_store.py` modulidagi SQLite asosli persistent holat do'koni; default joyi `${INSTAGRAM_MCP_STATE_DIR:-.state}/state.db`.
- **State_Snapshot**: bir vaqtda flush qilinadigan holat to'plami: per-proxy circuit breaker holati va cooldown'lari, adaptiv rate limiter `current_rps` / `max_rate` qiymatlari, per-account `status` va `cooldown_until`.
- **State_Flush_Interval**: State_Snapshot ni diskka yozish davri (default 30 sekund, `INSTAGRAM_MCP_STATE_FLUSH_SECONDS` orqali sozlanadi).
- **State_Schema_Version**: State_Store ichidagi metadata jadvalida saqlanuvchi `INTEGER`; schema migratsiyasi uchun ishlatiladi.
- **Degraded_Fallback**: State_Store fayli yo'q, korrupt yoki schema versiyasi mos kelmagan holda ishga tushirish; bu rejimda holat in-memory default qiymatlardan boshlanadi va WARN log yoziladi.
- **OTLP_Endpoint**: `OTEL_EXPORTER_OTLP_ENDPOINT` env varida ko'rsatilgan OpenTelemetry Protocol qabuluvchisi URL'i (gRPC yoki HTTP); shu o'zgaruvchi o'rnatilmagan bo'lsa tracing umuman yoqilmaydi.
- **Tracer_Provider**: `opentelemetry.sdk.trace.TracerProvider` instansi; `OTEL_SERVICE_NAME` (default `instagram-mcp`) va `OTEL_TRACES_SAMPLER` qiymatlaridan resource va sampler yig'iladi.
- **Tool_Span**: bitta tool chaqiruvi uchun yaratilgan ildiz span; nomi `tool.<tool_name>` ko'rinishida bo'ladi.
- **Nested_Span**: Tool_Span ichida ochilgan bola span'lar (`http.fetch`, `proxy.retry`, `cache.get`, `cache.set`, `rate_limiter.acquire` kabi).
- **Health_Probe**: `transport=http` rejimida ochiladigan HTTP route; `/healthz` liveness'ni, `/readyz` readiness'ni bildiradi.
- **Liveness_Probe**: `/healthz` route'i; jarayon javob bera olishini tekshiradi va doimo HTTP 200 qaytaradi (server jarayoni tirik bo'lsa).
- **Readiness_Probe**: `/readyz` route'i; quyidagi uch shartni tekshiradi: (a) cookies talab qilinsa va yuklangan bo'lsa; (b) konfiguratsiyada proxy ko'rsatilgan bo'lsa, kamida bitta proxy CLOSED holatida; (c) State_Store fayli yozilishi mumkin. Hammasi bajarilsa HTTP 200, aks holda 503.
- **Public_API_Surface**: tool nomlari, `MCPConfig` dataclass field nomlari va default qiymatlari, `INSTAGRAM_MCP_*` env var nomlari, MCP resource URI shablonlari, MCP prompt nomlari.
- **Sensitive_Material**: cookies fayl mazmuni, raw `Cookie` HTTP header qiymatlari, OAuth access/refresh tokenlari, Instagram session ID'lari.

## Requirements

## Yo'nalish 1 — Prometheus Metrics

### Requirement 1: Metrics module foundation

**User Story:** As an SRE, I want a single dedicated metrics module, so that every counter, histogram, and gauge in the server has one canonical home.

#### Acceptance Criteria

1. THE Observability_Stack SHALL expose a module at the import path `instagram_mcp.metrics`.
2. THE `instagram_mcp.metrics` module SHALL own a single Metrics_Registry instance reachable via the public attribute `REGISTRY`.
3. WHEN `prometheus_client` is not installed, THE `instagram_mcp.metrics` module SHALL provide No_Op_Shim implementations of every public counter, histogram, and gauge so that callers can import and use them without raising `ImportError`.
4. WHEN the Observability_Kill_Switch is enabled, THE `instagram_mcp.metrics` module SHALL route every metric mutation through No_Op_Shim implementations regardless of whether `prometheus_client` is installed.
5. THE `instagram_mcp.metrics` module SHALL expose every public symbol via `__all__` so that downstream callers know the supported surface.

### Requirement 2: Counter metrics

**User Story:** As an operator, I want counters for the high-volume events in the server, so that I can compute rate, error ratio, and saturation in Grafana.

#### Acceptance Criteria

1. THE Metrics_Registry SHALL declare a counter named `instagram_mcp_tool_calls_total` with the Metric_Label_Set `{tool, toolset, auth_tier, outcome}`.
2. THE Metrics_Registry SHALL declare a counter named `instagram_mcp_proxy_requests_total` with the Metric_Label_Set `{proxy_id, outcome}`.
3. THE Metrics_Registry SHALL declare a counter named `instagram_mcp_rate_limiter_429s_total` with no labels.
4. THE Metrics_Registry SHALL declare a counter named `instagram_mcp_circuit_breaker_opens_total` with the Metric_Label_Set `{scope}` where `scope` is one of `"global"` or `"per_proxy"`.
5. THE Metrics_Registry SHALL declare a counter named `instagram_mcp_cache_operations_total` with the Metric_Label_Set `{op, result}` where `op` is one of `"get"`, `"set"`, `"evict"`, `"cleanup"` and `result` is one of `"hit"`, `"miss"`, `"stored"`, `"expired"`, `"evicted"`.
6. WHEN a tool call completes via the Tool_Wrapper, THE Tool_Wrapper SHALL increment `instagram_mcp_tool_calls_total` exactly once with the resolved Outcome_Label.

### Requirement 3: Histogram metrics

**User Story:** As an operator, I want latency histograms for tool calls and proxy requests, so that I can compute p50/p95/p99 from raw bucket counters.

#### Acceptance Criteria

1. THE Metrics_Registry SHALL declare a histogram named `instagram_mcp_tool_duration_seconds` with the Metric_Label_Set `{tool}` and bucket boundaries `[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30]`.
2. THE Metrics_Registry SHALL declare a histogram named `instagram_mcp_proxy_latency_seconds` with the Metric_Label_Set `{proxy_id}` and the same bucket boundaries as `instagram_mcp_tool_duration_seconds`.
3. WHEN a tool call completes via the Tool_Wrapper, THE Tool_Wrapper SHALL observe the wall-clock duration into `instagram_mcp_tool_duration_seconds` exactly once.
4. WHEN an HTTP request through the `InstagramClient` completes, THE `InstagramClient` SHALL observe its wall-clock duration into `instagram_mcp_proxy_latency_seconds` with the `proxy_id` label set to the resolved proxy identifier or the literal string `"direct"` if no proxy was used.

### Requirement 4: Gauge metrics

**User Story:** As an operator, I want gauges that reflect live runtime state, so that I can build dashboards showing current proxy health, current rate limit, and account pool composition.

#### Acceptance Criteria

1. THE Metrics_Registry SHALL declare a gauge named `instagram_mcp_proxy_state` with the Metric_Label_Set `{proxy_id, state}` whose value is `1` for the currently active state of each proxy and `0` for inactive states.
2. THE Metrics_Registry SHALL declare a gauge named `instagram_mcp_rate_limiter_rps` with the Metric_Label_Set `{scope}` where `scope` is one of `"global"` or a stable proxy identifier.
3. THE Metrics_Registry SHALL declare a gauge named `instagram_mcp_account_pool_state` with the Metric_Label_Set `{alias, state}` whose value is `1` for the currently active state of each account and `0` for inactive states.
4. WHEN a proxy transitions between `CLOSED`, `OPEN`, and `HALF_OPEN`, THE `ProxyManager` SHALL update `instagram_mcp_proxy_state` so that exactly one `state` label per `proxy_id` holds the value `1`.
5. WHEN the adaptive rate limiter changes its `current_rps`, THE `AdaptiveRateLimiter` SHALL update `instagram_mcp_rate_limiter_rps` with the corresponding `scope` label.
6. WHEN the account pool changes a member's status, THE account pool component SHALL update `instagram_mcp_account_pool_state` so that exactly one `state` label per `alias` holds the value `1`.

### Requirement 5: HTTP metrics endpoint

**User Story:** As a Prometheus operator, I want a scrapeable `/metrics` endpoint when the server runs over HTTP transport, so that I can configure a standard scrape job.

#### Acceptance Criteria

1. WHILE `INSTAGRAM_MCP_TRANSPORT=http` and the Observability_Kill_Switch is disabled, THE Instagram_MCP_Server SHALL expose an HTTP `GET /metrics` endpoint on the Metrics_Endpoint port.
2. THE Metrics_Endpoint SHALL bind to the port resolved from `INSTAGRAM_MCP_METRICS_PORT` (default `9090`) and to host `0.0.0.0`.
3. THE Metrics_Endpoint SHALL serve the Prometheus text exposition format with `Content-Type: text/plain; version=0.0.4; charset=utf-8`.
4. WHEN `INSTAGRAM_MCP_METRICS_DISABLED` is set to `1` or `true`, THE Instagram_MCP_Server SHALL skip starting the Metrics_Endpoint.
5. WHILE `INSTAGRAM_MCP_TRANSPORT` is anything other than `"http"`, THE Instagram_MCP_Server SHALL skip starting the Metrics_Endpoint regardless of `INSTAGRAM_MCP_METRICS_PORT`.
6. IF the Metrics_Endpoint fails to bind (port in use, permission denied), THEN THE Instagram_MCP_Server SHALL log the failure at WARNING level and continue startup without the endpoint.

### Requirement 6: Optional Pushgateway export for stdio transport

**User Story:** As an SRE running the server in `stdio` mode, I want an optional Pushgateway exporter, so that I can still ship metrics without a scrapeable HTTP endpoint.

#### Acceptance Criteria

1. WHERE `INSTAGRAM_MCP_PUSHGATEWAY_URL` is set and the Observability_Kill_Switch is disabled, THE Instagram_MCP_Server SHALL push the Metrics_Registry contents to the configured Pushgateway URL every `INSTAGRAM_MCP_PUSHGATEWAY_INTERVAL` seconds (default `30`).
2. THE Pushgateway_Exporter SHALL use the job label `instagram_mcp` and the instance label resolved from `INSTAGRAM_MCP_PUSHGATEWAY_INSTANCE` (default to the OS hostname).
3. IF a Pushgateway push fails, THEN THE Pushgateway_Exporter SHALL log the failure at WARNING level and retry on the next interval without crashing the server.
4. WHEN `INSTAGRAM_MCP_PUSHGATEWAY_URL` is unset, THE Instagram_MCP_Server SHALL not start the Pushgateway_Exporter.

## Yo'nalish 2 — Structured Logging

### Requirement 7: JSON log format

**User Story:** As an SRE, I want every log line to be a single JSON document, so that downstream pipelines can parse the stream without regular expressions.

#### Acceptance Criteria

1. THE Observability_Stack SHALL expose a module at the import path `instagram_mcp.log_config`.
2. THE `instagram_mcp.log_config` module SHALL provide a function `configure_logging(level: str | None = None, fmt: str | None = None) -> None` that installs the chosen formatter on the root logger.
3. WHILE `INSTAGRAM_MCP_LOG_FORMAT` equals `"json"` (case-insensitive), THE `configure_logging` function SHALL install the JSON_Log_Formatter.
4. WHILE `INSTAGRAM_MCP_LOG_FORMAT` equals `"text"` (case-insensitive) or is unset, THE `configure_logging` function SHALL install a plain-text formatter equivalent to the format used today.
5. THE JSON_Log_Formatter SHALL emit each `LogRecord` as a single-line JSON object containing at minimum the keys `timestamp`, `level`, `logger`, `message`, `correlation_id`, `tool`, `account_alias`.
6. WHEN extra structured fields are passed to a logger via the `extra={"key": value}` mechanism, THE JSON_Log_Formatter SHALL include each extra field as a top-level key in the emitted JSON object.
7. WHEN `python-json-logger` is not installed, THE `instagram_mcp.log_config` module SHALL fall back to the plain-text formatter and log a single WARNING line indicating that JSON output is unavailable.

### Requirement 8: Log level configuration

**User Story:** As an operator, I want a single env var to control log verbosity, so that I do not have to edit code to change log level.

#### Acceptance Criteria

1. WHEN `INSTAGRAM_MCP_LOG_LEVEL` is set to one of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (case-insensitive), THE `configure_logging` function SHALL set the root logger level to the corresponding `logging` constant.
2. WHEN `INSTAGRAM_MCP_LOG_LEVEL` is unset, THE `configure_logging` function SHALL set the root logger level to `INFO`.
3. IF `INSTAGRAM_MCP_LOG_LEVEL` holds an unrecognised value, THEN THE `configure_logging` function SHALL fall back to `INFO` and log a single WARNING line naming the rejected value.

### Requirement 9: Context variables

**User Story:** As a developer debugging a customer issue, I want every log line for a single tool call to share a correlation ID, so that I can grep one ID and see the full trace.

#### Acceptance Criteria

1. THE `instagram_mcp.log_config` module SHALL declare three module-level `contextvars.ContextVar` instances named `current_correlation_id`, `current_tool_name`, `current_account_alias`, each with default value `None`.
2. WHEN the Tool_Wrapper begins processing a tool call, THE Tool_Wrapper SHALL set `current_correlation_id` to a freshly generated UUIDv4 string unless the caller already supplied a Correlation_ID.
3. WHEN the Tool_Wrapper begins processing a tool call, THE Tool_Wrapper SHALL set `current_tool_name` to the registered tool name.
4. WHEN the Tool_Wrapper begins processing a tool call against a specific account alias, THE Tool_Wrapper SHALL set `current_account_alias` to that alias.
5. WHEN the Tool_Wrapper finishes processing a tool call, THE Tool_Wrapper SHALL reset all three Context_Vars to their previous tokens so that nested calls and parent contexts are unaffected.
6. THE Correlation_ID generation routine SHALL never include user-supplied input, account credentials, cookies, or tokens.

### Requirement 10: Logging filter

**User Story:** As a developer, I want every log line to automatically carry the active correlation ID, tool name, and account alias, so that I never have to pass them explicitly.

#### Acceptance Criteria

1. THE `instagram_mcp.log_config` module SHALL register a Logging_Filter that copies the values of `current_correlation_id`, `current_tool_name`, and `current_account_alias` onto every `LogRecord` as the attributes `correlation_id`, `tool`, and `account_alias`.
2. WHEN any of the three Context_Vars holds the value `None`, THE Logging_Filter SHALL set the corresponding `LogRecord` attribute to the JSON value `null` rather than omitting the key.
3. THE Logging_Filter SHALL be installed on the root logger so that every child logger inherits it.

### Requirement 11: Sensitive material redaction

**User Story:** As a security engineer, I want guarantees that secrets never reach log output, so that log shipping pipelines do not become an exfiltration channel.

#### Acceptance Criteria

1. THE Observability_Stack SHALL never write the contents of cookies files, raw `Cookie` HTTP headers, OAuth access tokens, OAuth refresh tokens, or Instagram session IDs to any log entry, metric label, span attribute, or persistent store.
2. WHEN a debug log line would otherwise include an HTTP request, THE `InstagramClient` SHALL replace any `Cookie` header value with the literal string `"<redacted>"` before logging.
3. THE Correlation_ID, Idempotency_Key, account alias, and tool name fields SHALL contain no Sensitive_Material.

## Yo'nalish 3 — Idempotency

### Requirement 12: Idempotency module foundation

**User Story:** As a tool author, I want a single idempotency module, so that every Destructive_Tool can opt in with one decorator.

#### Acceptance Criteria

1. THE Observability_Stack SHALL expose a module at the import path `instagram_mcp.idempotency`.
2. THE `instagram_mcp.idempotency` module SHALL provide a class `IdempotencyStore` whose constructor accepts a `db_path: pathlib.Path` and a `ttl_seconds: int`.
3. THE `IdempotencyStore` class SHALL provide async methods `get(key: str) -> IdempotencyEntry | None`, `begin(key: str, tool: str) -> bool`, `complete(key: str, result_json: str) -> None`, `fail(key: str, error_json: str) -> None`, `cleanup_expired() -> int`.
4. WHEN `INSTAGRAM_MCP_IDEMPOTENCY_DISABLED` is set to `1` or `true` or the Observability_Kill_Switch is enabled, THE `IdempotencyStore` SHALL behave as a No_Op_Shim where every method returns the value indicating "not stored, please run the tool".

### Requirement 13: Idempotency key on destructive tool inputs

**User Story:** As an MCP client, I want every destructive tool to accept an `idempotency_key` field, so that I can safely retry without duplicating side effects.

#### Acceptance Criteria

1. THE Pydantic input model of every Destructive_Tool SHALL declare an optional field `idempotency_key: str | None = None`.
2. THE `idempotency_key` field SHALL accept strings of length 1 through 128 inclusive when present.
3. IF the supplied `idempotency_key` violates the length bound, THEN THE Tool_Wrapper SHALL raise a `ToolError` with `error_type="validation_error"` before invoking the underlying tool.
4. WHERE the input model belongs to a non-destructive tool, THE Pydantic input model SHALL not declare an `idempotency_key` field.

### Requirement 14: Idempotency execution semantics

**User Story:** As a network-flaky client, I want repeated calls with the same idempotency key to execute exactly once, so that retries are safe.

#### Acceptance Criteria

1. WHEN the Tool_Wrapper observes a non-empty `idempotency_key` for a Destructive_Tool, THE Tool_Wrapper SHALL look up the key in the Idempotency_Store before invoking the underlying tool.
2. IF the lookup returns an Idempotency_Entry whose `status` equals `"completed"` and whose `expires_at` is in the future, THEN THE Tool_Wrapper SHALL return the cached `result_json` and SHALL set the Outcome_Label to `"cached"`.
3. IF the lookup returns an Idempotency_Entry whose `status` equals `"in_progress"` and whose `expires_at` is in the future, THEN THE Tool_Wrapper SHALL raise a `ToolError` with `error_type="rate_limited"` and `message="operation_in_progress"`.
4. WHEN the lookup returns no entry or returns an entry whose `expires_at` is in the past, THE Tool_Wrapper SHALL atomically insert a new Idempotency_Entry with `status="in_progress"` and then invoke the underlying tool.
5. WHEN the underlying tool returns a successful result, THE Tool_Wrapper SHALL update the Idempotency_Entry to `status="completed"` and store the serialized result in `result_json`.
6. WHEN the underlying tool raises a `ToolError`, THE Tool_Wrapper SHALL update the Idempotency_Entry to `status="error"` and store the serialized error payload in `error_json`.
7. WHEN two concurrent invocations supply the same `idempotency_key` for the same Destructive_Tool, THE Idempotency_Store SHALL guarantee that exactly one invocation transitions the entry from absent to `"in_progress"`; every other concurrent caller SHALL observe `"in_progress"` or `"completed"` and SHALL behave per criteria 2 or 3.

### Requirement 15: Idempotency TTL and cleanup

**User Story:** As an operator, I want stale idempotency entries to expire automatically, so that the store does not grow without bound.

#### Acceptance Criteria

1. WHEN an Idempotency_Entry is created or updated, THE Idempotency_Store SHALL set `expires_at` to `created_at + INSTAGRAM_MCP_IDEMPOTENCY_TTL_HOURS * 3600`.
2. WHEN `INSTAGRAM_MCP_IDEMPOTENCY_TTL_HOURS` is unset, THE Idempotency_Store SHALL use the default value `24`.
3. THE Idempotency_Cleanup_Loop SHALL run as an asyncio task started during server lifespan and SHALL invoke `cleanup_expired()` every 60 seconds.
4. WHEN the Idempotency_Cleanup_Loop is cancelled during server shutdown, THE Idempotency_Cleanup_Loop SHALL exit within 3 seconds.
5. WHEN `cleanup_expired()` runs, THE Idempotency_Store SHALL delete every entry whose `expires_at` is in the past and SHALL return the count of deleted rows.

### Requirement 16: Idempotency database location

**User Story:** As an operator, I want a predictable on-disk location for the idempotency database, so that I can back it up and audit it.

#### Acceptance Criteria

1. THE Idempotency_Store SHALL persist to the SQLite file resolved from `INSTAGRAM_MCP_STATE_DIR` (default `.state`) joined with the literal filename `idempotency.db`.
2. WHEN the parent directory does not exist at startup, THE Idempotency_Store SHALL create it with mode `0o700` on POSIX systems.
3. THE Idempotency_Store SHALL open the SQLite database with `journal_mode=WAL` and `synchronous=NORMAL`.
4. THE Idempotency_Store SHALL declare its table schema with at least the columns `key TEXT PRIMARY KEY`, `tool TEXT NOT NULL`, `status TEXT NOT NULL`, `result_json TEXT`, `error_json TEXT`, `created_at INTEGER NOT NULL`, `expires_at INTEGER NOT NULL`.

### Requirement 17: Idempotency property guarantees

**User Story:** As a quality engineer, I want a property test that proves dedup, so that regression is caught automatically.

#### Acceptance Criteria

1. THE test suite SHALL contain a property-based test that issues a randomized number `N` (`2 <= N <= 64`) of concurrent invocations of a stubbed Destructive_Tool with the same `idempotency_key`.
2. FOR ALL such randomized executions, the property test SHALL assert that the underlying tool body executes exactly once.
3. FOR ALL such randomized executions, the property test SHALL assert that every concurrent caller observes either the cached result or the `"operation_in_progress"` `ToolError`.

## Yo'nalish 4 — Persistent State

### Requirement 18: State store module foundation

**User Story:** As an operator, I want one place where runtime state is persisted, so that restarts do not erase what the server has learned about its environment.

#### Acceptance Criteria

1. THE Observability_Stack SHALL expose a module at the import path `instagram_mcp.state_store`.
2. THE `instagram_mcp.state_store` module SHALL provide a class `StateStore` whose constructor accepts a `db_path: pathlib.Path` and a `flush_interval_seconds: int`.
3. THE `StateStore` class SHALL provide async methods `load() -> StateSnapshot`, `save(snapshot: StateSnapshot) -> None`, `start_flush_loop(snapshot_provider: Callable[[], StateSnapshot]) -> None`, `stop_flush_loop() -> None`.
4. WHEN `INSTAGRAM_MCP_STATE_DISABLED` is set to `1` or `true` or the Observability_Kill_Switch is enabled, THE `StateStore` SHALL behave as a No_Op_Shim where `load` returns an empty State_Snapshot, `save` is a no-op, and the flush loop is never started.

### Requirement 19: Persisted snapshot contents

**User Story:** As an SRE, I want the persistent snapshot to cover the three components that suffer most from amnesia, so that proxy bans, rate limits, and account cooldowns survive restarts.

#### Acceptance Criteria

1. THE State_Snapshot SHALL include for every proxy URL the fields `proxy_url`, `cb_state`, `cb_until_epoch`, `consecutive_failures`, `total_requests`, `total_failures`.
2. THE State_Snapshot SHALL include for the global adaptive rate limiter the fields `current_rps`, `max_rate`, `consecutive_429s`, `consecutive_successes`.
3. THE State_Snapshot SHALL include for every per-proxy rate limiter the fields `proxy_url`, `current_rps`, `max_rate`.
4. THE State_Snapshot SHALL include for every account in the pool the fields `alias`, `status`, `cooldown_until_epoch`, `consecutive_failures`.
5. THE State_Snapshot SHALL not include any Sensitive_Material such as cookies bytes, OAuth tokens, or session IDs.

### Requirement 20: Periodic flush

**User Story:** As an operator, I want state to be flushed on a fixed cadence and on graceful shutdown, so that I lose at most one flush window of progress.

#### Acceptance Criteria

1. WHILE the State_Store is enabled, THE State_Store SHALL invoke `save(snapshot_provider())` every State_Flush_Interval seconds.
2. WHEN `INSTAGRAM_MCP_STATE_FLUSH_SECONDS` is unset, THE State_Flush_Interval SHALL default to `30` seconds.
3. WHEN the Instagram_MCP_Server enters its lifespan shutdown phase, THE State_Store SHALL invoke `save(snapshot_provider())` one final time before the SQLite connection is closed.
4. IF a flush attempt raises an exception, THEN THE State_Store SHALL log the failure at WARNING level and retry on the next interval without crashing the server.

### Requirement 21: Startup recovery

**User Story:** As a freshly restarted server, I want to resume from the last persisted state, so that I do not re-burn proxies and accounts that were cooling down.

#### Acceptance Criteria

1. WHEN the Instagram_MCP_Server starts and the State_Store is enabled, THE State_Store SHALL invoke `load()` before any proxy, rate limiter, or account pool component begins serving traffic.
2. WHEN `load()` returns a non-empty State_Snapshot, THE `ProxyManager` SHALL restore each proxy's `cb_state` and `cb_until_epoch`.
3. WHEN `load()` returns a non-empty State_Snapshot, THE adaptive rate limiter SHALL restore its `current_rps` and `max_rate` clamped into `[min_rate, base_rate * 2.5]`.
4. WHEN `load()` returns a non-empty State_Snapshot, THE account pool component SHALL restore each member's `status` and `cooldown_until_epoch`.
5. WHEN a restored `cb_until_epoch` or `cooldown_until_epoch` is in the past relative to current wall-clock time, THE owning component SHALL treat the cooldown as already expired and transition the entity to its baseline state.

### Requirement 22: Schema versioning and corruption handling

**User Story:** As a maintainer evolving the schema, I want explicit version handling, so that an old SQLite file does not crash a new server build.

#### Acceptance Criteria

1. THE State_Store SHALL maintain a `metadata` table containing a single row with the column `schema_version INTEGER NOT NULL`.
2. WHEN the State_Store opens an existing database whose `schema_version` is less than the current build's version, THE State_Store SHALL run forward migrations registered in code.
3. WHEN the State_Store opens an existing database whose `schema_version` is greater than the current build's version, THE State_Store SHALL enter Degraded_Fallback mode and log an ERROR line.
4. IF the State_Store cannot open the database file because it is missing, locked, or corrupt, THEN THE State_Store SHALL enter Degraded_Fallback mode, log a WARNING line including the failure reason, and continue server startup.
5. WHILE in Degraded_Fallback mode, THE State_Store SHALL accept `save()` calls as no-ops and SHALL return an empty State_Snapshot from `load()`.

### Requirement 23: State store property guarantees

**User Story:** As a quality engineer, I want a property test that proves crash recovery, so that the state machine survives unexpected termination.

#### Acceptance Criteria

1. THE test suite SHALL contain a property-based test that generates a randomized State_Snapshot, writes it via `save()`, simulates a process kill by closing the database connection without flushing pending in-memory writes, reopens a fresh State_Store on the same file, and reads the snapshot via `load()`.
2. FOR ALL such randomized executions, the property test SHALL assert that the loaded snapshot equals the most recently saved snapshot.

## Yo'nalish 5 — Tracing & Health

### Requirement 24: OpenTelemetry tracing module

**User Story:** As a developer using a tracing backend, I want OTLP-compatible spans, so that I can plug the server into Tempo, Jaeger, or another OTLP collector.

#### Acceptance Criteria

1. THE Observability_Stack SHALL expose a module at the import path `instagram_mcp.tracing`.
2. WHEN `OTEL_EXPORTER_OTLP_ENDPOINT` is unset or empty, THE `instagram_mcp.tracing` module SHALL not configure any Tracer_Provider and every span helper SHALL behave as a No_Op_Shim.
3. WHEN `OTEL_EXPORTER_OTLP_ENDPOINT` is set and the Observability_Kill_Switch is disabled, THE `instagram_mcp.tracing` module SHALL configure a Tracer_Provider whose resource includes the attribute `service.name` resolved from `OTEL_SERVICE_NAME` (default `"instagram-mcp"`).
4. WHEN `OTEL_TRACES_SAMPLER` is set, THE `instagram_mcp.tracing` module SHALL configure the corresponding sampler from the OpenTelemetry SDK; if unset, the default `parentbased_always_on` sampler SHALL be used.
5. WHEN any of the optional OpenTelemetry packages is not installed, THE `instagram_mcp.tracing` module SHALL fall back to No_Op_Shim implementations and SHALL log a single INFO line indicating that tracing is disabled.

### Requirement 25: Per-tool root span

**User Story:** As a developer, I want one root span per tool call, so that the entire request appears as a single trace.

#### Acceptance Criteria

1. WHEN the Tool_Wrapper begins processing a tool call and tracing is enabled, THE Tool_Wrapper SHALL start a Tool_Span named `tool.<tool_name>` with `kind=SERVER`.
2. THE Tool_Wrapper SHALL set the following attributes on the Tool_Span: `instagram_mcp.tool` = tool name, `instagram_mcp.toolset` = toolset name, `instagram_mcp.auth_tier` = Auth_Tier value, `instagram_mcp.correlation_id` = active Correlation_ID.
3. WHEN the underlying tool returns successfully, THE Tool_Wrapper SHALL set the Tool_Span status to `OK` and SHALL set the attribute `instagram_mcp.outcome` to `"success"` or `"cached"`.
4. WHEN the underlying tool raises a `ToolError`, THE Tool_Wrapper SHALL set the Tool_Span status to `ERROR`, SHALL set the attribute `instagram_mcp.outcome` to `"error"`, and SHALL set `instagram_mcp.error_type` to the `error_type` field of the `ToolError`.
5. THE Tool_Span attributes SHALL contain no Sensitive_Material.

### Requirement 26: Nested spans

**User Story:** As a performance engineer, I want nested spans for the work that happens inside one tool, so that I can see which subsystem dominates latency.

#### Acceptance Criteria

1. WHEN the `InstagramClient` issues an HTTP request and tracing is enabled, THE `InstagramClient` SHALL open a Nested_Span named `http.fetch` with `kind=CLIENT` whose attributes include `http.method`, `http.url`, `proxy.id`.
2. WHEN the `ProxyManager` retries a request through a different proxy, THE `ProxyManager` SHALL open a Nested_Span named `proxy.retry` whose attributes include `proxy.id` and `proxy.attempt`.
3. WHEN the `SmartCache` performs a `get` or `set` and tracing is enabled, THE `SmartCache` SHALL open a Nested_Span named `cache.get` or `cache.set` respectively whose attributes include `cache.key_hash` and `cache.outcome`.
4. THE `cache.key_hash` attribute SHALL contain a stable hash of the cache key rather than the raw key, so that no Sensitive_Material is leaked.

### Requirement 27: Health endpoints module

**User Story:** As a Kubernetes operator, I want standard health endpoints, so that I can configure liveness and readiness probes.

#### Acceptance Criteria

1. WHILE `INSTAGRAM_MCP_TRANSPORT=http` and the Observability_Kill_Switch is disabled and `INSTAGRAM_MCP_HEALTH_DISABLED` is not set to `1` or `true`, THE Instagram_MCP_Server SHALL expose Health_Probe routes `GET /healthz` and `GET /readyz`.
2. WHILE `INSTAGRAM_MCP_TRANSPORT` is anything other than `"http"`, THE Instagram_MCP_Server SHALL skip starting the Health_Probe routes.
3. WHEN `INSTAGRAM_MCP_HEALTH_DISABLED` is set to `1` or `true`, THE Instagram_MCP_Server SHALL skip starting the Health_Probe routes regardless of transport.

### Requirement 28: Liveness semantics

**User Story:** As a Kubernetes operator, I want `/healthz` to return 200 whenever the process is alive, so that I do not restart healthy pods.

#### Acceptance Criteria

1. WHEN the Liveness_Probe receives a request, THE Liveness_Probe SHALL respond with HTTP status `200` and JSON body `{"status": "ok"}` within 100 milliseconds.
2. THE Liveness_Probe SHALL not depend on the State_Store, the proxy pool, or external network connectivity.

### Requirement 29: Readiness semantics

**User Story:** As a Kubernetes operator, I want `/readyz` to return 503 when the server cannot serve traffic, so that traffic is shifted away during transient outages.

#### Acceptance Criteria

1. WHEN the Readiness_Probe receives a request, THE Readiness_Probe SHALL evaluate three checks: cookies presence (only when at least one auth-tier tool is registered), at least one proxy in `CLOSED` state (only when `MCPConfig.proxy_urls` is non-empty), and write-access to the State_Store database file (only when the State_Store is enabled).
2. WHEN every applicable check passes, THE Readiness_Probe SHALL respond with HTTP status `200` and a JSON body listing each check name and its boolean result.
3. WHEN any applicable check fails, THE Readiness_Probe SHALL respond with HTTP status `503` and a JSON body listing each check name, its boolean result, and a short failure reason for failed checks.
4. THE Readiness_Probe response body SHALL contain no Sensitive_Material.
5. THE Readiness_Probe SHALL complete its evaluation within 500 milliseconds end-to-end.

## Non-Functional Requirements

### Requirement 30: Performance budget

**User Story:** As a maintainer, I want metric and span emission to add negligible overhead, so that observability does not become a bottleneck.

#### Acceptance Criteria

1. THE Tool_Wrapper SHALL add no more than `5` microseconds at p99 to a tool call for metric emission alone, measured on the project's CI hardware with the Observability_Stack enabled and tracing disabled.
2. WHEN tracing is enabled with a no-op exporter, THE Tool_Wrapper SHALL add no more than `100` microseconds at p99 to a tool call.
3. THE State_Store flush SHALL complete within `50` milliseconds at p95 for a snapshot containing up to 100 proxies and 100 accounts.

### Requirement 31: Graceful degradation

**User Story:** As a user who installed `instagram-mcp` without the `observability` extra, I want the server to keep working, so that I do not have to install dependencies I do not need.

#### Acceptance Criteria

1. WHEN any package listed in Optional_Extras is missing, THE Observability_Stack SHALL load the corresponding No_Op_Shim and the Instagram_MCP_Server SHALL continue startup without error.
2. WHEN the Observability_Kill_Switch is enabled, THE Instagram_MCP_Server SHALL behave identically to a build where Optional_Extras are not installed.
3. WHEN any Observability_Stack module raises an unexpected exception during startup, THE Instagram_MCP_Server SHALL log the failure at ERROR level, disable the failing module, and continue startup.

### Requirement 32: Resource budget

**User Story:** As an operator with limited disk, I want hard caps on the on-disk footprint, so that observability cannot fill the volume.

#### Acceptance Criteria

1. THE Idempotency_Store SHALL not grow beyond `50` megabytes on disk under normal operation; when the store size exceeds this bound, the Idempotency_Cleanup_Loop SHALL evict the oldest expired entries until the size returns under the bound.
2. THE State_Store SHALL not grow beyond `100` megabytes on disk under normal operation; when the store size exceeds this bound, the State_Store SHALL run `VACUUM` and SHALL log a WARNING line if the size remains above the bound.
3. THE Idempotency_Store and the State_Store SHALL each fit in a single SQLite file and SHALL not require external services.

### Requirement 33: Backwards compatibility

**User Story:** As an existing v0 user, I want zero behaviour change when I do not opt in, so that upgrades are safe.

#### Acceptance Criteria

1. WHEN the Instagram_MCP_Server starts with no `INSTAGRAM_MCP_*` observability env var set and no `OTEL_*` env var set, THE Instagram_MCP_Server SHALL produce the same set of registered tools, the same MCP resource URIs, and the same MCP prompt names as the pre-feature build.
2. THE `MCPConfig` dataclass field names and default values SHALL remain unchanged by this feature.
3. THE existing 780 tests in the repository SHALL pass without modification when the Observability_Stack is enabled with default settings.
4. THE Public_API_Surface SHALL not be expanded or narrowed by this feature except for the new env vars listed in Requirements 1 through 29.

### Requirement 34: Security posture

**User Story:** As a security engineer, I want strong guarantees that observability does not become a leak, so that I can approve the feature for production.

#### Acceptance Criteria

1. THE Correlation_ID generator SHALL use `uuid.uuid4()` and SHALL not include any user input, account credentials, cookies, or tokens.
2. THE Idempotency_Store and the State_Store SHALL be created with file mode `0o600` on POSIX systems.
3. THE State_Store SHALL not persist cookies bytes, OAuth tokens, session IDs, or raw `Cookie` headers in any column.
4. THE Metrics_Endpoint, the Pushgateway_Exporter, and the Health_Probe routes SHALL not expose any tool argument values, account credentials, or response bodies.

### Requirement 35: Test coverage

**User Story:** As a maintainer, I want the new modules to ship with unit tests, smoke tests, and property tests, so that regressions are caught early.

#### Acceptance Criteria

1. THE test suite SHALL include unit tests for `instagram_mcp.metrics`, `instagram_mcp.log_config`, `instagram_mcp.idempotency`, `instagram_mcp.state_store`, and `instagram_mcp.tracing`.
2. THE test suite SHALL include a smoke test that issues `GET /metrics` against a live HTTP-transport server and asserts the response body parses as Prometheus text exposition format.
3. THE test suite SHALL include a smoke test that issues `GET /healthz` against a live HTTP-transport server and asserts an HTTP `200` response with body `{"status": "ok"}`.
4. THE test suite SHALL include a smoke test that issues `GET /readyz` against a live HTTP-transport server in a deliberately unprepared state and asserts an HTTP `503` response.
5. THE test suite SHALL include the property test described in Requirement 17 (idempotency dedup) and the property test described in Requirement 23 (state store crash recovery).

## Out of Scope

The following items are explicitly out of scope for this feature and will be addressed, if at all, in separate specs:

- Multi-process or distributed tracing aggregation across multiple `instagram-mcp` instances.
- Pre-built Grafana dashboards or alert rules.
- Log shipping integrations such as Loki, Elasticsearch, or Splunk.
- Application Performance Monitoring (APM) integrations and Real User Monitoring (RUM).
- Rotating log files; operators are expected to handle rotation through `logrotate` or an equivalent system tool.
