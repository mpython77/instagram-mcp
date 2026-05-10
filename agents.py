"""
Programmatic workflow agents — multi-step Instagram data pipelines.

These agents orchestrate several API calls into a single structured result,
suitable for scripts, cron jobs, and custom integrations outside of MCP.
They share the same InstagramClient + MCPConfig as the MCP server.

Usage:
    from instagram_mcp.agents import InfluencerVettingAgent
    agent = InfluencerVettingAgent(client, config)
    result = await agent.run("nike", goal="brand partnership")

Available agents:
  InfluencerVettingAgent  — profile + engagement + collabs → scored vetting report
  AccountHealthAgent      — activity check + engagement → health score + red flags
  CreatorDiscoveryAgent   — tag-network traversal to find similar creators
  BulkScoringAgent        — score and rank up to 20 accounts in parallel
  ContentAuditAgent       — deep feed audit: content mix, cadence, hashtags, best days

Progress reporting:
  All agents accept an optional progress_cb:
      async def my_cb(current: int, total: int, message: str) -> None: ...
  Pass it as progress_cb=my_cb to any agent's run() method.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple, Union

from .client import InstagramClient
from .config import MCPConfig
from .models import DateRange, FeedTagResult, InstagramPost, InstagramProfile
from .parser import (
    check_dead_account,
    extract_page_info,
    parse_feed_tags,
    parse_feed_tags_from_edges,
    parse_profile,
)

logger = logging.getLogger("instagram_mcp.agents")

ProgressCB = Optional[Callable[[int, int, str], Union[None, Coroutine]]]


# ═════════════════════════════════════════════════════════════════════════════
# SCORING HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _er_score(er_pct: float) -> float:
    """ER% → 0-40 points."""
    if er_pct >= 6:
        return 40.0
    if er_pct >= 3:
        return 30.0 + (er_pct - 3) / 3 * 10
    if er_pct >= 1:
        return 15.0 + (er_pct - 1) / 2 * 15
    return max(0.0, er_pct * 15)


def _followers_score(n: int) -> float:
    """Followers → 0-30 points (log scale, 10M cap)."""
    if n <= 0:
        return 0.0
    return round(min(30.0, math.log10(max(n, 10)) / math.log10(10_000_000) * 30), 1)


def _activity_score(last_post_days: int) -> float:
    """Days since last post → 0-20 points."""
    if last_post_days <= 7:
        return 20.0
    if last_post_days <= 30:
        return 15.0
    if last_post_days <= 90:
        return 8.0
    if last_post_days <= 365:
        return 3.0
    return 0.0


def _quality_score(profile: InstagramProfile) -> float:
    """Profile quality signals → 0-10 points."""
    score = 0.0
    if profile.is_verified:
        score += 5
    if profile.is_business or profile.is_professional:
        score += 2
    if profile.highlight_count > 0:
        score += 2
    if profile.has_reels:
        score += 1
    return score


def compute_account_score(
    profile: InstagramProfile,
    er_pct: float = 0.0,
    last_post_days: int = 0,
) -> float:
    """Overall account quality score: 0-100.

    Breakdown:
      Engagement Rate : 0-40  (primary signal)
      Followers       : 0-30  (log scale)
      Activity        : 0-20  (recency of posts)
      Quality         : 0-10  (verified, business, highlights, reels)
    """
    return round(
        _er_score(er_pct)
        + _followers_score(profile.followers)
        + _activity_score(last_post_days)
        + _quality_score(profile),
        1,
    )


def compute_er(profile: InstagramProfile, posts: List[InstagramPost]) -> float:
    """(avg_likes + avg_comments) / followers × 100, rounded to 2dp."""
    if not posts or profile.followers <= 0:
        return 0.0
    n = len(posts)
    total = sum(p.likes + p.comments for p in posts)
    return round(total / n / profile.followers * 100, 2)


# ═════════════════════════════════════════════════════════════════════════════
# RESULT TYPES
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class VettingResult:
    """Output of InfluencerVettingAgent."""
    username: str = ""
    found: bool = False
    profile: Optional[InstagramProfile] = None
    is_dead: bool = False
    last_post_days: int = 0
    feed_tags: Optional[FeedTagResult] = None
    er_pct: float = 0.0
    avg_likes: float = 0.0
    avg_comments: float = 0.0
    posts_analysed: int = 0
    usertags: List[Tuple[str, int]] = field(default_factory=list)
    mentions: List[Tuple[str, int]] = field(default_factory=list)
    sponsors: List[str] = field(default_factory=list)
    coauthors: List[str] = field(default_factory=list)
    score: float = 0.0
    verdict: str = ""  # recommended | conditional | not_recommended | private | dead | not_found
    goal: str = ""
    errors: List[str] = field(default_factory=list)
    elapsed_s: float = 0.0


@dataclass
class HealthReport:
    """Output of AccountHealthAgent."""
    username: str = ""
    found: bool = False
    profile: Optional[InstagramProfile] = None
    status: str = ""  # active | dead | private | not_found
    last_post_days: int = 0
    er_pct: float = 0.0
    avg_likes: float = 0.0
    posts_analysed: int = 0
    health_score: float = 0.0
    red_flags: List[str] = field(default_factory=list)
    green_flags: List[str] = field(default_factory=list)
    verdict: str = ""  # healthy | needs_attention | problematic
    errors: List[str] = field(default_factory=list)
    elapsed_s: float = 0.0


@dataclass
class DiscoveredCreator:
    """Single creator found by CreatorDiscoveryAgent."""
    username: str = ""
    profile: Optional[InstagramProfile] = None
    discovered_via: str = ""  # usertag | mention | coauthor | sponsor
    frequency: int = 0        # how many times seed tagged this person
    score: float = 0.0
    last_post_days: int = 0


@dataclass
class ScoredAccount:
    """Single account result from BulkScoringAgent."""
    username: str = ""
    found: bool = False
    profile: Optional[InstagramProfile] = None
    is_dead: bool = False
    last_post_days: int = 0
    er_pct: float = 0.0
    score: float = 0.0
    rank: int = 0


# ═════════════════════════════════════════════════════════════════════════════
# BASE AGENT
# ═════════════════════════════════════════════════════════════════════════════

class _BaseAgent:
    def __init__(self, client: InstagramClient, config: MCPConfig) -> None:
        self.client = client
        self.config = config

    async def _fetch(self, username: str) -> Optional[dict]:
        return await self.client.fetch_user(username, self.config.cache_profile_ttl)

    async def _paginate(
        self,
        user: dict,
        profile: InstagramProfile,
        max_posts: int,
        max_age_days: int,
        date_range: Optional["DateRange"] = None,
    ) -> Tuple[List[dict], int]:
        """Return (all_edges, pages_fetched)."""
        page_info = extract_page_info(user)
        all_edges = list(page_info.get("first_page_edges", []))
        end_cursor = page_info.get("end_cursor", "")
        has_next = page_info.get("has_next_page", False)
        effective = min(max_posts, self.config.max_pagination_posts)
        pages = 1

        remaining = effective - len(all_edges)
        if remaining > 0 and has_next and end_cursor and profile.user_id:
            feed = await self.client.fetch_user_feed(
                user_id=profile.user_id,
                username=profile.username,
                end_cursor=end_cursor,
                max_posts=remaining,
                max_age_days=max_age_days,
                cache_ttl=self.config.cache_feed_ttl,
                date_range=date_range,
            )
            all_edges.extend(feed.get("edges", []))
            pages += feed.get("pages_fetched", 0)

        return all_edges[:effective], pages

    @staticmethod
    async def _emit(cb: ProgressCB, current: int, total: int, msg: str) -> None:
        if cb is None:
            return
        result = cb(current, total, msg)
        if asyncio.iscoroutine(result):
            await result


# ═════════════════════════════════════════════════════════════════════════════
# AGENT 1: INFLUENCER VETTING
# ═════════════════════════════════════════════════════════════════════════════

class InfluencerVettingAgent(_BaseAgent):
    """
    Full influencer vetting pipeline.

    Steps:
      1. Profile + first-page feed tags + activity status
      2. Engagement rate analysis (paginated, up to max_posts)
      3. Collaboration network (same post set)
      4. Scoring + verdict

    Verdict rules:
      score >= 60  → recommended
      score >= 35  → conditional
      score < 35   → not_recommended
      is_dead      → dead (scored but flagged)
      is_private   → private (no score)
      not found    → not_found

    Example:
        agent = InfluencerVettingAgent(client, config)
        result = await agent.run("nike", goal="brand partnership", max_posts=50)
        print(result.score, result.verdict)
    """

    async def run(
        self,
        username: str,
        goal: str = "brand partnership",
        max_posts: int = 50,
        max_age_days: int = 90,
        progress_cb: ProgressCB = None,
    ) -> VettingResult:
        t0 = time.perf_counter()
        result = VettingResult(username=username, goal=goal)

        # ── Step 1: Profile + feed ────────────────────────────────────────────
        await self._emit(progress_cb, 1, 4, f"Fetching @{username} profile...")
        try:
            user = await self._fetch(username)
        except Exception as e:
            result.errors.append(f"Fetch failed: {e}")
            result.verdict = "error"
            result.elapsed_s = round(time.perf_counter() - t0, 2)
            return result

        if user is None:
            result.verdict = "not_found"
            result.elapsed_s = round(time.perf_counter() - t0, 2)
            return result

        profile = parse_profile(user, username, self.config)
        result.found = True
        result.profile = profile

        if profile.is_private:
            result.verdict = "private"
            result.elapsed_s = round(time.perf_counter() - t0, 2)
            return result

        is_dead, last_post_days = check_dead_account(user)
        result.is_dead = is_dead
        result.last_post_days = last_post_days

        feed_tags = parse_feed_tags(user, 12, max_age_days)
        result.feed_tags = feed_tags

        # ── Step 2: Engagement (paginated) ────────────────────────────────────
        await self._emit(progress_cb, 2, 4, f"Analysing engagement ({max_posts} posts)...")
        try:
            all_edges, _ = await self._paginate(user, profile, max_posts, max_age_days)
            ft = parse_feed_tags_from_edges(
                edges=all_edges, max_posts=max_posts,
                max_age_days=max_age_days, detect_pinned=True,
            )
            posts = ft.posts
            result.posts_analysed = len(posts)
            result.er_pct = compute_er(profile, posts)
            if posts:
                result.avg_likes = round(sum(p.likes for p in posts) / len(posts), 1)
                result.avg_comments = round(sum(p.comments for p in posts) / len(posts), 1)
        except Exception as e:
            result.errors.append(f"Engagement analysis failed: {e}")
            posts = feed_tags.posts

        # ── Step 3: Collab network ────────────────────────────────────────────
        await self._emit(progress_cb, 3, 4, "Mapping collaboration network...")
        try:
            usertag_counter: Counter = Counter()
            mention_counter: Counter = Counter()
            sponsor_set: set = set()
            coauthor_set: set = set()

            for post in (posts if posts else feed_tags.posts):
                for u in post.usertags:
                    usertag_counter[u] += 1
                for m in post.mentions:
                    mention_counter[m] += 1
                for s in post.sponsor_tags:
                    sponsor_set.add(s)
                for c in post.coauthors:
                    coauthor_set.add(c)

            result.usertags = usertag_counter.most_common(20)
            result.mentions = mention_counter.most_common(20)
            result.sponsors = sorted(sponsor_set)
            result.coauthors = sorted(coauthor_set)
        except Exception as e:
            result.errors.append(f"Collab network failed: {e}")

        # ── Step 4: Score + verdict ───────────────────────────────────────────
        await self._emit(progress_cb, 4, 4, "Computing score...")
        result.score = compute_account_score(profile, result.er_pct, last_post_days)

        if is_dead:
            result.verdict = "dead"
        elif result.score >= 60:
            result.verdict = "recommended"
        elif result.score >= 35:
            result.verdict = "conditional"
        else:
            result.verdict = "not_recommended"

        result.elapsed_s = round(time.perf_counter() - t0, 2)
        logger.info(
            "InfluencerVettingAgent @%s → %s (score=%.1f, er=%.2f%%, %ds)",
            username, result.verdict, result.score, result.er_pct, result.elapsed_s,
        )
        return result


# ═════════════════════════════════════════════════════════════════════════════
# AGENT 2: ACCOUNT HEALTH
# ═════════════════════════════════════════════════════════════════════════════

class AccountHealthAgent(_BaseAgent):
    """
    Account health audit — activity status, engagement, red flags.

    Steps:
      1. Profile + activity check
      2. Engagement analysis (paginated)
      3. Health scoring + red/green flag detection

    Health score = same 0-100 formula as InfluencerVettingAgent.
    Verdict:
      score >= 55  → healthy
      score >= 30  → needs_attention
      score < 30   → problematic

    Example:
        agent = AccountHealthAgent(client, config)
        report = await agent.run("nike", dead_threshold_days=365)
        print(report.verdict, report.red_flags)
    """

    async def run(
        self,
        username: str,
        max_posts: int = 50,
        max_age_days: int = 180,
        dead_threshold_days: int = 365,
        progress_cb: ProgressCB = None,
    ) -> HealthReport:
        t0 = time.perf_counter()
        report = HealthReport(username=username)

        # ── Step 1: Profile + status ──────────────────────────────────────────
        await self._emit(progress_cb, 1, 3, f"Fetching @{username}...")
        try:
            user = await self._fetch(username)
        except Exception as e:
            report.errors.append(f"Fetch failed: {e}")
            report.status = "error"
            report.elapsed_s = round(time.perf_counter() - t0, 2)
            return report

        if user is None:
            report.status = "not_found"
            report.elapsed_s = round(time.perf_counter() - t0, 2)
            return report

        profile = parse_profile(user, username, self.config)
        report.found = True
        report.profile = profile

        if profile.is_private:
            report.status = "private"
            report.elapsed_s = round(time.perf_counter() - t0, 2)
            return report

        is_dead, last_post_days = check_dead_account(user, dead_threshold_days)
        report.last_post_days = last_post_days
        report.status = "dead" if is_dead else "active"

        # ── Step 2: Engagement ────────────────────────────────────────────────
        await self._emit(progress_cb, 2, 3, "Analysing engagement...")
        try:
            all_edges, _ = await self._paginate(user, profile, max_posts, max_age_days)
            ft = parse_feed_tags_from_edges(
                edges=all_edges, max_posts=max_posts,
                max_age_days=max_age_days, detect_pinned=True,
            )
            posts = ft.posts
            report.posts_analysed = len(posts)
            report.er_pct = compute_er(profile, posts)
            if posts:
                report.avg_likes = round(sum(p.likes for p in posts) / len(posts), 1)
        except Exception as e:
            report.errors.append(f"Engagement analysis failed: {e}")

        # ── Step 3: Flags + verdict ───────────────────────────────────────────
        await self._emit(progress_cb, 3, 3, "Evaluating health...")
        red: List[str] = []
        green: List[str] = []

        if is_dead:
            red.append(f"No posts in {last_post_days}+ days (dead threshold: {dead_threshold_days}d)")
        elif last_post_days > 90:
            red.append(f"Last post {last_post_days} days ago — infrequent posting")
        elif last_post_days <= 14:
            green.append(f"Actively posting (last post {last_post_days}d ago)")

        if profile.followers > 0 and profile.following > 0:
            ratio = profile.following / profile.followers
            if ratio > 2:
                red.append(f"Suspicious follow ratio: following {profile.following:,} vs {profile.followers:,} followers")
            elif ratio < 0.1:
                green.append("Strong follower/following ratio")

        if report.er_pct < 1 and report.posts_analysed > 5:
            red.append(f"Very low engagement rate: {report.er_pct:.2f}% (benchmark: ≥1%)")
        elif report.er_pct >= 6:
            green.append(f"Excellent engagement rate: {report.er_pct:.2f}%")
        elif report.er_pct >= 3:
            green.append(f"Good engagement rate: {report.er_pct:.2f}%")

        if profile.posts_count == 0:
            red.append("Zero posts published")
        elif profile.posts_count < 9:
            red.append(f"Very few posts: {profile.posts_count}")
        elif profile.posts_count >= 100:
            green.append(f"Established account: {profile.posts_count:,} posts")

        if profile.is_verified:
            green.append("Verified account")
        if profile.is_new_account:
            red.append("New account (recently joined)")

        report.red_flags = red
        report.green_flags = green

        report.health_score = compute_account_score(profile, report.er_pct, last_post_days)

        if report.health_score >= 55:
            report.verdict = "healthy"
        elif report.health_score >= 30:
            report.verdict = "needs_attention"
        else:
            report.verdict = "problematic"

        report.elapsed_s = round(time.perf_counter() - t0, 2)
        logger.info(
            "AccountHealthAgent @%s → %s (score=%.1f, flags=%d red %d green, %ds)",
            username, report.verdict, report.health_score,
            len(red), len(green), report.elapsed_s,
        )
        return report


# ═════════════════════════════════════════════════════════════════════════════
# AGENT 3: CREATOR DISCOVERY
# ═════════════════════════════════════════════════════════════════════════════

class CreatorDiscoveryAgent(_BaseAgent):
    """
    Tag-network traversal to discover similar creators.

    Starts from a seed account, extracts everyone they tag/mention,
    then fetches profiles for each discovered account and filters for
    active public creators.

    Steps:
      1. Collab network for seed (usertags + mentions + coauthors)
      2. Parallel profile fetch for all discovered accounts (concurrency=10)
      3. Filter: public + active + min_followers
      4. Score and rank

    Example:
        agent = CreatorDiscoveryAgent(client, config)
        creators = await agent.run(
            "nike", max_posts=50, min_followers=1000, min_frequency=2
        )
        for c in creators[:10]:
            print(c.username, c.profile.followers, c.score)
    """

    async def run(
        self,
        seed_username: str,
        max_posts: int = 50,
        max_age_days: int = 90,
        min_followers: int = 1_000,
        min_frequency: int = 1,
        max_results: int = 30,
        progress_cb: ProgressCB = None,
    ) -> List[DiscoveredCreator]:
        # ── Step 1: Seed collab network ───────────────────────────────────────
        await self._emit(progress_cb, 1, 3, f"Scanning @{seed_username} tag network...")
        try:
            user = await self._fetch(seed_username)
        except Exception as e:
            logger.warning("CreatorDiscoveryAgent: fetch failed for seed @%s: %s", seed_username, e)
            return []

        if user is None:
            return []

        profile = parse_profile(user, seed_username, self.config)
        if profile.is_private:
            return []

        all_edges, _ = await self._paginate(user, profile, max_posts, max_age_days)
        ft = parse_feed_tags_from_edges(
            edges=all_edges, max_posts=max_posts,
            max_age_days=max_age_days, detect_pinned=True,
        )

        # Collect candidates with source type and frequency.
        # Priority order (strongest signal wins): sponsor > coauthor > usertag > mention.
        _PRIORITY = {"sponsor": 4, "coauthor": 3, "usertag": 2, "mention": 1}

        def _record(uname: str, via: str) -> None:
            if not uname or uname == seed_username:
                return
            entry = candidates.get(uname)
            if entry is None:
                candidates[uname] = {"via": via, "freq": 1}
                return
            entry["freq"] += 1
            if _PRIORITY[via] > _PRIORITY[entry["via"]]:
                entry["via"] = via

        candidates: Dict[str, Dict] = {}
        for post in ft.posts:
            for u in post.usertags:
                _record(u, "usertag")
            for m in post.mentions:
                _record(m, "mention")
            for ca in post.coauthors:
                _record(ca, "coauthor")
            for s in post.sponsor_tags:
                _record(s, "sponsor")

        # Apply min_frequency filter
        candidates = {u: d for u, d in candidates.items() if d["freq"] >= min_frequency}
        if not candidates:
            return []

        # Cap candidates to avoid fetching an unbounded number of profiles.
        # Keep the highest-frequency entries; max_results * 10 is a generous ceiling.
        _MAX_CANDIDATES = max_results * 10
        if len(candidates) > _MAX_CANDIDATES:
            candidates = dict(
                sorted(candidates.items(), key=lambda kv: kv[1]["freq"], reverse=True)[
                    :_MAX_CANDIDATES
                ]
            )

        # ── Step 2: Parallel profile fetch ───────────────────────────────────
        await self._emit(progress_cb, 2, 3, f"Fetching {len(candidates)} discovered accounts...")
        semaphore = asyncio.Semaphore(10)

        async def _fetch_one(uname: str) -> Tuple[str, Optional[dict]]:
            async with semaphore:
                try:
                    u = await self.client.fetch_user(uname, self.config.cache_profile_ttl)
                    return uname, u
                except Exception as e:
                    logger.debug("CreatorDiscoveryAgent: fetch failed for @%s: %s", uname, e)
                    return uname, None

        tasks = [asyncio.create_task(_fetch_one(u)) for u in candidates]
        raw_results = await asyncio.gather(*tasks)

        # ── Step 3: Filter + score ────────────────────────────────────────────
        await self._emit(progress_cb, 3, 3, "Scoring discovered creators...")
        discovered: List[DiscoveredCreator] = []

        for uname, user_data in raw_results:
            if user_data is None:
                continue
            p = parse_profile(user_data, uname, self.config)
            if p.is_private or p.followers < min_followers:
                continue

            is_dead, last_post_days = check_dead_account(user_data)
            if is_dead:
                continue

            meta = candidates[uname]
            score = compute_account_score(p, 0.0, last_post_days)

            discovered.append(DiscoveredCreator(
                username=uname,
                profile=p,
                discovered_via=meta["via"],
                frequency=meta["freq"],
                score=score,
                last_post_days=last_post_days,
            ))

        # Sort: frequency first, then score
        discovered.sort(key=lambda x: (x.frequency, x.score), reverse=True)
        result = discovered[:max_results]

        logger.info(
            "CreatorDiscoveryAgent @%s → %d creators from %d candidates",
            seed_username, len(result), len(candidates),
        )
        return result


# ═════════════════════════════════════════════════════════════════════════════
# AGENT 4: BULK SCORING
# ═════════════════════════════════════════════════════════════════════════════

class BulkScoringAgent(_BaseAgent):
    """
    Score and rank up to 20 accounts in parallel.

    Fetches all profiles concurrently, computes scores, and returns a
    ranked list. Dead and not-found accounts are included at the bottom
    with score=0.

    For top-N accounts (by follower count), optionally fetches engagement
    data for a more accurate score.

    Example:
        agent = BulkScoringAgent(client, config)
        accounts = await agent.run(
            ["nike", "adidas", "puma"],
            enrich_top_n=3,
        )
        for a in accounts:
            print(a.rank, a.username, a.score)
    """

    async def run(
        self,
        usernames: List[str],
        enrich_top_n: int = 0,
        max_age_days: int = 90,
        progress_cb: ProgressCB = None,
    ) -> List[ScoredAccount]:
        if not usernames:
            return []

        await self._emit(progress_cb, 1, 2, f"Fetching {len(usernames)} profiles in parallel...")

        raw = await self.client.fetch_bulk(
            usernames,
            concurrency=min(len(usernames), 10),
            cache_ttl=self.config.cache_profile_ttl,
        )

        accounts: List[ScoredAccount] = []
        for item in raw:
            uname = item.get("username", "")
            if not item.get("found") or not item.get("user"):
                accounts.append(ScoredAccount(username=uname, found=False))
                continue

            user_data = item["user"]
            p = parse_profile(user_data, uname, self.config)
            is_dead, last_post_days = check_dead_account(user_data)

            accounts.append(ScoredAccount(
                username=uname,
                found=True,
                profile=p,
                is_dead=is_dead,
                last_post_days=last_post_days,
                score=compute_account_score(p, 0.0, last_post_days),
            ))

        # ── Optional ER enrichment for top N ─────────────────────────────────
        if enrich_top_n > 0:
            active = [a for a in accounts if a.found and not a.is_dead and a.profile]
            active.sort(key=lambda a: a.profile.followers if a.profile else 0, reverse=True)
            top = active[:enrich_top_n]

            if top:
                await self._emit(progress_cb, 2, 2, f"Enriching top {len(top)} with engagement data...")

                async def _enrich(acc: ScoredAccount) -> None:
                    try:
                        user_data = await self._fetch(acc.username)
                        if user_data is None or acc.profile is None:
                            return
                        all_edges, _ = await self._paginate(
                            user_data, acc.profile, 50, max_age_days
                        )
                        ft = parse_feed_tags_from_edges(
                            edges=all_edges, max_posts=50,
                            max_age_days=max_age_days, detect_pinned=True,
                        )
                        acc.er_pct = compute_er(acc.profile, ft.posts)
                        acc.score = compute_account_score(acc.profile, acc.er_pct, acc.last_post_days)
                    except Exception as e:
                        logger.debug("Enrich failed for @%s: %s", acc.username, e)

                await asyncio.gather(*[asyncio.create_task(_enrich(a)) for a in top])

        # Sort: found+active first, then by score desc
        accounts.sort(
            key=lambda a: (a.found and not a.is_dead, a.score),
            reverse=True,
        )
        for i, a in enumerate(accounts, 1):
            a.rank = i

        logger.info(
            "BulkScoringAgent: %d accounts, top score=%.1f (@%s)",
            len(accounts),
            accounts[0].score if accounts else 0,
            accounts[0].username if accounts else "",
        )
        return accounts


# ═════════════════════════════════════════════════════════════════════════════
# AGENT 5: CONTENT AUDIT
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class ContentAuditReport:
    """Output of ContentAuditAgent."""
    username: str = ""
    found: bool = False
    posts_analyzed: int = 0
    # Type breakdown: {type → count}
    type_counts: Dict[str, int] = field(default_factory=dict)
    # Avg likes/comments per type: {type → (avg_likes, avg_comments)}
    type_avg: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    # Best day of week by avg likes: [(day_name, avg_likes)]
    best_days: List[Tuple[str, float]] = field(default_factory=list)
    # Top 10 hashtags by frequency: [(tag, count)]
    top_hashtags: List[Tuple[str, int]] = field(default_factory=list)
    # Posts per week average
    posts_per_week: float = 0.0
    # Overall engagement rate
    er_pct: float = 0.0
    # Top 3 posts by likes: [(shortcode, likes, comments)]
    top_posts: List[Tuple[str, int, int]] = field(default_factory=list)
    elapsed_s: float = 0.0


class ContentAuditAgent(_BaseAgent):
    """
    Content strategy audit — deep feed analysis with per-type performance.

    Fetches up to max_posts recent posts (paginated), then computes:
    - Content mix: reels vs carousels vs images vs videos
    - Per-type engagement averages
    - Best posting days by avg likes
    - Top hashtags used
    - Posting cadence (posts/week)
    - Overall engagement rate

    Usage::

        agent = ContentAuditAgent(client, config)
        report = await agent.run("nike", max_posts=100, max_age_days=90)
    """

    async def run(
        self,
        username: str,
        max_posts: int = 100,
        max_age_days: int = 90,
        progress_cb: ProgressCB = None,
    ) -> ContentAuditReport:
        t0 = time.monotonic()
        report = ContentAuditReport(username=username)

        await self._emit(progress_cb, 0, 3, f"Fetching @{username} profile…")
        try:
            user = await self._fetch(username)
        except Exception as e:
            logger.warning("ContentAuditAgent: fetch failed for @%s: %s", username, e)
            return report
        if user is None:
            return report

        profile = parse_profile(user, username, self.config)
        report.found = True

        if profile.is_private:
            logger.info("ContentAuditAgent @%s → private, skipping feed", username)
            return report

        await self._emit(progress_cb, 1, 3, "Paginating feed…")
        edges, _ = await self._paginate(user, profile, max_posts, max_age_days)

        from .parser import parse_feed_tags_from_edges
        from datetime import datetime, timezone

        feed = parse_feed_tags_from_edges(
            edges=edges,
            max_posts=max_posts,
            max_age_days=max_age_days,
        )
        posts = feed.posts
        report.posts_analyzed = len(posts)

        if not posts:
            return report

        await self._emit(progress_cb, 2, 3, f"Analysing {len(posts)} posts…")

        # ── Type breakdown ──────────────────────────────────────────────────
        type_likes: Dict[str, List[int]] = {}
        type_comments: Dict[str, List[int]] = {}
        day_likes: Dict[str, List[int]] = {}
        hashtag_counter: Counter = Counter()
        timestamps: List[int] = []

        _DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        for p in posts:
            ptype = p.post_type or "image"
            type_likes.setdefault(ptype, []).append(p.likes)
            type_comments.setdefault(ptype, []).append(p.comments)

            if p.taken_at:
                timestamps.append(p.taken_at)
                day_name = _DAY[datetime.fromtimestamp(p.taken_at, tz=timezone.utc).weekday()]
                day_likes.setdefault(day_name, []).append(p.likes)

            for tag in p.hashtags:
                hashtag_counter[tag.lower().lstrip("#")] += 1

        report.type_counts = {t: len(v) for t, v in type_likes.items()}
        report.type_avg = {
            t: (
                round(sum(type_likes[t]) / len(type_likes[t]), 1),
                round(sum(type_comments[t]) / len(type_comments[t]), 1),
            )
            for t in type_likes
        }

        # ── Best days ───────────────────────────────────────────────────────
        report.best_days = sorted(
            [(d, round(sum(v) / len(v), 1)) for d, v in day_likes.items()],
            key=lambda x: x[1],
            reverse=True,
        )

        # ── Top hashtags ────────────────────────────────────────────────────
        report.top_hashtags = hashtag_counter.most_common(10)

        # ── Posting cadence ─────────────────────────────────────────────────
        if len(timestamps) >= 2:
            span_days = (max(timestamps) - min(timestamps)) / 86400
            report.posts_per_week = round(len(timestamps) / max(span_days, 1) * 7, 1)

        # ── Engagement rate ─────────────────────────────────────────────────
        report.er_pct = compute_er(profile, posts)

        # ── Top posts ───────────────────────────────────────────────────────
        report.top_posts = [
            (p.shortcode, p.likes, p.comments)
            for p in sorted(posts, key=lambda x: x.likes, reverse=True)[:3]
        ]

        report.elapsed_s = round(time.monotonic() - t0, 2)
        logger.info(
            "ContentAuditAgent @%s → %d posts | er=%.2f%% | cadence=%.1f/wk | %ds",
            username,
            len(posts),
            report.er_pct,
            report.posts_per_week,
            report.elapsed_s,
        )
        return report
