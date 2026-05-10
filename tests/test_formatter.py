import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

from instagram_mcp.formatter import (
    _build_collab_network,
    _compute_engagement,
    _engagement_rate_label,
    _format_location,
    _network_table,
    format_account_status_markdown,
    format_bulk_results_markdown,
    format_collab_network_markdown,
    format_comments_markdown,
    format_compare_profiles_markdown,
    format_deep_feed_markdown,
    format_diagnostics_json,
    format_diagnostics_markdown,
    format_engagement_analysis_markdown,
    format_feed_tags_json,
    format_feed_tags_markdown,
    format_followers,
    format_post_markdown,
    format_posts_json,
    format_posts_markdown,
    format_profile_json,
    format_profile_markdown,
    format_profile_with_tags_markdown,
    format_reels_markdown,
    format_reposts_markdown,
    format_tagged_by_markdown,
)
from instagram_mcp.models import (
    CacheStats,
    CommentItem,
    FeedTagResult,
    InstagramPost,
    InstagramProfile,
    PostInfo,
    PostLocation,
    ProxyStatus,
    ReelItem,
    RepostItem,
    TaggedPost,
)

def test_format_location():
    assert _format_location(None) == ""
    assert _format_location({}) == ""
    
    # Lat/lng available
    loc1 = {"name": "New York", "lat": 40.7, "lng": -74.0}
    assert _format_location(loc1) == "[New York](https://www.google.com/maps?q=40.7,-74.0)"
    
    # Only name available
    loc2 = {"name": "Paris"}
    assert _format_location(loc2) == "[Paris](https://www.google.com/maps/search/?api=1&query=Paris)"

def test_format_followers():
    assert format_followers(500) == "500"
    assert format_followers(1500) == "1.5K"
    assert format_followers(1000000) == "1.0M"
    assert format_followers(2540000) == "2.5M"

def _make_profile(**kwargs):
    defaults = {
        "user_id": "123",
        "username": "testuser",
        "full_name": "Test User",
        "biography": "Bio text\nLine 2",
        "followers": 150000,
        "following": 100,
        "posts_count": 50,
        "is_private": False,
        "is_verified": True,
        "is_business": True,
        "account_type": 3,
        "category": "Blogger",
        "overall_category": "Creator",
        "website": "https://example.com",
        "external_url": "",
        "contact_phone": "123-456",
        "public_email": "test@example.com",
        "city": "NY",
        "pronouns": ["they", "them"],
        "highlight_count": 5,
        "usertags_count": 10,
        "has_reels": True,
        "has_guides": True,
        "is_new_account": True,
        "is_professional": True,
    }
    defaults.update(kwargs)
    return InstagramProfile(**defaults)

def test_format_profile_markdown_all_fields():
    p = _make_profile()
    md = format_profile_markdown(p)
    assert "## 👤 @testuser" in md
    assert "**Test User**" in md
    assert "*they · them*" in md
    assert "High" in md # Engagement hint
    assert "**150.0K**" in md
    assert "✅ Verified" in md
    assert "🏷 Business" in md
    assert "🆕 New" in md
    assert "📂 **Category**: Blogger" in md
    assert "📂 **Type**: Creator" in md
    assert "🎬 Reels" in md
    assert "> 📝 Bio text" in md
    assert "> Line 2" in md
    assert "🔗 **Website**" in md
    assert "📞 **Phone**" in md
    assert "📧 **Email**" in md
    assert "📍 **City**" in md
    assert "🆔 **User ID**" in md

def test_format_profile_markdown_minimal():
    p = _make_profile(
        full_name="", pronouns=[], biography="", followers=0, following=0, posts_count=0,
        is_private=True, is_verified=False, is_business=False, account_type=1,
        category="", overall_category="", website="", external_url="https://x.com",
        contact_phone="", public_email="", city="", highlight_count=0, usertags_count=0,
        has_reels=False, has_guides=False, is_new_account=False, is_professional=False,
        user_id=""
    )
    md = format_profile_markdown(p)
    assert "## 👤 @testuser" in md
    assert "🔒 Private" in md
    assert "🔗 **URL**" in md

