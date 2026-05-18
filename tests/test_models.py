import pytest
from instagram_mcp.models import (
    _parse_user_date,
    AccountStatus,
    InstagramProfile,
    InstagramPost,
    FeedTagResult,
    ProfileWithTags,
    TaggedPost,
    DateRange,
    CacheStats,
    ProxyStatus,
    UsernameInput,
    ProfileInput,
    BulkProfilesInput,
    EngagementAnalysisInput,
    CollabNetworkInput,
    CompareProfilesInput,
    DeepFeedInput,
    TaggedByInput,
    RepostsInput,
    ReelsInput,
    RepostItem,
    ReelItem,
    CommentItem,
    PostLocation,
    PostInfo,
    PostInput,
    PostCommentsInput,
    ServerInput
)

def test_parse_user_date():
    assert _parse_user_date("") is None
    assert _parse_user_date("  ") is None
    
    # 2026-03-01 -> 1772323200 depending on timezone, but let's just check it doesn't raise
    assert isinstance(_parse_user_date("2026-03-01"), int)
    assert isinstance(_parse_user_date("01.03.2026"), int)
    assert isinstance(_parse_user_date("01/03/2026"), int)
    assert isinstance(_parse_user_date("01-03-2026"), int)
    assert isinstance(_parse_user_date("01,03,2026"), int)
    
    with pytest.raises(ValueError, match="Invalid date"):
        _parse_user_date("invalid")

def test_account_status():
    assert AccountStatus.ACTIVE == "active"
    assert AccountStatus.DEAD == "dead"
    assert AccountStatus.PRIVATE == "private"
    assert AccountStatus.NOT_FOUND == "not_found"

def test_instagram_profile_post_init():
    p = InstagramProfile(
        username=" Test  ",
        followers=-1,
        following=-5,
        posts_count=-2,
        highlight_count=-3,
        usertags_count=-1
    )
    assert p.username == "test"
    assert p.followers == 0
    assert p.following == 0
    assert p.posts_count == 0
    assert p.highlight_count == 0
    assert p.usertags_count == 0
    assert repr(p) == "Profile(username='test', followers=0)"

def test_instagram_post_post_init():
    p = InstagramPost(
        shortcode="abc",
        likes=-1,
        comments=-2,
        video_view_count=-3,
        carousel_count=-4,
        width=-5,
        height=-6,
        taken_at_str="now"
    )
    assert p.likes == 0
    assert p.comments == 0
    assert p.video_view_count == 0
    assert p.carousel_count == 0
    assert p.width == 0
    assert p.height == 0
    assert repr(p) == "Post(shortcode='abc', likes=0, taken_at_str='now')"

def test_feed_tag_result_post_init():
    r = FeedTagResult(
        tags=["a"],
        posts_checked=-1,
        posts_with_tags=-2,
        pages_fetched=-3
    )
    assert r.posts_checked == 0
    assert r.posts_with_tags == 0
    assert r.pages_fetched == 1
    assert repr(r) == "FeedTagResult(tags=1, posts_checked=0)"

def test_profile_with_tags():
    p = ProfileWithTags()
    assert p.is_dead is False

def test_tagged_post_post_init():
    p = TaggedPost(likes=-1, comments=-2, view_count=-3, carousel_count=-4)
    assert p.likes == 0
    assert p.comments == 0
    assert p.view_count == 0
    assert p.carousel_count == 0

def test_date_range():
    dr = DateRange(since=100, until=200)
    assert dr.contains(150) is True
    assert dr.contains(50) is False
    assert dr.contains(250) is False
    assert dr.is_before_range(50) is True
    assert dr.is_before_range(150) is False

def test_cache_stats():
    c = CacheStats()
    assert c.enabled is True

def test_proxy_status():
    p = ProxyStatus()
    assert p.is_active is True

def test_pydantic_username_input():
    u = UsernameInput(username=" @Test ")
    assert u.username == "test"

def test_profile_input():
    p = ProfileInput(username="test", since_date="01.03.2026", until_date="01/04/2026")
    assert isinstance(p.resolved_since(), int)
    assert isinstance(p.resolved_until(), int)
    
    p2 = ProfileInput(username="test", since_timestamp=100, until_timestamp=200)
    assert p2.resolved_since() == 100
    assert p2.resolved_until() == 200

