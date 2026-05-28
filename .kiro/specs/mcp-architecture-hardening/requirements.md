# Requirements Document

## Introduction

`instagram-mcp` loyihasi hozirda 79 ta MCP tool'ini bitta `instagram_mcp/tools.py` faylida (~5500 satr) saqlaydi, server `instructions` matni va `__init__.py` docstring'i qo'lda yozilgan eskirgan inventarni ko'rsatadi (12/19/11/8), README esa boshqa raqamlarni (79/30/48/1) keltiradi. Repo'da ishchi nusxasida cookies va PII fayllar (`cookie.txt`, `cookies.json`, `cookies.txt`, `data/cookies.json`, `exports/`, `data/media_cache/`) yotibdi va `MagicMock/mock.accounts_dir/` ostida 2247 ta bo'sh papka mavjud (test ishlay turib `MagicMock` obyektini path argumenti sifatida ishlatib qo'ygan natija). Bu hujjat ushbu arxitektura, hujjatlash, gigiyena, xavfsizlik va MCP-konformlik muammolarini yechish uchun talablarni belgilaydi.

Bu feature beshta yo'nalishni qamraydi:

1. **Refactor** — `tools.py` ni domain bo'yicha sub-package'larga bo'lish (Requirement 1–5).
2. **Documentation Sync** — `instructions`, `__init__.py` docstring va README tool inventarining haqiqiy registratsiyaga mos kelishini majburiy qilish (Requirement 6–10).
3. **Repo Hygiene** — `MagicMock/`, cookies fayllari, `exports/`, `data/media_cache/` ni working copy'dan tozalash va `.gitignore`'ga qattiq qo'shish (Requirement 11–13).
4. **Security Hardening** — `SECURITY.md`, pre-commit secret-scan hook, git history tozalash playbook'i (Requirement 14–16).
5. **MCP Conformance** — tool annotation auditi, `ToolError` taksonomiyasi, README'da Resources va Prompts bo'limlari (Requirement 17–19).

Non-functional talablar (performance, backwards compatibility, security posture, curl_cffi pattern, Python versiyasi, test suite) Requirement 20–26 da bayon qilinadi.

Public API (tool nomlari, `MCPConfig` dataclass, env var nomlari, MCP resources URI'lari, prompts nomlari) o'zgarishsiz qoladi. `curl_cffi` Chrome 142 impersonation pattern'i o'zgarishsiz qoladi.

## Glossary

- **Instagram_MCP_Server**: `create_mcp_server()` factory tomonidan yaratilgan `FastMCP` instansi va uning barcha komponentlari (client, cache, proxy_manager, rate_limiter, tools, resources, prompts).
- **Tools_Package**: `instagram_mcp/tools/` Python package'i (rejalashtirilayotgan), domain bo'yicha bo'lingan submodullar to'plami.
- **Tool_Orchestrator**: `instagram_mcp/tools/__init__.py` ichidagi `register_tools(mcp, client, config, exporter)` funksiyasi; har bir submodulning `register_<group>` registrar'ini chaqiradi.
- **Submodule_Registrar**: har bir domain submodulida e'lon qilingan `register_<group>(mcp, client, config, exporter) -> list[ToolDescriptor]` funksiyasi.
- **Toolset**: bitta submodulga to'g'ri keladigan tool guruhi nomi. To'liq ro'yxat: `profile`, `analysis`, `content`, `social_graph`, `dm`, `upload`, `automation`, `server`.
- **Auth_Tier**: har bir tool uchun avtorizatsiya darajasi belgisi:
  - **🌐 Anonymous**: cookies talab qilinmaydi.
  - **🔐 Authenticated**: `cookies.json`/`cookies.txt` talab qilinadi.
  - **🌐/🔐 Auto-mode**: anonymous ishlaydi, cookies bo'lsa yuqori darajaga o'tadi.
- **Tool_Inventory**: registratsiyadan keyin Tool_Orchestrator tomonidan qurilgan ro'yxat: har bir element `name`, `toolset`, `auth_tier`, `annotations`, `input_model`, `description_first_line` maydonlarini saqlaydi.
- **Server_Instructions**: `FastMCP(... instructions=...)` ga uzatiladigan matn; LLM-larga server qobiliyatlarini tasvirlaydi.
- **Tool_Annotations**: `readOnlyHint`, `idempotentHint`, `destructiveHint`, `openWorldHint`, `title` maydonlari (MCP standartiga muvofiq).
- **Destructive_Operation**: Instagram tomonida holat o'zgartiruvchi yozish operatsiyasi (DM yuborish, post like, foydalanuvchini follow qilish, upload, delete, comment, block, profile edit, story publish, schedule modify).
- **ToolError_Taxonomy**: `ToolError` `error_type` maydoni uchun yagona ruxsat etilgan qiymatlar to'plami: `validation_error`, `not_found`, `private_account`, `auth_required`, `rate_limited`, `network_error`, `fetch_error`, `unexpected_error`.
- **MCPConfig**: `instagram_mcp/config.py` ichidagi konfiguratsiya dataclass'i (env-driven).
- **Tool_Gating**: `MCPConfig.enabled_toolsets` va `MCPConfig.hide_auth_when_no_cookies` orqali registratsiya paytida tool'larni filtr qilish mexanizmi.
- **Pydantic_Input_Model**: har bir tool argumentlari uchun e'lon qilingan `pydantic.BaseModel` (`instagram_mcp/models.py`).
- **Repo_Hygiene_Guard**: sensitiv fayllar va artefaktlarning working copy'ga tushishini oldini oluvchi mexanizm to'plami: `.gitignore` qoidalari, pre-commit hook, runtime guard'lar.
- **Path_Argument_Guard**: runtime kodida path argumentini qabul qiladigan funksiyalarda `isinstance(value, (str, pathlib.PurePath))` ga teng tekshirish.
- **Working_Copy**: git working tree'idagi (commit qilinmagan ham) fayllar.
- **Secret_Scan_Hook**: pre-commit yoki CI bosqichi; cookies/secret fayllarning index'ga qo'shilishini bloklaydi.
- **Git_History_Cleanup_Playbook**: `SECURITY.md` ichidagi maintainer uchun yo'riqnoma: BFG Repo-Cleaner yoki `git filter-repo` bilan tarixdan secret olib tashlash, force-push, kollaboratorlar re-clone, Instagram sessiyasini Settings > Login Activity orqali invalidate qilish.
- **Public_API_Surface**: tashqi iste'molchilarga ko'rinadigan kontrakt: tool nomlari, `MCPConfig` dataclass field nomlari va default qiymatlari, `INSTAGRAM_MCP_*` env var nomlari, MCP resource URI shablonlari, MCP prompt nomlari.
- **Server_Bootstrap**: `instagram_mcp/__init__.py` ichidagi `create_mcp_server()` funksiyasi va u chaqiradigan `_register_resources`, `_register_prompts` yordamchilari.
- **MCP_Tool_Annotation_Audit**: `register_tools` ishi tugagandan keyin har bir registratsiya qilingan tool annotation'larini Destructive_Operation ro'yxatiga nisbatan tekshiruvchi runtime/test bosqichi.

## Requirements

## Yo'nalish 1 — Refactor (Tool Package Split)

### Requirement 1: Tools package strukturasi

**User Story:** As a maintainer, I want the 5500-line `tools.py` split into domain submodules, so that each file is small enough to read, review, and test independently.

#### Acceptance Criteria

1. THE Tools_Package SHALL be a Python package located at `instagram_mcp/tools/` with an `__init__.py`.
2. THE Tools_Package SHALL contain exactly the following submodules: `profile.py`, `analysis.py`, `content.py`, `social_graph.py`, `dm.py`, `upload.py`, `automation.py`, `server.py`.
3. THE Tools_Package SHALL contain a shared helpers module `_helpers.py` holding `sanitize_username`, `_tool_error`, `_exception_to_tool_error`, `_paginate_feed`, and any helper used by two or more submodules.
4. WHEN the refactor is complete, THE old file `instagram_mcp/tools.py` SHALL no longer exist in the repository.
5. WHERE an external caller writes `from instagram_mcp.tools import register_tools`, THE Tools_Package SHALL expose the symbol `register_tools` with the call signature `(mcp, client, config, exporter) -> None`.
6. WHERE an external caller writes `from instagram_mcp.tools import sanitize_username`, THE Tools_Package SHALL re-export the symbol from `_helpers.py` for backwards compatibility.

### Requirement 2: Submodule contract

**User Story:** As a contributor, I want each submodule to follow a uniform registrar contract, so that adding or removing a tool group requires only one entry in the orchestrator.

#### Acceptance Criteria

1. THE Submodule_Registrar SHALL be exposed as a top-level function named `register_<toolset>` where `<toolset>` matches the submodule's filename without extension.
2. THE Submodule_Registrar SHALL accept exactly four positional parameters: `mcp: FastMCP`, `client: InstagramClient`, `config: MCPConfig`, `exporter: JsonExporter`.
3. THE Submodule_Registrar SHALL return a value of type `list[ToolDescriptor]` describing each tool the submodule registered, where `ToolDescriptor` is a typed structure with at least the fields `name: str`, `toolset: str`, `auth_tier: Literal["anon","auth","auto"]`, `annotations: dict`, `input_model: type[BaseModel]`.
4. WHERE a submodule registers no tools at runtime because of Tool_Gating, THE Submodule_Registrar SHALL return an empty list.
5. THE Tools_Package submodule SHALL declare a module-level constant `TOOLSET_NAME: str` equal to its `<toolset>` identifier.

### Requirement 3: Tool Orchestrator behaviour

**User Story:** As an integrator, I want `register_tools` to be a thin orchestrator, so that responsibility for individual tools lives in the relevant submodule.

#### Acceptance Criteria

1. THE Tool_Orchestrator SHALL invoke each Submodule_Registrar in the canonical order `profile`, `analysis`, `content`, `social_graph`, `dm`, `upload`, `automation`, `server`.
2. THE Tool_Orchestrator SHALL collect every returned `ToolDescriptor` into a single Tool_Inventory list.
3. THE Tool_Orchestrator SHALL store the resulting Tool_Inventory on the `mcp` object as the attribute `mcp._instagram_tool_inventory` so that the Server_Bootstrap layer can read it.
4. THE Tool_Orchestrator SHALL not contain any inline `@mcp.tool` declarations after the refactor.
5. WHEN the Tool_Orchestrator finishes, THE Tool_Orchestrator SHALL log at INFO level the total number of registered tools and a per-toolset count.

### Requirement 4: Tool gating preservation

**User Story:** As an operator, I want existing toolset gating to keep working after the split, so that `INSTAGRAM_MCP_TOOLSETS` and `INSTAGRAM_MCP_HIDE_AUTH_WHEN_NO_COOKIES` behave exactly as before.

#### Acceptance Criteria

1. WHERE `MCPConfig.enabled_toolsets` contains `"all"` or is empty, THE Tool_Orchestrator SHALL invoke every Submodule_Registrar.
2. WHERE `MCPConfig.enabled_toolsets` is a non-empty set NOT containing `"all"`, THE Tool_Orchestrator SHALL invoke only the Submodule_Registrars whose toolset name appears in the set, plus the `server` submodule.
3. THE Tool_Orchestrator SHALL invoke the `server` Submodule_Registrar regardless of `MCPConfig.enabled_toolsets` content.
4. IF the `server` Submodule_Registrar raises an exception, THEN THE Tool_Orchestrator SHALL log the failure at ERROR level and continue invoking the remaining Submodule_Registrars in degraded mode.
5. WHILE `MCPConfig.hide_auth_when_no_cookies` is true and `client.cookie_manager.is_authenticated` is false, THE Submodule_Registrar SHALL skip registration of any tool whose Auth_Tier is `auth`.
6. WHILE `MCPConfig.hide_auth_when_no_cookies` is true and `client.cookie_manager.is_authenticated` is false, THE Submodule_Registrar SHALL still register tools whose Auth_Tier is `anon` or `auto`.

### Requirement 5: Public API stability after refactor

**User Story:** As a downstream user, I want every existing tool name and signature preserved, so that my Claude Desktop or Claude Code config does not break.

#### Acceptance Criteria

1. THE Tools_Package SHALL register each tool under exactly the same `name` string as the pre-refactor `tools.py` (for example `instagram_profile`, `instagram_dm_send`, `instagram_batch_scrape`).
2. THE Pydantic_Input_Model class used by each refactored tool SHALL be the same class (same module path, same field names, same defaults) as before.
3. THE return contract (text or markdown body, JSON export side-effect) of each refactored tool SHALL be functionally equivalent to the pre-refactor version.
4. THE `MCPConfig` dataclass field names and default values SHALL remain unchanged.
5. THE `INSTAGRAM_MCP_*` environment variable names recognised by `MCPConfig.from_env()` SHALL remain unchanged.
6. THE MCP resource URI templates `instagram://profile/{username}`, `instagram://feed/{username}`, `instagram://server/status` SHALL remain unchanged.
7. THE MCP prompt names `analyze_influencer`, `find_brand_collaborations`, `competitive_analysis`, `account_audit` and any other already-registered prompt SHALL remain unchanged.

## Yo'nalish 2 — Documentation Sync

### Requirement 6: Generated server instructions

**User Story:** As an LLM client, I want the server `instructions` text to match the actually registered tools, so that I never see ghost tools or outdated counts.

#### Acceptance Criteria

1. THE Server_Bootstrap SHALL build the `FastMCP` `instructions` string from the Tool_Inventory after `register_tools` has run.
2. THE Server_Instructions SHALL group entries by Toolset in the canonical order `profile`, `analysis`, `content`, `social_graph`, `dm`, `upload`, `automation`, `server`.
3. THE Server_Instructions SHALL prefix each tool entry with its Auth_Tier badge: `🌐` for `anon`, `🔐` for `auth`, `🌐/🔐` for `auto`.
4. THE Server_Instructions SHALL display the actual total count of registered tools.
5. THE Server_Instructions SHALL display per-toolset and per-Auth_Tier counts derived from the Tool_Inventory.
6. WHEN tools are skipped because of Tool_Gating OR when the Tool_Inventory is empty, THE Server_Instructions SHALL still be generated and SHALL show zero counts and an empty tool list rather than an error.
7. THE Server_Instructions SHALL include a line stating which authentication mode the server is currently running in (the literal string `authenticated` or `anonymous (no cookies.txt)`).

### Requirement 7: Module docstring inventory

**User Story:** As a code reader, I want `__init__.py` and `tools/__init__.py` docstrings to never lie about tool counts, so that documentation drift cannot happen silently.

#### Acceptance Criteria

1. THE module-level docstring of `instagram_mcp/__init__.py` SHALL not contain any hard-coded tool count number such as "12 tools", "19 tools", "11 anonymous", or "8 auth".
2. WHERE the `instagram_mcp/__init__.py` docstring describes the architecture, THE docstring SHALL refer to the runtime-generated Tool_Inventory and the README tool table as the authoritative sources.
3. THE module-level docstring of `instagram_mcp/tools/__init__.py` SHALL not contain hard-coded per-tool descriptions; instead the docstring SHALL document the orchestrator contract and point to the per-submodule docstrings.
4. THE pre-refactor "AUTH TIERS" comment block at the top of the old `tools.py` SHALL not be carried into the new package as a numeric inventory.

### Requirement 8: Per-tool docstring tier marker

**User Story:** As an LLM agent, I want every tool docstring to start with its auth tier, so that I can choose tools without reading further.

#### Acceptance Criteria

1. WHEN a tool is registered, THE tool docstring SHALL begin (after stripping leading whitespace) with one of the three Auth_Tier markers: `🌐`, `🔐`, or `🌐/🔐`.
2. IF a tool's docstring does not begin with one of the three Auth_Tier markers, THEN THE registration SHALL fail at server startup with an error message naming the offending tool.
3. THE tool's declared Auth_Tier in its `ToolDescriptor` SHALL match the marker present at the start of the docstring.

### Requirement 9: Docstring vs Pydantic model parity test

**User Story:** As a contributor, I want a CI test that fails when a tool's documented parameters disagree with its Pydantic input model, so that documentation drift is caught at review time.

#### Acceptance Criteria

1. THE Tools_Package SHALL ship a test (under `tests/test_tool_docs.py`) that iterates every tool in the Tool_Inventory.
2. WHEN the test runs, THE test SHALL verify that every public field name of the tool's Pydantic_Input_Model is mentioned in the tool's docstring.
3. WHEN the test runs, THE test SHALL verify that no parameter names appear in the docstring that are absent from the Pydantic_Input_Model field set.
4. IF either parity check fails, THEN THE test SHALL report the tool name, the missing parameters, and the extraneous parameters in its failure message.

### Requirement 10: README and pyproject sync

**User Story:** As a reader of `pyproject.toml` and `README.md`, I want the tool-count claims to be accurate, so that I do not lose trust in the documentation.

#### Acceptance Criteria

1. THE `pyproject.toml` `description` field SHALL not contain a tool count that contradicts the Tool_Inventory.
2. THE `README.md` Auth Tiers table counts SHALL match the per-Auth_Tier counts produced by Tool_Inventory.
3. THE Tools_Package SHALL ship a test that loads the README, parses the tool table, and asserts every tool listed in README appears in Tool_Inventory and vice versa.
4. IF a tool exists in README but not in Tool_Inventory, OR exists in Tool_Inventory but not in README, THEN THE test SHALL fail with the offending tool name.

## Yo'nalish 3 — Repo Hygiene

### Requirement 11: MagicMock directory cleanup and guard

**User Story:** As a maintainer, I want the 2247 `MagicMock/mock.accounts_dir/*` empty directories removed and prevented from coming back, so that the repo size and `git status` output stay clean.

#### Acceptance Criteria

1. THE Repo_Hygiene_Guard SHALL remove the `MagicMock/` directory tree from the Working_Copy.
2. THE `.gitignore` file SHALL include the entry `MagicMock/`.
3. THE Path_Argument_Guard SHALL be added to every code path that accepts a directory or file path argument and uses it for filesystem operations, including the `AccountPool` constructor, the `MediaCache` constructor, every `accounts_dir` consumer, and every `media_cache_dir` consumer.
4. IF a Path_Argument_Guard receives a value that is not an instance of `str`, `bytes`, or `pathlib.PurePath`, THEN THE guard SHALL raise `TypeError` with a message identifying the offending parameter name and the received type.
5. THE Tools_Package SHALL ship a regression test that constructs the relevant components with a `unittest.mock.MagicMock` path argument and asserts `TypeError` is raised before any filesystem call.

### Requirement 12: Cookies and PII files in working copy

**User Story:** As an owner of the repo, I want all known cookie and PII artefacts removed from the working copy and blocked from being added back, so that I do not accidentally commit credentials.

#### Acceptance Criteria

1. THE Repo_Hygiene_Guard SHALL remove from the Working_Copy the files `cookie.txt`, `cookies.json`, `cookies.txt`, and `data/cookies.json`.
2. THE Repo_Hygiene_Guard SHALL not place removed files into a git commit; removal SHALL happen at the Working_Copy level only.
3. THE `.gitignore` file SHALL contain explicit, non-negated entries for `cookie.txt`, `cookies.json`, `cookies.txt`, `data/cookies.json`, `**/cookies.json`, and `**/cookies.txt`.
4. THE `.gitignore` file SHALL contain explicit entries for `exports/`, `data/media_cache/`, `MagicMock/`, `*.mcpb`, `dist/`, `.pytest_cache/`, `.state/`, and `.venv/`.
5. THE `.gitignore` file SHALL not contain a top-level `*.json` or `*.txt` blanket rule that requires negations; the rules SHALL be specific enough that committing legitimate JSON or TXT files (for example `manifest.json`, `LICENSE`) does not need a `!` exception.
6. THE existing contents of `exports/` (including `batch_scrape/`, `hashtag/`, `profile/`, `index.json`) SHALL be removed from the Working_Copy.
7. THE existing contents of `data/media_cache/` SHALL be removed from the Working_Copy.

### Requirement 13: Build artefacts and caches

**User Story:** As a contributor cloning the repo fresh, I want build artefacts and tool caches not tracked, so that PRs do not contain noise diffs.

#### Acceptance Criteria

1. THE `.gitignore` file SHALL include entries for `dist/`, `*.mcpb`, `.pytest_cache/`, `.state/`, `.venv/`, `.mypy_cache/`, and `.ruff_cache/`.
2. WHERE a `.mcpb` file (`instagram-mcp.mcpb`) currently exists in the Working_Copy at the repo root, THE Repo_Hygiene_Guard SHALL remove it from the Working_Copy.

## Yo'nalish 4 — Security Hardening

### Requirement 14: SECURITY.md document

**User Story:** As a security-aware user or maintainer, I want a `SECURITY.md` file at the repo root that explains how to disclose vulnerabilities and how to handle leaked secrets, so that incidents have a clear playbook.

#### Acceptance Criteria

1. THE repository SHALL contain a file `SECURITY.md` at the repo root.
2. THE SECURITY.md SHALL contain a section titled "Reporting a Vulnerability" describing the contact channel (email or GitHub Security Advisory) and the expected response time.
3. THE SECURITY.md SHALL contain a section listing every environment variable name considered secret, including `INSTAGRAM_MCP_COOKIES`, `INSTAGRAM_MCP_COOKIES_<ALIAS>`, OAuth-related variables, and any value embedded in `proxies.txt`.
4. THE SECURITY.md SHALL document the recommended cookie storage practice: cookies SHALL be referenced by an absolute filesystem path passed via env var, and cookie contents SHALL not be embedded directly inside an env var value.
5. THE SECURITY.md SHALL document the recommended Docker volume mount as `:ro` (read-only) for cookie files.
6. THE SECURITY.md SHALL contain a section titled "If a secret was committed" containing a step-by-step Git_History_Cleanup_Playbook with these phases: backup the repo and notify collaborators, rotate the leaked secret upstream (for Instagram cookies, invalidate the session in Settings > Login Activity), rewrite history using BFG Repo-Cleaner or `git filter-repo`, force-push the cleaned history, and instruct collaborators to re-clone rather than rebase.
7. THE SECURITY.md SHALL link to the BFG Repo-Cleaner project page and the `git filter-repo` documentation.

### Requirement 15: Pre-commit secret-scan hook

**User Story:** As a developer, I want my local commits to be blocked when they contain known secret-shaped files, so that I cannot accidentally push cookies.

#### Acceptance Criteria

1. THE repository SHALL contain a pre-commit hook configuration in the form of a `.pre-commit-config.yaml` for the `pre-commit` framework or a custom `.githooks/pre-commit` script wired via `core.hooksPath`.
2. THE Secret_Scan_Hook SHALL block commits whose staged file paths match any of `cookies.json`, `cookies.txt`, `cookie.txt`, `*.env`, `secrets.*`, `**/cookies.json`, or `**/cookies.txt`.
3. WHEN a developer runs `git commit` AND the staged set contains a path matching the blocked patterns, THE Secret_Scan_Hook SHALL exit with a non-zero status and print the offending file path.
4. THE Secret_Scan_Hook SHALL be implementable without internet access; a vendored regex-based fallback SHALL be acceptable.
5. WHERE `gitleaks` is available on the developer machine, THE pre-commit configuration SHALL also run `gitleaks protect --staged` and SHALL fail the commit on any reported finding.
6. THE README or SECURITY.md SHALL include the install and enable instructions for the pre-commit hook.

### Requirement 16: CI secret scan

**User Story:** As a maintainer, I want CI to scan every push and pull request for secrets, so that even contributors who skipped the local hook cannot leak credentials.

#### Acceptance Criteria

1. THE `.github/workflows/ci.yml` SHALL include a job step that runs a secret scan on the pushed commit range.
2. WHEN the CI secret scan finds a finding, THE CI SHALL fail the workflow.
3. THE CI secret scan step SHALL not require any external paid service; the step SHALL use `gitleaks` or an equivalent open-source tool.

## Yo'nalish 5 — MCP Conformance

### Requirement 17: Tool annotation audit

**User Story:** As an MCP host, I want every tool to declare correct annotations, so that I can present write actions with appropriate user warnings.

#### Acceptance Criteria

1. WHEN the Tool_Orchestrator finishes registration, THE MCP_Tool_Annotation_Audit SHALL verify that every tool exposes a non-empty `title` and boolean values for `readOnlyHint`, `idempotentHint`, `destructiveHint`, and `openWorldHint`.
2. WHERE a tool performs a Destructive_Operation, THE tool annotations SHALL declare `readOnlyHint=False` and at least one of `destructiveHint=True` or `idempotentHint=False`.
3. WHERE a tool performs only read operations on public or session-owned data, THE tool annotations SHALL declare `readOnlyHint=True` and `destructiveHint=False`.
4. IF a tool's annotations violate Acceptance Criteria 17.2 or 17.3, THEN THE registration SHALL fail at server startup with an error message naming the offending tool and the violated rule.
5. THE README SHALL contain a section titled "Tool Annotations" that lists, for each tool, its `readOnlyHint`, `idempotentHint`, `destructiveHint`, and `openWorldHint` values.
6. THE Tools_Package SHALL ship a test that asserts the README "Tool Annotations" section matches the annotations registered at runtime.

### Requirement 18: ToolError taxonomy

**User Story:** As an LLM agent, I want a fixed, documented set of `error_type` values, so that I can branch on errors deterministically.

#### Acceptance Criteria

1. THE `instagram_mcp/exceptions.py` module SHALL define a typed `Literal` or `Enum` named `ErrorType` with exactly the values `validation_error`, `not_found`, `private_account`, `auth_required`, `rate_limited`, `network_error`, `fetch_error`, and `unexpected_error`.
2. WHEN any tool raises a `ToolError` directly or via `_tool_error` or `_exception_to_tool_error`, THE raised error's `error_type` field SHALL be one of the eight values defined in Requirement 18 Acceptance Criterion 1.
3. IF a tool raises a `ToolError` with an `error_type` not in the ToolError_Taxonomy, THEN THE corresponding CI test SHALL fail when executed and SHALL identify the offending tool and the offending value.
4. THE Tools_Package SHALL ship a test that asserts every `_tool_error(...)` call site in the codebase passes an `error_type` belonging to the ToolError_Taxonomy.
5. THE README SHALL contain a section titled "Error Taxonomy" listing each `error_type` value with a one-sentence description and a typical example.

### Requirement 19: Resources and Prompts in README

**User Story:** As a reader of README, I want to see what MCP resources and prompts are exposed, so that I know what is available beyond tools.

#### Acceptance Criteria

1. THE README SHALL contain a section titled "Resources" listing every registered MCP resource with URI template, name, description, and MIME type.
2. THE README SHALL contain a section titled "Prompts" listing every registered MCP prompt with name, parameters and their defaults, and a one-line description.
3. WHEN a resource is added, removed, or renamed, THE corresponding README "Resources" entry SHALL be updated in the same change.
4. WHEN a prompt is added, removed, or renamed, THE corresponding README "Prompts" entry SHALL be updated in the same change.
5. THE Tools_Package SHALL ship a test that asserts the README "Resources" section lists exactly the resources registered by `_register_resources` and the README "Prompts" section lists exactly the prompts registered by `_register_prompts`.

## Non-Functional Requirements

### Requirement 20: Performance — registration overhead

**User Story:** As a user starting the server, I want the refactor not to slow down startup, so that cold-start time stays within acceptable bounds.

#### Acceptance Criteria

1. THE Tool_Orchestrator import time on a cold Python 3.10 interpreter measured by `python -X importtime -c "import instagram_mcp.tools"` SHALL not exceed the pre-refactor baseline import time by more than 15 percent.
2. THE Tool_Orchestrator SHALL avoid eager top-level imports of heavy optional components such as `scheduler`, `monitor`, `oauth_manager`, and `session_manager`; lazy imports SHALL be performed inside the relevant tool function bodies, matching the current pattern in `__init__.py`.
3. WHEN measured with `time python -c "from instagram_mcp import create_mcp_server; create_mcp_server()"`, THE server creation time SHALL not regress by more than 100 milliseconds compared to the pre-refactor baseline measured on the same machine.

### Requirement 21: Backwards compatibility

**User Story:** As an existing integrator, I want my Claude Desktop config and my pinned `cookies.json` location to keep working, so that I do not need to change anything after upgrading.

#### Acceptance Criteria

1. THE Public_API_Surface SHALL remain unchanged after the refactor.
2. WHEN an external caller imports `instagram_mcp` and invokes `run_server()`, THE entry point SHALL behave identically to the pre-refactor entry point with the same configuration.
3. THE `instagram-mcp` console-script entry point declared in `pyproject.toml` SHALL remain `instagram_mcp:run_server`.
4. THE `.well-known/mcp/server-card.json`, where present, SHALL be regenerated to reflect the runtime Tool_Inventory; existing fields outside the tools list SHALL remain unchanged.

### Requirement 22: Maintainability

**User Story:** As a future contributor, I want each submodule to be small and self-contained, so that I can pick a domain to work on without context-switching across the whole repo.

#### Acceptance Criteria

1. Each Tools_Package submodule SHALL contain at most 1500 source lines, excluding shared helpers in `_helpers.py`.
2. Each Tools_Package submodule SHALL declare its `TOOLSET_NAME` constant and its Submodule_Registrar in the same file.
3. THE Tools_Package SHALL ship a static structural test that asserts every submodule exports `register_<toolset>` and `TOOLSET_NAME`, every registered tool name uses the `instagram_` prefix, and no tool name is registered twice across the package.

### Requirement 23: Security posture

**User Story:** As a security reviewer, I want runtime and CI guarantees that secrets do not leak through logs, exports, or Docker images, so that operating the server is low-risk.

#### Acceptance Criteria

1. THE Instagram_MCP_Server SHALL not log cookie file contents, raw `Cookie:` HTTP headers, or OAuth tokens at any log level.
2. WHERE a log message would include a path to a cookie file, THE log statement SHALL include only the path string and not its content.
3. THE Dockerfile SHALL not `COPY` `cookies.json`, `cookies.txt`, `cookie.txt`, `*.env`, or `secrets.*` into the image.
4. WHEN the Docker image build context contains any of those files, THE Dockerfile SHALL exclude them via `.dockerignore`.
5. THE `.dockerignore` file SHALL include entries equivalent to the cookie and secret entries in `.gitignore`.

### Requirement 24: curl_cffi pattern preservation

**User Story:** As an operator, I want the Chrome 142 impersonation pattern unchanged, so that the proven anti-bot bypass keeps working.

#### Acceptance Criteria

1. THE `MCPConfig.ig_impersonate` default value SHALL remain `chrome142`.
2. THE `InstagramClient` SHALL continue to construct sessions with the same `impersonate=` parameter and the same User-Agent format as before.
3. THE refactor SHALL not introduce any new HTTP client library; `curl_cffi` SHALL remain the sole HTTP backend for Instagram traffic.

### Requirement 25: Python and dependency versions

**User Story:** As a packager, I want supported Python versions and dependency floors unchanged, so that the upgrade is non-breaking for users on Python 3.10 and `mcp[cli]>=1.0`.

#### Acceptance Criteria

1. THE `pyproject.toml` `requires-python` SHALL remain `>=3.10`.
2. THE `mcp[cli]` dependency floor SHALL remain `>=1.0.0`.
3. THE `curl-cffi` dependency floor SHALL remain `>=0.7.0`.

### Requirement 26: Test suite preservation

**User Story:** As a contributor running `pytest`, I want the existing test suite to keep passing after the refactor, so that I trust the change set.

#### Acceptance Criteria

1. WHEN `pytest` is run against the refactored codebase, THE pre-existing tests SHALL pass without modification, except for tests that explicitly exercise the removed `instagram_mcp/tools.py` import path; such tests SHALL be updated to import from the new package.
2. WHERE no test framework is currently configured, THE Tools_Package SHALL still ship the new tests for parity, taxonomy, structural, and annotation audit under a `tests/` directory using `pytest`.

## Out of Scope

The following items are explicitly excluded from this feature:

1. **New Instagram features.** No new tools, resources, or prompts will be added; the surface area stays at the current set.
2. **HTTP backend change.** Replacing `curl_cffi` with a different library is out of scope.
3. **Transport additions.** No new MCP transports beyond the currently supported STDIO and Streamable HTTP.
4. **Performance tuning of the Instagram client.** Rate-limiter, proxy manager, cache, and circuit-breaker behaviour stay as-is, except for log-line redactions required by Requirement 23.
5. **Renaming public symbols.** Tool names, env var names, `MCPConfig` field names, prompt names, and resource URIs are frozen.
6. **Rewriting git history on the public branch.** The Git_History_Cleanup_Playbook is documentation only; this feature does not execute the rewrite. Maintainers run the playbook outside the scope of this change.
7. **Adding a paid secret-scan service.** Only open-source tooling such as `gitleaks` or custom regex hooks is in scope.
8. **Multi-account session model changes.** `session_manager.py` and `INSTAGRAM_MCP_COOKIES_<ALIAS>` semantics stay unchanged.
9. **OAuth flow changes.** `oauth_manager.py` behaviour and the `instagram_oauth` tool stay unchanged.
10. **Migration of `data/accounts/` schema.** `AccountPool` storage layout is preserved; only the Path_Argument_Guard is added.