def test_format_profile_markdown_engagement_hint_medium():
    p = _make_profile(followers=50000, posts_count=10)
    md = format_profile_markdown(p)
    assert "Medium" in md

def test_format_profile_markdown_engagement_hint_low():
    p = _make_profile(followers=500, posts_count=10)
    md = format_profile_markdown(p)
    assert "Low" in md

def test_format_profile_markdown_professional_not_business():
    p = _make_profile(account_type=1, is_professional=True, is_business=False)
    md = format_profile_markdown(p)
    assert "⭐ Professional" in md

def test_format_profile_json():
    p = _make_profile()
    d = format_profile_json(p)
    assert d["username"] == "testuser"
    assert d["followers"] == 150000

def _make_feed_tags():
    return FeedTagResult(
        posts_checked=5,
        posts_with_tags=2,
        tags=["user1", "user2"],
        tag_shortcodes={"user1": "short1", "user2": "short2"},
        tag_timestamps={"user1": "2023-01-01"},
        pages_fetched=1,
        has_more_posts=False,
        posts=[]
    )

def test_format_feed_tags_markdown_with_tags():
    ft = _make_feed_tags()
    md = format_feed_tags_markdown(ft)
    assert "### 🏷️ Feed Tags Analysis" in md
    assert "| 📸 Checked posts | 5 |" in md
    assert "| 🏷️ Posts with tags | 2 |" in md
    assert "| 1 | @user1 | 2023-01-01 | [view](https://www.instagram.com/p/short1/) |" in md
    assert "| 2 | @user2 | — | [view](https://www.instagram.com/p/short2/) |" in md

def test_format_feed_tags_markdown_no_tags():
    ft = FeedTagResult(posts_checked=0, posts_with_tags=0, tags=[], tag_shortcodes={}, tag_timestamps={}, pages_fetched=0, has_more_posts=False, posts=[])
    md = format_feed_tags_markdown(ft)
    assert "*No tags found" in md

def test_format_feed_tags_json():
    ft = _make_feed_tags()
    d = format_feed_tags_json(ft)
    assert d["stats"]["posts_checked"] == 5
    assert d["stats"]["total_tags"] == 2
    assert d["tags"] == ["user1", "user2"]
    assert d["tag_details"][0]["username"] == "user1"
    assert d["tag_details"][0]["post_url"] == "https://www.instagram.com/p/short1/"
    assert d["tag_details"][0]["timestamp"] == "2023-01-01"

def _make_post(**kwargs):
    defaults = {
        "shortcode": "xyz",
        "post_url": "https://ig.com/p/xyz/",
        "post_type": "carousel",
        "taken_at": 1672531200,
        "taken_at_str": "2023-01-01",
        "age_days": 10,
        "likes": 100,
        "comments": 10,
        "video_view_count": 500,
        "carousel_count": 3,
        "width": 1080,
        "height": 1350,
        "location": {"name": "NY"},
        "music_title": "Song",
        "music_artist": "Artist",
        "caption": "A long caption\nnewline",
        "accessibility_caption": "alt text",
        "coauthors": ["c1"],
        "sponsor_tags": ["s1"],
        "usertags": ["u1"],
        "mentions": ["m1"],
        "hashtags": ["h1"],
    }
    defaults.update(kwargs)
    return InstagramPost(**defaults)

def test_format_posts_markdown_empty():
    assert "*No posts found" in format_posts_markdown([])

