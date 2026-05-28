# Implementation Plan: MCP Architecture Hardening

## Overview

Convert the feature design into a series of prompts for a code-generation LLM that will implement each step with incremental progress. Make sure that each prompt builds on the previous prompts, and ends with wiring things together. There should be no hanging or orphaned code that isn't integrated into a previous step. Focus ONLY on tasks that involve writing, modifying, or testing code.

The plan splits into five layers that can mostly progress in parallel before converging on the bootstrap rewrite:

1. **Foundation primitives** — shared helpers, `ToolDescriptor`, `ErrorType` taxonomy, `ensure_path` guard, audit and instructions builders.
2. **Repo hygiene & security artefacts** — `.gitignore`, `.dockerignore`, working-copy cleanup, log redaction, `SECURITY.md`, secret-scan script, pre-commit config, CI gitleaks step.
3. **Submodule extraction** — split `tools.py` into eight domain submodules.
4. **Orchestration & bootstrap** — write the orchestrator, delete the legacy file, rewire `create_mcp_server`.
5. **Documentation sync & guardrails** — README sections, `pyproject.toml`, server card, public-API snapshot, perf and size tests.

The implementation language is **Python 3.10+** to match the existing codebase. Tests use `pytest`, property-based tests use `hypothesis` (added under `[project.optional-dependencies].dev`).

## Tasks

- [ ] 1. Set up tools package skeleton and extend exception taxonomy
  - [ ] 1.1 Create `instagram_mcp/tools/_helpers.py` with the `ToolDescriptor` dataclass, `AuthTier` literal, and re-exported `sanitize_username`, `_tool_error`, `_exception_to_tool_error` ported from current `tools.py`
    - Create the `instagram_mcp/tools/` package directory with empty `__init__.py` (orchestrator filled in later in task 8.1)
    - Move `sanitize_username`, `_tool_error`, `_exception_to_tool_error`, and `_paginate_feed` bodies from `tools.py` into `_helpers.py` without behavioural change
    - Define `ToolDescriptor(name, toolset, auth_tier, annotations, input_model, description_first_line)` as a frozen dataclass
    - Type `_tool_error`'s `error_type` parameter against the `ErrorType` literal from exceptions
    - _Requirements: 1.1, 1.3, 1.6, 2.3_

  - [ ] 1.2 Add `ErrorType` literal and `ALLOWED_ERROR_TYPES` frozenset to `instagram_mcp/exceptions.py`
    - Define `ErrorType = Literal["validation_error", "not_found", "private_account", "auth_required", "rate_limited", "network_error", "fetch_error", "unexpected_error"]`
    - Define `ALLOWED_ERROR_TYPES: frozenset[str]` with the same eight values
    - Add a runtime check inside `_tool_error` (or a small validator helper) that raises `ValueError` when `error_type not in ALLOWED_ERROR_TYPES`
    - _Requirements: 18.1, 18.2_

  - [ ] 1.3 Update existing exception classes' `error_type` values to match the taxonomy
    - Remap `PostNotFoundError` → `not_found`, `ProxyError` → `network_error`, `ConfigError` → `validation_error`, `AccountSuspendedError` → `unexpected_error`, base `InstagramMCPError` default → `unexpected_error`
    - Leave `UserNotFoundError`, `RateLimitError`, `PrivateAccountError`, `AuthError`, `FetchError` unchanged
    - _Requirements: 18.1, 18.2_

  - [ ]* 1.4 Write property test for ToolError taxonomy in `tests/properties/test_error_taxonomy.py`
    - **Property 6: ToolError taxonomy membership**
    - **Validates: Requirements 18.1, 18.2, 18.3, 18.4**
    - Use `ast` to walk every `.py` file under `instagram_mcp/`, collect each `_tool_error(error_type=<literal>)` keyword and each `error_type` class attribute literal on `InstagramMCPError` subclasses, and assert membership in `ALLOWED_ERROR_TYPES`
    - Tag docstring `Feature: mcp-architecture-hardening, Property 6: ToolError taxonomy membership`
    - Configure with `@settings(max_examples=200)` where the generator drives the AST traversal across discovered files

