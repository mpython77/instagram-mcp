"""
Diagnostics: check what data actually comes back from Instagram anon endpoints.
Run: python -m tests._diag_anon
"""
import asyncio, sys, json

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

async def main():
    from instagram_mcp.cache import SmartCache
    from instagram_mcp.client import InstagramClient
    from instagram_mcp.config import MCPConfig
    from instagram_mcp.cookie_manager import CookieManager
    from instagram_mcp.proxy_manager import ProxyManager
    from instagram_mcp.rate_limiter import AdaptiveRateLimiter
    from instagram_mcp.parser import parse_profile, parse_feed_items, parse_post_html

    config = MCPConfig.from_env()
    client = InstagramClient(
        config=config,
        proxy_manager=ProxyManager(proxy_urls=config.proxy_urls),
        rate_limiter=AdaptiveRateLimiter(rate=config.rate_limit_rps, burst=config.rate_limit_burst),
        cache=SmartCache(max_entries=500, enabled=True),
        cookie_manager=CookieManager(),
    )

    # ── 1. Feed item raw fields ──────────────────────────────────────────────
    print("=== FEED ITEM RAW KEYS ===")
    u = await client.fetch_user("instagram", config.cache_profile_ttl)
    p = parse_profile(u, "instagram", config)
    items = await client.fetch_feed_items(p.user_id, 3)
    for i, item in enumerate(items[:2]):
        print(f"\n  Item #{i}: keys = {list(item.keys())}")
        print(f"    code      = {item.get('code')!r}")
        print(f"    shortcode = {item.get('shortcode')!r}")
        print(f"    caption   = {str(item.get('caption',''))[:80]!r}")
        print(f"    like_count= {item.get('like_count')}")
        print(f"    username  = {item.get('user',{}).get('username')}")

    # ── 2. Post HTML parse ───────────────────────────────────────────────────
    print("\n=== POST HTML PARSE ===")
    sc = None
    for item in items:
        sc = item.get("code") or item.get("shortcode") or ""
        if sc: break
    if sc:
        print(f"  Using shortcode: {sc}")
        try:
            html = await client.fetch_post(sc, cache_ttl=60)
            info = parse_post_html(html, sc)
            print(f"  username  = {info.username!r}")
            print(f"  likes     = {info.likes}")
            print(f"  taken_at  = {info.taken_at_str!r}")
            print(f"  caption   = {info.caption[:80]!r}")
            print(f"  post_type = {info.post_type!r}")
        except Exception as e:
            print(f"  FAILED: {e}")

    # ── 3. Hashtag posts raw fields ──────────────────────────────────────────
    print("\n=== HASHTAG POST RAW KEYS ===")
    result = await client.fetch_hashtag("travel", max_posts=3)
    posts = result.get("posts", []) if result else []
    print(f"  Total posts: {len(posts)}")
    for i, post in enumerate(posts[:2]):
        print(f"\n  Post #{i}: keys = {list(post.keys())}")
        print(f"    username = {post.get('username')!r}")
        print(f"    caption  = {str(post.get('caption',''))[:80]!r}")
        print(f"    like_count = {post.get('like_count')}")
        print(f"    shortcode  = {post.get('shortcode')!r}")

    # ── 4. Hashtag suggest internal ─────────────────────────────────────────
    print("\n=== HASHTAG SUGGEST INTERNALS ===")
    import re as _re
    seed = "travel"
    r2 = await client.fetch_hashtag(seed, max_posts=12)
    ps2 = r2.get("posts", []) if r2 else []
    freq = {}
    for post in ps2:
        caption = post.get("caption") or ""
        tags = _re.findall(r"#([A-Za-z0-9_]+)", caption)
        for tag in tags:
            t = tag.lower()
            if t != seed:
                freq[t] = freq.get(t, 0) + 1
    print(f"  Posts fetched: {len(ps2)}")
    print(f"  Posts with captions: {sum(1 for p in ps2 if p.get('caption'))}")
    print(f"  Unique hashtags found: {len(freq)}")
    print(f"  Top 5 hashtags: {sorted(freq.items(), key=lambda x: -x[1])[:5]}")

    await client.close()

asyncio.run(main())
