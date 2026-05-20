## What's new in v2.1.0

### New tools (79 total, up from 77)
- `instagram_analyze_comments` — Rule-based sentiment analysis (Positive/Neutral/Negative) on post comments with emoji stats, keyword extraction, and audience highlights. Supports English, Uzbek, and Russian. No login required.
- `instagram_submit_verification_code` — Submit SMS/Email/2FA code to resolve an Instagram checkpoint challenge and restore the account session.

### New modules
- **AccountPool** (`account_pool.py`) — Rotating pool of Instagram accounts with health tracking (rate_limited/checkpoint_required/expired/active), auto-cooldown recovery, and round-robin failover.
- **ChallengeResolver** (`challenge.py`) — Handles Instagram checkpoint/2FA challenges dynamically. Registers the challenge, accepts a code via instagram_submit_verification_code, updates cookies on disk.
- **JitterAsyncSession + DelaySimulator** (`delay.py`) — Human-like request delays (configurable INSTAGRAM_MCP_DELAY_MIN/MAX ms) to reduce detection risk.
- **MediaCache** (`media_cache.py`) — Caches Instagram CDN media locally by SHA-256 hash to prevent URL expiration issues.

### Bug fixes
- Fixed test_instagram_hashtag_basic mock format mismatch (raw node vs flat normalized dict)
- Eliminated all RuntimeWarning: coroutine never awaited in test suite
- Added allow_redirects=False to tagged, reposts, reels, search, likers, stories, location, audio_reels, highlights endpoints

### New config env vars
- INSTAGRAM_MCP_ACCOUNTS_DIR — Path to directory of account cookie files
- INSTAGRAM_MCP_MEDIA_CACHE_DIR — Path for local media cache
- INSTAGRAM_MCP_DELAY_MIN — Min jitter delay ms (default 500)
- INSTAGRAM_MCP_DELAY_MAX — Max jitter delay ms (default 2000)

### Test suite
748 passed, 0 failures, 0 warnings

## Installation

```bash
pip install instagram-mcp==2.1.0
```

Or download the wheel from this release and install directly:
```bash
pip install instagram_mcp-2.1.0-py3-none-any.whl
```