- [ ] 2. Implement path-argument guard
  - [ ] 2.1 Create `instagram_mcp/_path_guard.py` exporting `ensure_path(value, *, name)`
    - Accept `value`, raise `TypeError(f"{name} must be a str, bytes, or pathlib.PurePath, got {type(value).__name__}")` when value is not a `str`, `bytes`, or `pathlib.PurePath`
    - Return the value unchanged on the happy path
    - _Requirements: 11.3, 11.4_

  - [ ] 2.2 Wire `ensure_path` into every component that consumes a directory or file path argument
    - Call `ensure_path(accounts_dir, name="accounts_dir")` at the top of `AccountPool.__init__`
    - Call `ensure_path(media_cache_dir, name="media_cache_dir")` at the top of `MediaCache.__init__`
    - Call `ensure_path(export_dir, name="export_dir")` inside `JsonExporter` construction
    - Call `ensure_path(value, name=...)` for `INSTAGRAM_MCP_ACCOUNTS_DIR`, `INSTAGRAM_MCP_MEDIA_CACHE_DIR`, `INSTAGRAM_MCP_EXPORT_DIR`, and any cookie-path env var read inside `MCPConfig.from_env`
    - _Requirements: 11.3, 11.4_

  - [ ]* 2.3 Write property test for path-argument guard in `tests/properties/test_path_guard.py`
    - **Property 4: Path-argument guard contract**
    - **Validates: Requirements 11.3, 11.4, 11.5**
    - Use `hypothesis` to generate random non-path values (including `unittest.mock.MagicMock`, `int`, `list`, `None`, custom objects) and random valid path values (`str`, `bytes`, `pathlib.Path`, `pathlib.PurePosixPath`, `pathlib.PureWindowsPath`)
    - Assert `ensure_path` raises `TypeError` iff value is not in allowed types and the message contains the parameter name and `type(v).__name__`
    - Add regression cases that construct `AccountPool`, `MediaCache`, `JsonExporter` with a `MagicMock` and assert `TypeError` is raised before any filesystem call
    - Tag docstring `Feature: mcp-architecture-hardening, Property 4: Path-argument guard contract`

- [ ] 3. Implement annotation audit and server instructions builder
  - [ ] 3.1 Create `instagram_mcp/tools/_audit.py` with `run_annotation_audit` and the `DESTRUCTIVE_TOOLS` frozenset
    - Mirror the destructive tool list in design Section 6 (`instagram_dm_send`, `instagram_dm_send_photo`, `instagram_dm_send_video`, `instagram_dm_react`, `instagram_dm_unsend`, `instagram_dm_mark_seen`, `instagram_post_like`, `instagram_post_save`, `instagram_follow_user`, `instagram_block_user`, `instagram_post_comment`, `instagram_delete_comment`, `instagram_publish_story`, `instagram_story_mark_seen`, `instagram_story_reply`, `instagram_edit_profile`, `instagram_broadcast_channel`, `instagram_upload_photo`, `instagram_upload_reel`, `instagram_schedule`, `instagram_oauth`, `instagram_sessions`)
    - Define `AnnotationAuditError(RuntimeError)` and accumulate every violation into a single error message
    - Enforce the rules from design Section 6 verbatim: title presence, four boolean hints, docstring marker matches `auth_tier`, destructive vs read-only invariants
    - _Requirements: 8.1, 8.2, 8.3, 17.1, 17.2, 17.3, 17.4_

  - [ ]* 3.2 Write property test for annotation audit in `tests/properties/test_annotation_audit.py`
    - **Property 3: Annotation audit acceptance invariant**
    - **Validates: Requirements 8.1, 8.2, 8.3, 17.1, 17.2, 17.3, 17.4**
    - Generate random `ToolDescriptor` lists with both well-formed and randomly mutated annotations (missing title, non-bool hints, mismatched markers, destructive-rule violations)
    - Assert `run_annotation_audit` accepts iff every descriptor satisfies all five rules (i)–(v)
    - On failure, assert `AnnotationAuditError` message contains the offending tool's `name` and the violated rule
    - Tag docstring `Feature: mcp-architecture-hardening, Property 3: Annotation audit acceptance invariant`

  - [ ] 3.3 Create `instagram_mcp/tools/_instructions.py` with `build_server_instructions`
    - Implement the `CANONICAL_ORDER` and `TIER_BADGE` constants and the function shape from design Section 5
    - Build the output deterministically: header with `auth_status`, AUTH TIERS line with three tier counters, total count, per-toolset section with badge-prefixed tool lines sorted by name
    - Return non-empty string with zero counts when inventory is empty (do not raise)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [ ]* 3.4 Write property test for instructions builder in `tests/properties/test_instructions_builder.py`
    - **Property 2: Server instructions builder invariants**
    - **Validates: Requirements 6.2, 6.3, 6.4, 6.5, 6.6, 6.7**
    - Use `hypothesis` to generate random `(inventory, auth_status)` tuples and assert all seven invariants (auth_status substring, toolset header order, tier badges, total count, per-toolset counts, per-tier counts, empty-inventory non-raising)
    - Tag docstring `Feature: mcp-architecture-hardening, Property 2: Server instructions builder invariants`