def test_format_posts_markdown():
    p1 = _make_post()
    p2 = _make_post(
        post_type="video", carousel_count=1, width=0, height=0, location=None,
        music_title="", music_artist="OnlyArtist", caption="x" * 200, coauthors=[], sponsor_tags=[],
        usertags=[], mentions=[], accessibility_caption=""
    )
    p3 = _make_post(
        post_type=None, music_title="OnlyTitle", music_artist="", caption="", accessibility_caption="a" * 150
    )
    
    md = format_posts_markdown([p1, p2, p3])
    
    assert "**📸 [xyz]" in md
    assert "❤️ 100 · 💬 10 · 👁️ 500 views" in md
    assert "📐 1080×1350px" in md
    assert "📍 [NY]" in md
    assert "🎵 **Music**: Song — Artist" in md
    assert "🤝 **Collab**: @c1" in md
    assert "💼 **Sponsored**: @s1" in md
    assert "🏷️ **Usertags**: @u1" in md
    assert "📣 **Mentions**: @m1" in md
    assert "> 📝 A long caption" in md
    
    assert "🎬" in md
    assert "🎵 **Music**: OnlyArtist" in md
    assert "..." in md # caption truncation
    
    assert "📸" in md # fallback icon
    assert "`POST ×3`" in md # fallback type label
    assert "🎵 **Music**: OnlyTitle" in md
    assert "> ♿ aaaaaaaaaa" in md # acc truncation

def test_format_posts_json():
    p1 = _make_post()
    d = format_posts_json([p1])
    assert len(d) == 1
    assert d[0]["shortcode"] == "xyz"

def test_format_profile_with_tags_markdown():
    prof = _make_profile()
    ft = _make_feed_tags()
    ft.posts = [_make_post()]
    
    md = format_profile_with_tags_markdown(prof, ft, False, 5)
    assert "✅ **Active account**" in md
    assert "### 🏷️ Feed Tags Analysis" in md
    assert "### 📸 Recent Posts" in md
    
    prof_priv = _make_profile(is_private=True)
    md_priv = format_profile_with_tags_markdown(prof_priv, ft, False, 0)
    assert "⚠️ **Private account**" in md_priv
    
    md_dead = format_profile_with_tags_markdown(prof, ft, True, 100)
    assert "💀 **DEAD account**" in md_dead

def test_format_bulk_results_markdown():
    res = [
        {"username": "u1", "found": True, "followers": 100, "category": "A", "is_verified": True},
        {"username": "u2", "found": True, "followers": 200, "is_private": True, "category": None},
        {"username": "u3", "found": True, "followers": 300, "is_dead": True},
        {"username": "u4", "found": False},
    ]
    md = format_bulk_results_markdown(res)
    assert "3/4" in md
    assert "| 1 | @u1 | **100** | A | ✅ Active ☑️ |" in md
    assert "| 2 | @u2 | **200** | — | 🔒 Private |" in md
    assert "| 3 | @u3 | **300** | — | 💀 Dead |" in md
    assert "| 4 | @u4 | — | — | ❌ Not found |" in md

def test_format_account_status_markdown():
    md1 = format_account_status_markdown("u1", "active", False, False, 5, 100, 10, 365)
    assert "✅ @u1 — **ACTIVE**" in md1
    assert "✅ Account is **active**" in md1
    
    md2 = format_account_status_markdown("u2", "dead", True, False, 400, 100, 10, 365)
    assert "💀 @u2 — **DEAD**" in md2
    assert "💀 This account hasn't posted in **400** days" in md2
    
    md3 = format_account_status_markdown("u3", "private", False, True, 0, 100, 10, 365)
    assert "🔒 @u3 — **PRIVATE**" in md3
    assert "🔒 **Private** account" in md3
    
    md4 = format_account_status_markdown("u4", "not_found", False, False, 0, 0, 0, 365)
    assert "❌ @u4 — **NOT_FOUND**" in md4