def test_bulk_profiles_input():
    b = BulkProfilesInput(usernames=["a", "b"])
    assert len(b.usernames) == 2

def test_engagement_analysis_input():
    e = EngagementAnalysisInput(username=" @abc ")
    assert e.username == "abc"

def test_collab_network_input():
    c = CollabNetworkInput(username="  xyz")
    assert c.username == "xyz"

def test_compare_profiles_input():
    c = CompareProfilesInput(usernames=["a", "b"])
    assert c.usernames == ["a", "b"]

def test_deep_feed_input():
    d = DeepFeedInput(username="test", since_date="01.03.2026", until_date="01.04.2026")
    assert isinstance(d.resolved_since(), int)
    assert isinstance(d.resolved_until(), int)

    d2 = DeepFeedInput(username="test", since_timestamp=100, until_timestamp=200)
    assert d2.resolved_since() == 100
    assert d2.resolved_until() == 200

def test_tagged_by_input():
    t = TaggedByInput(username=" @aa ")
    assert t.username == "aa"

def test_reposts_input():
    r = RepostsInput(username=" @bb ")
    assert r.username == "bb"

def test_reels_input():
    r = ReelsInput(username=" @cc ")
    assert r.username == "cc"

def test_repost_item_post_init():
    r = RepostItem(likes=-1, comments=-2, view_count=-3, carousel_count=-4)
    assert r.likes == 0
    assert r.comments == 0
    assert r.view_count == 0
    assert r.carousel_count == 0

def test_reel_item_post_init():
    r = ReelItem(play_count=-1, like_count=-2, comment_count=-3)
    assert r.play_count == 0
    assert r.like_count == 0
    assert r.comment_count == 0

def test_comment_item_post_init():
    c = CommentItem(comment_like_count=-1, child_comment_count=-2)
    assert c.comment_like_count == 0
    assert c.child_comment_count == 0

def test_post_location():
    loc1 = PostLocation(name="Here")
    assert loc1.has_location is True
    loc2 = PostLocation(lat=1.0, lng=2.0)
    assert loc2.has_location is True
    loc3 = PostLocation()
    assert loc3.has_location is False

def test_post_info_post_init():
    p = PostInfo(likes=-1, comments=-2)
    assert p.likes == 0
    assert p.comments == 0

def test_post_input():
    p1 = PostInput(post="DXjuqH9nDVE")
    assert p1.post == "DXjuqH9nDVE"
    
    p2 = PostInput(post="https://www.instagram.com/p/DXjuqH9nDVE/")
    assert p2.post == "DXjuqH9nDVE"
    
    with pytest.raises(ValueError, match="Cannot extract a valid shortcode"):
        PostInput(post="invalid!!!")

def test_post_comments_input():
    p1 = PostCommentsInput(post="https://www.instagram.com/reel/DXjuqH9nDVE/")
    assert p1.post == "DXjuqH9nDVE"
    
    p2 = PostCommentsInput(post="DXjuqH9nDVE", sort_order="INVALID")
    assert p2.sort_order == "popular"
    
    p3 = PostCommentsInput(post="DXjuqH9nDVE", sort_order="recent")
    assert p3.sort_order == "recent"

    with pytest.raises(ValueError, match="Cannot extract a valid shortcode"):
        PostCommentsInput(post="invalid!!!")

def test_server_input():
    s = ServerInput(action="status")
    assert s.action == "status"


def test_like_post_input():
    from instagram_mcp.models import LikePostInput
    p1 = LikePostInput(media_id="3612076889987614897")
    assert p1.media_id == "3612076889987614897"
    assert p1.action == "like"

    p2 = LikePostInput(media_id="123", action="unlike")
    assert p2.action == "unlike"


def test_follow_user_input():
    from instagram_mcp.models import FollowUserInput
    p1 = FollowUserInput(user_id="47689974259")
    assert p1.user_id == "47689974259"
    assert p1.action == "follow"

    p2 = FollowUserInput(user_id="123", action="unfollow")
    assert p2.action == "unfollow"