- [ ] 4. Repo hygiene foundation
  - [ ] 4.1 Rewrite `.gitignore` with explicit, non-negated entries
    - Remove any blanket `*.json` or `*.txt` rule and the corresponding `!` exceptions
    - Add `cookie.txt`, `cookies.json`, `cookies.txt`, `data/cookies.json`, `**/cookies.json`, `**/cookies.txt`, `*.env`, `secrets.*`
    - Add `MagicMock/`, `exports/`, `data/media_cache/`, `dist/`, `*.mcpb`, `.pytest_cache/`, `.state/`, `.venv/`, `.mypy_cache/`, `.ruff_cache/`
    - _Requirements: 11.2, 12.3, 12.4, 12.5, 13.1_

  - [ ] 4.2 Create `.dockerignore` mirroring the secret/cache entries from `.gitignore`
    - Include cookies, `*.env`, `secrets.*`, `MagicMock/`, `exports/`, `data/`, `dist/`, `*.mcpb`, `.pytest_cache/`, `.state/`, `.venv/`, `.git/`
    - _Requirements: 23.4, 23.5_

  - [ ] 4.3 Remove tracked sensitive artefacts from the working copy
    - Delete `cookie.txt`, `cookies.json`, `cookies.txt`, `data/cookies.json`, `instagram-mcp.mcpb` from the working tree
    - Delete the `MagicMock/` directory tree, `exports/batch_scrape/`, `exports/hashtag/`, `exports/profile/`, `exports/index.json`, and `data/media_cache/*` from the working tree
    - Do NOT stage these deletions as a commit; this task only touches the working copy
    - _Requirements: 11.1, 12.1, 12.2, 12.6, 12.7, 13.2_

  - [ ] 4.4 Audit `Dockerfile` and remove any `COPY` line that pulls cookies, `*.env`, or `secrets.*` into the image
    - Replace any `COPY . .` or similar broad copy with explicit allowlists, or rely on `.dockerignore` to exclude the sensitive files
    - _Requirements: 23.3, 23.4_

  - [ ] 4.5 Redact secrets from logging in `client.py`, `cookie_manager.py`, and `oauth_manager.py`
    - Audit every `logger.*` call and ensure cookie file content, raw `Cookie:` HTTP headers, and OAuth tokens are never concatenated into log strings
    - Where a cookie path is logged, log only the path string and not its content
    - Reuse the existing `_mask_proxy_url` helper for any new proxy URL log line
    - _Requirements: 23.1, 23.2_

  - [ ]* 4.6 Add `tests/test_gitignore.py` smoke test asserting required entries and absence of blanket rules
    - _Requirements: 11.2, 12.3, 12.4, 12.5, 13.1_

  - [ ]* 4.7 Add `tests/test_dockerignore.py` smoke test asserting cookie/secret entries match `.gitignore`
    - _Requirements: 23.4, 23.5_

  - [ ]* 4.8 Add `tests/test_dockerfile.py` smoke test asserting no `COPY` line includes `cookies.json`, `cookies.txt`, `cookie.txt`, `*.env`, `secrets.*`
    - _Requirements: 23.3_

  - [ ]* 4.9 Add `tests/test_log_redaction.py` static test that greps `instagram_mcp/` for forbidden logger concatenations
    - _Requirements: 23.1, 23.2_