def test_format_diagnostics():
    cs = CacheStats(enabled=True, total_entries=10, max_entries=100, hits=5, misses=5, hit_rate=0.5, evictions=0)
    ps = [ProxyStatus(url_masked="http://...", is_active=True, cooldown_remaining_s=0, total_requests=10, success_rate=0.9, avg_latency_ms=100)]
    ps.append(ProxyStatus(url_masked="http://...", is_active=False, cooldown_remaining_s=10, total_requests=5, success_rate=0.1, avg_latency_ms=500))
    psum = {"active_proxies": 1, "total_proxies": 2, "total_fallbacks": 0}
    rs = {"current_rps": 1, "burst": 5, "tokens_available": 4, "total_requests": 15}
    
    md = format_diagnostics_markdown(cs, ps, psum, rs)
    assert "Hit rate | **50.0%**" in md
    assert "**1/2** active" in md
    assert "🟢" in md
    assert "🔴 ⏳10s" in md
    assert "RPS: **1**" in md
    
    js = format_diagnostics_json(cs, ps, psum, rs)
    d = json.loads(js)
    assert d["cache"]["hits"] == 5
    assert d["proxies"]["summary"]["active_proxies"] == 1
    assert len(d["proxies"]["details"]) == 2

def test_format_deep_feed_markdown():
    prof = _make_profile()
    ft = _make_feed_tags()
    ft.has_more_posts = True
    ft.posts = [_make_post(usertags=["u1"], mentions=["u2"]), _make_post(usertags=["u1"])]
    
    md = format_deep_feed_markdown(prof, ft, False, 5)
    assert "✅ **Active**" in md
    assert "- **More posts available**: ✅ Yes" in md
    assert "| @u1 | 2 |" in md
    assert "| @u2 | 1 |" in md
    assert "❤️ Likes | 200 | 100 |" in md
    
    ft2 = _make_feed_tags()
    ft2.has_more_posts = False
    ft2.posts = []
    ft2.tags = []
    md2 = format_deep_feed_markdown(prof, ft2, True, 100)
    assert "💀 **Dead account**" in md2
    assert "- **More posts available**: ❌ No" in md2
    
    prof_priv = _make_profile(is_private=True)
    md3 = format_deep_feed_markdown(prof_priv, ft2, False, 0)
    assert "⚠️ **Private account**" in md3

def test_engagement_rate_label():
    assert _engagement_rate_label(7.0) == "🔥 Excellent (6%+)"
    assert _engagement_rate_label(4.0) == "✅ Good (3–6%)"
    assert _engagement_rate_label(2.0) == "⚠️ Average (1–3%)"
    assert _engagement_rate_label(0.5) == "❌ Low (<1%)"

def test_compute_engagement():
    assert _compute_engagement([], 100) == {}
    
    p1 = _make_post(likes=100, comments=10, video_view_count=50, post_type="video", taken_at=1672617600, hashtags=["h1", "h2"]) # Mon
    p2 = _make_post(likes=200, comments=20, video_view_count=0, post_type="image", taken_at=1672704000, hashtags=["h2", "h3"]) # Tue
    p3 = _make_post(likes=300, comments=30, video_view_count=0, post_type=None, taken_at=None, hashtags=["h3"]) # No date
    p4 = _make_post(likes=400, comments=40, video_view_count=0, post_type="carousel", taken_at="invalid", hashtags=[]) # Invalid date
    
    res = _compute_engagement([p1, p2, p3, p4], 10000)
    assert res["posts_analyzed"] == 4
    assert res["total_likes"] == 1000
    assert res["engagement_rate"] == 2.75 # 1000/4 + 100/4 = 250+25. 275/10000 = 2.75%
    
    assert res["content_mix"]["video"]["count"] == 1
    assert res["content_mix"]["video"]["avg_likes"] == 100
    assert res["content_mix"]["image"]["count"] == 2 # None type becomes image
    
    assert len(res["best_days"]) == 2
    assert res["best_days"][0]["day"] in ["Mon", "Tue"]
    
    assert res["top_posts"][0].likes == 400
    
    assert res["top_hashtags"][0][0] in ["h2", "h3"] # Both have count 2