- [ ] 5. Security hardening artefacts
  - [ ] 5.1 Author `SECURITY.md` at the repo root
    - Include sections: `Reporting a Vulnerability`, `Secret environment variables` (list `INSTAGRAM_MCP_COOKIES`, `INSTAGRAM_MCP_COOKIES_<ALIAS>`, OAuth env vars, `proxies.txt` content), `Recommended cookie storage` (absolute path env var, no embedded contents, Docker `:ro` mount), `If a secret was committed` (BFG / `git filter-repo` playbook with backup, rotate via Settings > Login Activity, force-push, collaborators re-clone), `Pre-commit secret scan` (install instructions)
    - Add links to BFG Repo-Cleaner and `git filter-repo` documentation
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7_

  - [ ] 5.2 Implement `scripts/check_no_secrets.py` blocklist matcher
    - Read file paths from argv; exit non-zero if any path matches `cookie.txt`, `cookies.json`, `cookies.txt`, `*.env`, `secrets.*`, `**/cookies.json`, `**/cookies.txt`
    - Print the offending path verbatim to stderr on rejection
    - Use only the standard library so it works without internet access
    - _Requirements: 15.2, 15.3, 15.4_

  - [ ] 5.3 Add `.pre-commit-config.yaml` wiring the local `forbid-cookies` hook and an optional `gitleaks` hook
    - Local hook entry runs `python scripts/check_no_secrets.py` with `pass_filenames: true`
    - Add the `gitleaks/gitleaks` hook with `args: ["protect", "--staged", "--redact"]`
    - _Requirements: 15.1, 15.5_

  - [ ] 5.4 Add a `gitleaks` secret-scan step to `.github/workflows/ci.yml`
    - Use `gitleaks/gitleaks-action@v2` and ensure the workflow fails on any finding
    - _Requirements: 16.1, 16.2, 16.3_

  - [ ]* 5.5 Write property test for secret-scan hook in `tests/properties/test_secret_scan_hook.py`
    - **Property 5: Secret-scan hook path-blocklist contract**
    - **Validates: Requirements 15.2, 15.3**
    - Use `hypothesis` to generate random POSIX-style paths; compute expected blocked status from the documented patterns; spawn `scripts/check_no_secrets.py` as a subprocess and assert exit code matches expected
    - For blocked paths, assert stderr contains the path verbatim
    - Tag docstring `Feature: mcp-architecture-hardening, Property 5: Secret-scan hook path-blocklist contract`

  - [ ]* 5.6 Add `tests/test_security_md.py` smoke test asserting required section titles, env var names, and external links exist
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7_

  - [ ]* 5.7 Add `tests/test_pre_commit_config.py` smoke test asserting `forbid-cookies` hook is referenced
    - _Requirements: 15.1, 15.4, 15.5_

  - [ ]* 5.8 Add `tests/test_ci_workflow.py` smoke test parsing `ci.yml` and asserting the secret-scan step exists
    - _Requirements: 16.1, 16.3_

- [ ] 6. Checkpoint - Foundation primitives and hygiene artefacts in place
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Extract tool submodules from `tools.py`
  - [ ] 7.1 Extract `profile` submodule into `instagram_mcp/tools/profile.py`
    - Move `instagram_profile`, `instagram_feed_deep`, `instagram_compare_profiles`, `instagram_bulk_check`, `instagram_threads_profile`, `instagram_threads_posts` registrations into a `register_profile(mcp, client, config, exporter) -> list[ToolDescriptor]` function
    - Declare `TOOLSET_NAME = "profile"` at module level
    - Each tool docstring SHALL begin with its auth tier marker (`🌐`, `🔐`, or `🌐/🔐`); each registered `ToolDescriptor` SHALL carry an `auth_tier` matching the marker
    - Honour `MCPConfig.hide_auth_when_no_cookies` and `client.cookie_manager.is_authenticated` for `auth`-tier tools
    - Preserve every tool body byte-for-byte; only relocate
    - _Requirements: 1.2, 2.1, 2.2, 2.3, 2.4, 2.5, 4.5, 4.6, 5.1, 5.2, 5.3, 8.1, 8.3_

  - [ ] 7.2 Extract `analysis` submodule into `instagram_mcp/tools/analysis.py`
    - Move `instagram_analyze_engagement`, `instagram_find_collab_network`, `instagram_hashtag_suggest`, `instagram_caption_analyze`, `instagram_account_report`, `instagram_analyze_comments`
    - Apply the same registrar contract and docstring marker rule
    - _Requirements: 1.2, 2.1, 2.2, 2.3, 2.4, 2.5, 4.5, 4.6, 5.1, 5.2, 5.3, 8.1, 8.3_

  - [ ] 7.3 Extract `content` submodule into `instagram_mcp/tools/content.py`
    - Move `instagram_post`, `instagram_post_comments`, `instagram_hashtag`, `instagram_hashtag_deep`, `instagram_post_bulk`, `instagram_niche_top`, `instagram_stories`, `instagram_highlights`, `instagram_reels`, `instagram_tagged_by`, `instagram_reposts`, `instagram_location_posts`, `instagram_audio_reels`
    - _Requirements: 1.2, 2.1, 2.2, 2.3, 2.4, 2.5, 4.5, 4.6, 5.1, 5.2, 5.3, 8.1, 8.3_

  - [ ] 7.4 Extract `social_graph` submodule into `instagram_mcp/tools/social_graph.py`
    - Move `instagram_search`, `instagram_followers_list`, `instagram_following_list`, `instagram_post_likers`, `instagram_similar_accounts`, `instagram_user_search`, `instagram_user_followers`, `instagram_user_following`, `instagram_follow_user`, `instagram_block_user`, `instagram_post_like`, `instagram_post_save`, `instagram_post_comment`, `instagram_delete_comment`, `instagram_publish_story`, `instagram_story_mark_seen`, `instagram_story_reply`, `instagram_edit_profile`, `instagram_broadcast_channel`
    - Verify each destructive tool declares `readOnlyHint=False` and at least one of `destructiveHint=True` or `idempotentHint=False`
    - _Requirements: 1.2, 2.1, 2.2, 2.3, 2.4, 2.5, 4.5, 4.6, 5.1, 5.2, 5.3, 8.1, 8.3, 17.2_

  - [ ] 7.5 Extract `dm` submodule into `instagram_mcp/tools/dm.py`
    - Move `instagram_dm_inbox`, `instagram_dm_thread`, `instagram_dm_send`, `instagram_dm_send_photo`, `instagram_dm_send_video`, `instagram_dm_react`, `instagram_dm_unsend`, `instagram_dm_mark_seen`
    - Confirm destructive annotations on the write tools
    - _Requirements: 1.2, 2.1, 2.2, 2.3, 2.4, 2.5, 4.5, 4.6, 5.1, 5.2, 5.3, 8.1, 8.3, 17.2_

  - [ ] 7.6 Extract `upload` submodule into `instagram_mcp/tools/upload.py`
    - Move `instagram_upload_photo`, `instagram_upload_reel`, `instagram_download`
    - _Requirements: 1.2, 2.1, 2.2, 2.3, 2.4, 2.5, 4.5, 4.6, 5.1, 5.2, 5.3, 8.1, 8.3, 17.2_

  - [ ] 7.7 Extract `automation` submodule into `instagram_mcp/tools/automation.py`
    - Move `instagram_batch_scrape`, `instagram_schedule`, `instagram_monitor`, `instagram_sessions`, `instagram_oauth`
    - Keep the lazy imports of `scheduler`, `monitor`, `oauth_manager`, `session_manager` inside tool bodies (no top-level imports of these modules)
    - _Requirements: 1.2, 2.1, 2.2, 2.3, 2.4, 2.5, 4.5, 4.6, 5.1, 5.2, 5.3, 8.1, 8.3, 17.2, 20.2_

  - [ ] 7.8 Extract `server` submodule into `instagram_mcp/tools/server.py`
    - Move `instagram_server` registration; this submodule is always invoked, so it has no auth-gating skip logic for the tool itself
    - _Requirements: 1.2, 2.1, 2.2, 2.3, 2.4, 2.5, 4.3, 5.1, 5.2, 5.3, 8.1, 8.3_