def test_format_engagement_analysis_markdown():
    prof = _make_profile()
    md_empty = format_engagement_analysis_markdown(prof, [])
    assert "*No posts found" in md_empty
    
    p1 = _make_post(likes=100, comments=10, video_view_count=50, post_type="video", taken_at=1672617600, hashtags=["h1"])
    md = format_engagement_analysis_markdown(prof, [p1])
    assert "### 📈 Engagement Analysis" in md
    assert "👁️ Total video views | 50" in md
    assert "🎬 Content Mix" in md
    assert "### 📅 Best Posting Days" in md
    assert "### #️⃣ Top Hashtags" in md
    assert "### 🏆 Top Performing Posts" in md

def test_build_collab_network():
    p1 = _make_post(usertags=["u1", "u2"], mentions=["m1"], coauthors=["c1"], sponsor_tags=["s1"])
    p2 = _make_post(usertags=["u1"], mentions=["m1", "m2"], coauthors=[], sponsor_tags=["s1", "s2"])
    
    net = _build_collab_network([p1, p2])
    assert net["posts_analyzed"] == 2
    assert net["total_unique_people"] == 7 # u1, u2, m1, m2, c1, s1, s2 wait, u1, u2 (2), m1, m2 (2), c1 (1), s1, s2 (2). 2+2+1+2 = 7? Wait, unique across all: u1, u2, m1, m2, c1, s1, s2 = 7
    
    u1 = next(x for x in net["usertags"] if x["username"] == "u1")
    assert u1["frequency"] == 2
    assert u1["first_post"] == "xyz"
    assert u1["first_post_url"] == "https://www.instagram.com/p/xyz/"

def test_network_table():
    items = [{"username": "u1", "frequency": 2, "first_post_url": "http://.."}, {"username": "u2", "frequency": 1, "first_post_url": ""}]
    res = _network_table(items, 1)
    assert len(res) == 4
    assert "@u1" in res[2]
    assert "@u2" in res[3]
    assert "—" in res[3]
    
    res2 = _network_table(items, 5)
    assert res2 == ["*None found.*"]

def test_format_collab_network_markdown():
    prof = _make_profile()
    md_empty = format_collab_network_markdown(prof, [])
    assert "*No posts found" in md_empty
    
    p1 = _make_post(usertags=["u1"], mentions=["m1"], coauthors=["c1"], sponsor_tags=["s1"])
    md = format_collab_network_markdown(prof, [p1])
    assert "### 🏷️ Photo Usertags (1 people)" in md
    assert "@u1" in md
    assert "### 📣 Caption Mentions (1 people)" in md
    assert "@m1" in md
    assert "### 🤝 Official Co-authors (1 people)" in md
    assert "@c1" in md
    assert "### 💼 Paid Sponsors (1 people)" in md
    assert "@s1" in md

def test_format_compare_profiles_markdown():
    assert format_compare_profiles_markdown([]) == "*No profiles to compare.*"
    
    p1 = _make_profile(username="u1", is_private=False)
    p2 = _make_profile(username="u2", is_private=True, followers=0, posts_count=0, following=0, is_verified=False, account_type=1, is_business=False, is_professional=False, category="", overall_category="", has_reels=False, website="", external_url="", user_id="")
    p3 = _make_profile(username="", is_private=False, followers=0, posts_count=0, following=0, is_verified=False, account_type=1, is_business=False, category="", has_reels=False, website="", external_url="", user_id="") # Not found
    
    entries = [(p1, False, 10), (p2, True, 0), (p3, False, 0)]
    md = format_compare_profiles_markdown(entries)
    
    assert "| Metric | @u1 | @u2 | @ |" in md
    assert "| 📊 Status | ✅ Active (10d) | 🔒 Private | ❌ Not found |" in md
    assert "| 👥 Followers | **150.0K** | — | — |" in md
    assert "| 📸 Posts | 50 | — | — |" in md
    assert "| ✅ Verified | ✅ Yes | No | No |" in md
    # Type row is omitted or differently formatted
    assert "| 📂 Category | Blogger | — | — |" in md
    assert "| 🎬 Reels | ✅ | — | — |" in md
    assert "| 🔗 Website | ✅ | — | — |" in md