- [ ] 8. Implement Tool Orchestrator
  - [ ] 8.1 Fill in `instagram_mcp/tools/__init__.py` with `register_tools` and helpers
    - Import every submodule and define `CANONICAL_ORDER = (profile, analysis, content, social_graph, dm, upload, automation, server)` plus `LEGACY_ALIASES = {"batch": "automation"}`
    - Implement `_resolve_enabled_toolsets(config) -> set[str]` per design Section "Tool gating": empty/`all` → all toolsets; otherwise expanded set + `server` always
    - Implement `register_tools(mcp, client, config, exporter) -> None`: invoke each enabled submodule's `register_<toolset>` in canonical order, catching exceptions from the `server` submodule and continuing in degraded mode while logging at ERROR level; collect every returned `ToolDescriptor` into a single inventory list and store it as `mcp._instagram_tool_inventory`
    - Log INFO-level summary with the total count and per-toolset counts via `_log_inventory_summary`
    - Re-export `register_tools`, `sanitize_username`, `_tool_error`, `_exception_to_tool_error`, `ToolDescriptor` in `__all__`
    - Do NOT include any inline `@mcp.tool` declarations
    - _Requirements: 1.5, 1.6, 3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 4.4_

  - [ ] 8.2 Delete the legacy `instagram_mcp/tools.py` file
    - Verify all external imports (including `from instagram_mcp.tools import register_tools` and `from instagram_mcp.tools import sanitize_username`) resolve to the new package
    - _Requirements: 1.4_

  - [ ]* 8.3 Write property test for orchestrator gating in `tests/properties/test_orchestrator_gating.py`
    - **Property 1: Toolset gating contract**
    - **Validates: Requirements 2.4, 3.2, 4.1, 4.2, 4.3, 4.5, 4.6**
    - Use `hypothesis` to generate `(enabled_toolsets, hide_auth_when_no_cookies, is_authenticated, per-module ToolDescriptor lists)`; monkeypatch each submodule's `register_<toolset>` to return the generated list and record invocation; run `register_tools`; assert `mcp._instagram_tool_inventory` matches the gated/concatenated expected list and the `server` submodule is always invoked
    - Tag docstring `Feature: mcp-architecture-hardening, Property 1: Toolset gating contract`

  - [ ]* 8.4 Add `tests/test_tool_structure.py` for static structural assertions
    - For each canonical submodule assert `register_<toolset>` and `TOOLSET_NAME` are exported, the registrar signature has four positional parameters, every registered tool name uses the `instagram_` prefix, and no name appears twice across the package
    - _Requirements: 1.5, 2.1, 2.2, 2.3, 2.5, 5.1, 22.3_

  - [ ]* 8.5 Add `tests/test_smoke_structure.py` smoke test
    - Assert `import instagram_mcp.tools` succeeds, the legacy `instagram_mcp/tools.py` is absent, every submodule importable, and `_helpers` exposes `sanitize_username`, `_tool_error`, `_exception_to_tool_error`, `ToolDescriptor`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.6_

- [ ] 9. Wire server bootstrap and synchronize docstrings
  - [ ] 9.1 Update `create_mcp_server` in `instagram_mcp/__init__.py` to call orchestrator → audit → instructions builder
    - Build `auth_status = "authenticated"` or `"anonymous (no cookies.txt)"` from `client.cookie_manager.is_authenticated`
    - Instantiate `FastMCP` with placeholder `instructions=""`
    - Call `register_tools(mcp, client, config, exporter)`
    - Call `run_annotation_audit(mcp._instagram_tool_inventory)`; on `AnnotationAuditError`, propagate (server fails to start)
    - Call `build_server_instructions(mcp._instagram_tool_inventory, auth_status)` and assign to `mcp._mcp_server.instructions` (or `mcp.set_instructions(...)` if available in the installed `mcp[cli]` version)
    - Keep `_register_resources(mcp, client, config)` and `_register_prompts(mcp)` calls unchanged
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 8.2, 17.1, 17.4_

  - [ ] 9.2 Strip hardcoded inventory text from `instagram_mcp/__init__.py` module docstring
    - Remove every literal tool-count string such as "12 tools", "19 tools", "11 anonymous", "8 auth"
    - Replace with a sentence that points readers to the runtime-generated `Tool_Inventory` and the README tool table as the authoritative sources
    - Apply the same cleanup to `instagram_mcp/tools/__init__.py` docstring (already mostly orchestrator-shaped; just confirm no per-tool inventory text remains)
    - Drop the legacy "AUTH TIERS" comment block from any code that may have inherited it during extraction
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [ ]* 9.3 Add `tests/test_no_inline_tool_decorators.py`
    - AST-parse `instagram_mcp/tools/__init__.py` and assert no `@mcp.tool` decorator usage
    - _Requirements: 3.4_

  - [ ]* 9.4 Add `tests/test_lazy_imports.py`
    - AST scan: assert `scheduler`, `monitor`, `oauth_manager`, `session_manager` are NOT imported at module top level of any `tools/` submodule
    - _Requirements: 20.2_

  - [ ]* 9.5 Add `tests/test_docstring_inventory.py`
    - Read `instagram_mcp/__init__.py` and `instagram_mcp/tools/__init__.py` docstrings; assert no patterns like `\d+\s+tools`, `\d+\s+anonymous`, `\d+\s+auth`
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [ ]* 9.6 Add `tests/test_tool_docs.py` for docstring vs Pydantic field parity
    - Iterate `Tool_Inventory`; for each tool assert every public Pydantic field name appears in the docstring and no extra parameter names appear
    - On failure, report the tool name, the missing parameters, and the extraneous parameters
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

- [ ] 10. Checkpoint - Refactored package boots and audits cleanly
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 11. Synchronize documentation surface and add public-API guardrails
  - [ ] 11.1 Update `README.md` sections to match runtime registrations
    - Rebuild the **Auth Tiers** table from runtime per-tier counts (no hardcoded contradictions)
    - Add or refresh **Tool Annotations** table listing each tool's `readOnlyHint`, `idempotentHint`, `destructiveHint`, `openWorldHint`
    - Add **Error Taxonomy** section listing each of the eight `error_type` values with one-sentence description and one typical example
    - Add **Resources** section listing `instagram://profile/{username}`, `instagram://feed/{username}`, `instagram://server/status` with URI, name, description, MIME type
    - Add **Prompts** section listing `analyze_influencer`, `find_brand_collaborations`, `competitive_analysis`, `account_audit`, `discover_creators`, `validate_prospect_list` with parameters/defaults and one-line descriptions
    - Add **Pre-commit setup** instructions (`pip install pre-commit && pre-commit install`)
    - _Requirements: 10.2, 15.6, 17.5, 18.5, 19.1, 19.2_

  - [ ] 11.2 Update `pyproject.toml` description so it does not contradict runtime inventory
    - Remove any specific tool-count number from the `description` field
    - _Requirements: 10.1_

  - [ ] 11.3 Add `scripts/regenerate_server_card.py` to rebuild `.well-known/mcp/server-card.json` from `Tool_Inventory`
    - Construct an `MCP server` instance via `create_mcp_server`, read `mcp._instagram_tool_inventory`, write the tool list section while preserving every other field
    - _Requirements: 21.4_

  - [ ]* 11.4 Add `tests/test_readme_sync.py` parity tests
    - Parse README sections and assert each of `Auth Tiers`, `Tool Annotations`, `Resources`, `Prompts`, `Error Taxonomy` is in 1-1 correspondence with runtime registrations
    - On failure, name the offending tool/resource/prompt
    - _Requirements: 10.2, 10.3, 10.4, 17.5, 17.6, 18.5, 19.1, 19.2, 19.5_

  - [ ]* 11.5 Add `tests/test_public_api_snapshot.py`
    - Snapshot `MCPConfig` field names plus default values, env var names parsed by `from_env`, `instagram://...` resource URIs, prompt names, and the `instagram_mcp:run_server` console-script entry point
    - _Requirements: 5.4, 5.5, 5.6, 5.7, 21.1, 21.2, 21.3, 24.1, 24.2, 24.3_

  - [ ]* 11.6 Add `tests/test_pyproject_versions.py`
    - Read `pyproject.toml` and assert `requires-python>=3.10`, `mcp[cli]>=1.0.0`, `curl-cffi>=0.7.0`, console-script `instagram-mcp = "instagram_mcp:run_server"`
    - _Requirements: 21.3, 25.1, 25.2, 25.3_

  - [ ]* 11.7 Add `tests/test_server_card.py`
    - Run `scripts/regenerate_server_card.py` against the freshly-built server, assert the resulting `.well-known/mcp/server-card.json` tool list equals the runtime inventory names
    - _Requirements: 21.4_

- [ ] 12. Final guardrails - submodule size and startup performance
  - [ ]* 12.1 Add `tests/test_submodule_size.py` asserting each `tools/<submodule>.py` ≤ 1500 source lines (excluding `_helpers.py`)
    - _Requirements: 22.1_

  - [ ]* 12.2 Add `tests/test_startup_perf.py` benchmark with baseline in `tests/_baselines.json`
    - Measure `python -X importtime` cold-import time of `instagram_mcp.tools`; assert ≤ baseline × 1.15
    - Measure `create_mcp_server()` wall time; assert no more than +100 ms regression vs baseline captured pre-refactor
    - _Requirements: 20.1, 20.3_

- [ ] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP, but the property tests (1.4, 2.3, 3.2, 3.4, 5.5, 8.3) materialize the six Correctness Properties P1-P6 and SHOULD be implemented for spec confidence.
- Property-to-task map: **P1 → 8.3**, **P2 → 3.4**, **P3 → 3.2**, **P4 → 2.3**, **P5 → 5.5**, **P6 → 1.4**.
- Each task references granular sub-requirement clauses (e.g. `4.5, 4.6`) so traceability is preserved.
- Checkpoints (tasks 6, 10, 13) are top-level milestones and are intentionally excluded from the dependency graph.
- The plan keeps `Public_API_Surface` (tool names, env var names, `MCPConfig` defaults, resource URIs, prompt names, console-script entry) frozen as required by Requirement 21.
- `curl_cffi` Chrome 142 impersonation pattern is preserved (Requirement 24); no HTTP backend change.
- Working-copy cleanup in task 4.3 removes files from disk only and does NOT stage a commit (Requirement 12.2). History rewrite is documented in `SECURITY.md` but executed outside this feature scope.
- All test sub-tasks add tests under `tests/` using `pytest`; property tests live under `tests/properties/` and use `hypothesis` with `@settings(max_examples=200)` and the `Feature: mcp-architecture-hardening, Property N: <title>` docstring tag.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "2.1", "4.1", "4.2", "4.4", "4.5", "5.1", "5.2", "5.4"] },
    { "id": 1, "tasks": ["1.3", "2.2", "3.1", "3.3", "4.3", "5.3", "4.6", "4.7", "4.8", "4.9", "5.6", "5.8"] },
    { "id": 2, "tasks": ["1.4", "2.3", "3.2", "3.4", "5.5", "5.7", "7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "7.8"] },
    { "id": 3, "tasks": ["8.1"] },
    { "id": 4, "tasks": ["8.2", "8.3", "8.4", "9.1", "9.3", "9.4", "12.1"] },
    { "id": 5, "tasks": ["8.5", "9.2", "9.6", "11.1", "11.2", "11.3", "11.5", "12.2"] },
    { "id": 6, "tasks": ["9.5", "11.4", "11.6", "11.7"] }
  ]
}
```
