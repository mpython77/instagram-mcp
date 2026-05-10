"""

Comprehensive pytest tests for instagram_mcp/parser.py.
Goal: 100% line and branch coverage.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest
import time
from instagram_mcp.parser import (
    _get_taken_at,
    _get_shortcode,
    _get_likes,
    _get_comments,
    _get_video_views,
    _get_caption_text,
    _get_post_type_and_carousel,
    _get_usertags,
    _get_display_url,
    _get_dimensions,
    _extract_location,
    _extract_music,
    filter_bio_links,
    detect_pinned_posts,
    check_dead_account,
    parse_profile,
    parse_feed_tags,
    parse_feed_tags_from_edges,
    extract_page_info,
    parse_tagged_tab_edges,
    parse_repost_items,
    parse_reels_edges,
    shortcode_to_media_id,
    parse_comments,
    parse_post_html,
    _pk_to_timestamp,
    _IG_TS_MIN,
    _IG_TS_MAX,
)
from instagram_mcp.models import (
    DateRange,
    FeedTagResult,
    InstagramPost,
    InstagramProfile,
    CommentItem,
)
from instagram_mcp.config import MCPConfig


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def config():
    return MCPConfig()


@pytest.fixture
def now():
    return time.time()


# ─────────────────────────────────────────────────────────────────────────────
# _get_taken_at
# ─────────────────────────────────────────────────────────────────────────────

class TestGetTakenAt:
    def test_taken_at_timestamp_key(self):
        node = {"taken_at_timestamp": 1700000000}
        assert _get_taken_at(node) == 1700000000

    def test_taken_at_key(self):
        node = {"taken_at": 1700000001}
        assert _get_taken_at(node) == 1700000001

    def test_taken_at_timestamp_takes_priority_over_taken_at(self):
        node = {"taken_at_timestamp": 1700000000, "taken_at": 9999999999}
        assert _get_taken_at(node) == 1700000000

    def test_missing_both_uses_caption_created_at(self):
        node = {"caption": {"created_at": 1700000002, "text": "hello"}}
        assert _get_taken_at(node) == 1700000002

    def test_missing_all_returns_zero(self):
        node = {}
        assert _get_taken_at(node) == 0

    def test_caption_not_dict_returns_zero(self):
        node = {"caption": "some text"}
        assert _get_taken_at(node) == 0

    def test_zero_taken_at_falls_through_to_caption(self):
        # taken_at=0 is falsy, so falls through to caption
        node = {"taken_at": 0, "caption": {"created_at": 1700000005}}
        assert _get_taken_at(node) == 1700000005

    def test_caption_dict_missing_created_at(self):
        node = {"caption": {"text": "no timestamp"}}
        assert _get_taken_at(node) == 0


# ─────────────────────────────────────────────────────────────────────────────
# _get_shortcode
# ─────────────────────────────────────────────────────────────────────────────

class TestGetShortcode:
    def test_shortcode_key(self):
        assert _get_shortcode({"shortcode": "ABC123"}) == "ABC123"

    def test_code_key(self):
        assert _get_shortcode({"code": "XYZ789"}) == "XYZ789"

    def test_shortcode_takes_priority(self):
        assert _get_shortcode({"shortcode": "AAA", "code": "BBB"}) == "AAA"

    def test_empty_shortcode_falls_to_code(self):
        assert _get_shortcode({"shortcode": "", "code": "BBB"}) == "BBB"

    def test_missing_both_returns_empty(self):
        assert _get_shortcode({}) == ""


# ─────────────────────────────────────────────────────────────────────────────
# _get_likes
# ─────────────────────────────────────────────────────────────────────────────

class TestGetLikes:
    def test_old_format_edge_media_preview_like(self):
        node = {"edge_media_preview_like": {"count": 42}}
        assert _get_likes(node) == 42

    def test_new_format_like_count(self):
        node = {"like_count": 100}
        assert _get_likes(node) == 100

    def test_old_format_takes_priority(self):
        node = {"edge_media_preview_like": {"count": 10}, "like_count": 999}
        assert _get_likes(node) == 10

    def test_missing_returns_zero(self):
        assert _get_likes({}) == 0

    def test_old_format_zero_count_falls_to_new(self):
        # count=0 is falsy
        node = {"edge_media_preview_like": {"count": 0}, "like_count": 77}
        assert _get_likes(node) == 77

    def test_none_edge_media_returns_like_count(self):
        node = {"edge_media_preview_like": None, "like_count": 55}
        assert _get_likes(node) == 55


# ─────────────────────────────────────────────────────────────────────────────
# _get_comments
# ─────────────────────────────────────────────────────────────────────────────

class TestGetComments:
    def test_old_format(self):
        node = {"edge_media_to_comment": {"count": 7}}
        assert _get_comments(node) == 7

    def test_new_format(self):
        node = {"comment_count": 15}
        assert _get_comments(node) == 15

    def test_old_format_takes_priority(self):
        node = {"edge_media_to_comment": {"count": 3}, "comment_count": 999}
        assert _get_comments(node) == 3

    def test_missing_returns_zero(self):
        assert _get_comments({}) == 0

    def test_old_format_none_falls_to_new(self):
        node = {"edge_media_to_comment": None, "comment_count": 8}
        assert _get_comments(node) == 8

    def test_old_format_zero_count_falls_to_new(self):
        node = {"edge_media_to_comment": {"count": 0}, "comment_count": 12}
        assert _get_comments(node) == 12


# ─────────────────────────────────────────────────────────────────────────────
# _get_video_views
# ─────────────────────────────────────────────────────────────────────────────

class TestGetVideoViews:
    def test_video_view_count_priority(self):
        node = {"video_view_count": 1000, "play_count": 2000, "view_count": 3000}
        assert _get_video_views(node) == 1000

    def test_play_count_fallback(self):
        node = {"play_count": 2000, "view_count": 3000}
        assert _get_video_views(node) == 2000

    def test_view_count_fallback(self):
        node = {"view_count": 3000}
        assert _get_video_views(node) == 3000

    def test_missing_returns_zero(self):
        assert _get_video_views({}) == 0

    def test_zero_video_view_count_falls_to_play_count(self):
        node = {"video_view_count": 0, "play_count": 500}
        assert _get_video_views(node) == 500


# ─────────────────────────────────────────────────────────────────────────────
# _get_caption_text
# ─────────────────────────────────────────────────────────────────────────────

class TestGetCaptionText:
    def test_old_edge_media_to_caption_format(self):
        node = {
            "edge_media_to_caption": {
                "edges": [{"node": {"text": "Hello world"}}]
            }
        }
        assert _get_caption_text(node) == "Hello world"

    def test_new_caption_text_dict_format(self):
        node = {"caption": {"text": "Dict caption"}}
        assert _get_caption_text(node) == "Dict caption"

    def test_caption_as_plain_string(self):
        node = {"caption": "Plain string caption"}
        assert _get_caption_text(node) == "Plain string caption"

    def test_missing_returns_empty(self):
        assert _get_caption_text({}) == ""

    def test_empty_edges_falls_to_caption_dict(self):
        node = {
            "edge_media_to_caption": {"edges": []},
            "caption": {"text": "fallback"}
        }
        assert _get_caption_text(node) == "fallback"

    def test_none_caption_returns_empty(self):
        node = {"caption": None}
        assert _get_caption_text(node) == ""

    def test_old_format_edge_missing_node_text(self):
        node = {
            "edge_media_to_caption": {
                "edges": [{"node": {}}]
            }
        }
        assert _get_caption_text(node) == ""

    def test_caption_dict_missing_text_key(self):
        node = {"caption": {"other_key": "value"}}
        assert _get_caption_text(node) == ""


# ─────────────────────────────────────────────────────────────────────────────
# _get_post_type_and_carousel
# ─────────────────────────────────────────────────────────────────────────────

class TestGetPostTypeAndCarousel:
    def test_graph_sidecar_with_edges(self):
        node = {
            "__typename": "GraphSidecar",
            "edge_sidecar_to_children": {"edges": [1, 2, 3]}
        }
        ptype, count = _get_post_type_and_carousel(node)
        assert ptype == "carousel"
        assert count == 3

    def test_media_type_8_carousel_with_carousel_media_count(self):
        node = {"media_type": 8, "carousel_media_count": 5}
        ptype, count = _get_post_type_and_carousel(node)
        assert ptype == "carousel"
        assert count == 5

    def test_graph_sidecar_no_edges_uses_carousel_media_count(self):
        node = {
            "__typename": "GraphSidecar",
            "carousel_media_count": 4
        }
        ptype, count = _get_post_type_and_carousel(node)
        assert ptype == "carousel"
        assert count == 4

    def test_product_type_clips(self):
        node = {"product_type": "clips"}
        ptype, count = _get_post_type_and_carousel(node)
        assert ptype == "reel"
        assert count == 0

    def test_product_type_reel(self):
        node = {"product_type": "reel"}
        ptype, count = _get_post_type_and_carousel(node)
        assert ptype == "reel"
        assert count == 0

    def test_product_type_igtv(self):
        node = {"product_type": "igtv"}
        ptype, count = _get_post_type_and_carousel(node)
        assert ptype == "igtv"
        assert count == 0

    def test_graph_video_typename(self):
        node = {"__typename": "GraphVideo"}
        ptype, count = _get_post_type_and_carousel(node)
        assert ptype == "video"
        assert count == 0

    def test_is_video_true(self):
        node = {"is_video": True}
        ptype, count = _get_post_type_and_carousel(node)
        assert ptype == "video"
        assert count == 0

    def test_media_type_2_video(self):
        node = {"media_type": 2}
        ptype, count = _get_post_type_and_carousel(node)
        assert ptype == "video"
        assert count == 0

    def test_default_image(self):
        node = {}
        ptype, count = _get_post_type_and_carousel(node)
        assert ptype == "image"
        assert count == 0

    def test_media_type_1_image(self):
        node = {"media_type": 1}
        ptype, count = _get_post_type_and_carousel(node)
        assert ptype == "image"
        assert count == 0


# ─────────────────────────────────────────────────────────────────────────────
# _get_usertags
# ─────────────────────────────────────────────────────────────────────────────

class TestGetUsertags:
    def test_old_edge_media_to_tagged_user_format(self):
        node = {
            "edge_media_to_tagged_user": {
                "edges": [
                    {"node": {"user": {"username": "Alice"}}},
                    {"node": {"user": {"username": "Bob"}}},
                ]
            }
        }
        tags = _get_usertags(node)
        assert tags == ["alice", "bob"]

    def test_new_usertags_in_format(self):
        node = {
            "usertags": {
                "in": [
                    {"user": {"username": "Charlie"}},
                    {"user": {"username": "Dave"}},
                ]
            }
        }
        tags = _get_usertags(node)
        assert tags == ["charlie", "dave"]

    def test_empty_returns_empty_list(self):
        assert _get_usertags({}) == []

    def test_old_format_skips_missing_username(self):
        node = {
            "edge_media_to_tagged_user": {
                "edges": [
                    {"node": {"user": {"username": ""}}},
                    {"node": {"user": {"username": "Valid"}}},
                ]
            }
        }
        tags = _get_usertags(node)
        assert tags == ["valid"]

    def test_new_format_skips_missing_username(self):
        node = {
            "usertags": {
                "in": [
                    {"user": {"username": ""}},
                    {"user": {"username": "Eve"}},
                ]
            }
        }
        tags = _get_usertags(node)
        assert tags == ["eve"]

    def test_old_format_missing_node(self):
        node = {
            "edge_media_to_tagged_user": {
                "edges": [{"node": None}]
            }
        }
        tags = _get_usertags(node)
        assert tags == []


# ─────────────────────────────────────────────────────────────────────────────
# _get_display_url
# ─────────────────────────────────────────────────────────────────────────────

class TestGetDisplayUrl:
    def test_display_url_key(self):
        node = {"display_url": "https://example.com/image.jpg"}
        assert _get_display_url(node) == "https://example.com/image.jpg"

    def test_image_versions2_candidates_fallback(self):
        node = {
            "image_versions2": {
                "candidates": [
                    {"url": "https://cdn.example.com/img.jpg"},
                    {"url": "https://cdn.example.com/img_small.jpg"},
                ]
            }
        }
        assert _get_display_url(node) == "https://cdn.example.com/img.jpg"

    def test_display_uri_fallback(self):
        node = {"display_uri": "https://example.com/uri.jpg"}
        assert _get_display_url(node) == "https://example.com/uri.jpg"

    def test_missing_all_returns_empty(self):
        assert _get_display_url({}) == ""

    def test_empty_display_url_falls_to_image_versions2(self):
        node = {
            "display_url": "",
            "image_versions2": {
                "candidates": [{"url": "https://cdn.example.com/fallback.jpg"}]
            }
        }
        assert _get_display_url(node) == "https://cdn.example.com/fallback.jpg"

    def test_empty_image_versions2_falls_to_display_uri(self):
        node = {
            "display_url": "",
            "image_versions2": {"candidates": []},
            "display_uri": "https://example.com/uri.jpg"
        }
        assert _get_display_url(node) == "https://example.com/uri.jpg"


# ─────────────────────────────────────────────────────────────────────────────
# _get_dimensions
# ─────────────────────────────────────────────────────────────────────────────

class TestGetDimensions:
    def test_dimensions_dict_present(self):
        node = {"dimensions": {"width": 1080, "height": 1920}}
        result = _get_dimensions(node)
        assert result == {"width": 1080, "height": 1920}

    def test_original_width_height_fallback(self):
        node = {"original_width": 720, "original_height": 1280}
        result = _get_dimensions(node)
        assert result == {"width": 720, "height": 1280}

    def test_width_height_fallback(self):
        node = {"width": 640, "height": 480}
        result = _get_dimensions(node)
        assert result == {"width": 640, "height": 480}

    def test_empty_dimensions_falls_to_original(self):
        node = {"dimensions": {}, "original_width": 1080, "original_height": 1080}
        result = _get_dimensions(node)
        assert result == {"width": 1080, "height": 1080}

    def test_missing_all_returns_empty_dict(self):
        result = _get_dimensions({})
        assert result == {}

    def test_only_width_set(self):
        node = {"original_width": 500}
        result = _get_dimensions(node)
        assert result == {"width": 500, "height": 0}


# ─────────────────────────────────────────────────────────────────────────────
# _extract_location
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractLocation:
    def test_full_location_with_lat_lng_name_slug(self):
        node = {
            "location": {
                "name": "Eiffel Tower",
                "pk": "123456",
                "lat": 48.8584,
                "lng": 2.2945,
                "slug": "eiffel-tower"
            }
        }
        result = _extract_location(node)
        assert result is not None
        assert result["name"] == "Eiffel Tower"
        assert result["lat"] == 48.8584
        assert result["lng"] == 2.2945
        assert result["slug"] == "eiffel-tower"
        assert result["id"] == "123456"

    def test_location_without_coordinates(self):
        node = {
            "location": {"name": "New York City", "pk": "789"}
        }
        result = _extract_location(node)
        assert result is not None
        assert result["name"] == "New York City"
        assert "lat" not in result
        assert "lng" not in result

    def test_missing_name_returns_none(self):
        node = {"location": {"pk": "111", "lat": 1.0, "lng": 2.0}}
        assert _extract_location(node) is None

    def test_no_location_returns_none(self):
        assert _extract_location({}) is None

    def test_location_info_key_fallback(self):
        node = {
            "location_info": {"name": "Paris", "lat": 48.8566, "lng": 2.3522}
        }
        result = _extract_location(node)
        assert result is not None
        assert result["name"] == "Paris"

    def test_empty_location_returns_none(self):
        node = {"location": {}}
        assert _extract_location(node) is None

    def test_location_with_latitude_longitude_keys(self):
        node = {
            "location": {
                "name": "Tokyo",
                "latitude": 35.6762,
                "longitude": 139.6503,
            }
        }
        result = _extract_location(node)
        assert result is not None
        assert result["lat"] == 35.6762
        assert result["lng"] == 139.6503

    def test_location_with_id_key(self):
        node = {
            "location": {"name": "Rome", "id": "42"}
        }
        result = _extract_location(node)
        assert result["id"] == "42"

    def test_location_with_location_name_key(self):
        node = {
            "location": {"location_name": "Berlin"}
        }
        result = _extract_location(node)
        assert result["name"] == "Berlin"

    def test_invalid_coordinates_ignored(self):
        node = {
            "location": {
                "name": "Test Place",
                "lat": "not_a_number",
                "lng": "bad"
            }
        }
        result = _extract_location(node)
        assert result is not None
        assert result["name"] == "Test Place"
        assert "lat" not in result

    def test_location_slug_key(self):
        node = {
            "location": {
                "name": "London",
                "location_slug": "london-uk"
            }
        }
        result = _extract_location(node)
        assert result["slug"] == "london-uk"


# ─────────────────────────────────────────────────────────────────────────────
# _extract_music
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractMusic:
    def test_music_metadata_path(self):
        node = {
            "music_metadata": {
                "music_info": {
                    "artist_name": "Drake",
                    "song_name": "God's Plan"
                }
            }
        }
        artist, title = _extract_music(node)
        assert artist == "Drake"
        assert title == "God's Plan"

    def test_clips_metadata_music_info_path(self):
        node = {
            "clips_metadata": {
                "music_info": {
                    "artist_name": "Taylor Swift",
                    "song_name": "Shake It Off"
                }
            }
        }
        artist, title = _extract_music(node)
        assert artist == "Taylor Swift"
        assert title == "Shake It Off"

    def test_missing_returns_empty_strings(self):
        assert _extract_music({}) == ("", "")

    def test_music_metadata_with_artist_key(self):
        node = {
            "music_metadata": {
                "artist": "Eminem",
                "title": "Lose Yourself"
            }
        }
        artist, title = _extract_music(node)
        assert artist == "Eminem"
        assert title == "Lose Yourself"

    def test_nested_music_info_in_music_metadata(self):
        # music_metadata.music_info overrides music_metadata itself
        node = {
            "music_metadata": {
                "music_info": {
                    "artist_name": "Kanye",
                    "song_name": "Stronger"
                },
                "artist_name": "Other"
            }
        }
        artist, title = _extract_music(node)
        assert artist == "Kanye"
        assert title == "Stronger"

    def test_empty_clips_metadata_falls_through(self):
        node = {"clips_metadata": {}}
        assert _extract_music(node) == ("", "")

    def test_music_metadata_none(self):
        node = {"music_metadata": None, "clips_metadata": {"music_info": {"artist_name": "Jay-Z", "song_name": "99 Problems"}}}
        artist, title = _extract_music(node)
        assert artist == "Jay-Z"
        assert title == "99 Problems"


# ─────────────────────────────────────────────────────────────────────────────
# filter_bio_links
# ─────────────────────────────────────────────────────────────────────────────

class TestFilterBioLinks:
    def setup_method(self):
        self.social_domains = {
            "tiktok.com", "youtube.com", "twitter.com", "x.com",
            "facebook.com", "t.me", "linktr.ee", "beacons.ai", "bio.link"
        }

    def test_filters_out_social_domains(self):
        links = [
            {"url": "https://tiktok.com/@user"},
            {"url": "https://linktr.ee/user"},
        ]
        assert filter_bio_links(links, self.social_domains) == ""

    def test_returns_first_personal_com_site(self):
        links = [
            {"url": "https://tiktok.com/@user"},
            {"url": "https://mysite.com/home"},
        ]
        result = filter_bio_links(links, self.social_domains)
        assert result == "https://mysite.com/home"

    def test_empty_links_returns_empty(self):
        assert filter_bio_links([], self.social_domains) == ""

    def test_none_links_returns_empty(self):
        assert filter_bio_links(None, self.social_domains) == ""

    def test_all_social_returns_empty(self):
        links = [
            {"url": "https://twitter.com/user"},
            {"url": "https://youtube.com/channel"},
        ]
        assert filter_bio_links(links, self.social_domains) == ""

    def test_prefers_com_site_over_other_extensions(self):
        links = [
            {"url": "https://mysite.net"},
            {"url": "https://mysite.com"},
        ]
        result = filter_bio_links(links, self.social_domains)
        assert result == "https://mysite.com"

    def test_io_extension_prioritized(self):
        links = [{"url": "https://myapp.io"}]
        result = filter_bio_links(links, self.social_domains)
        assert result == "https://myapp.io"

    def test_plain_string_links(self):
        links = ["https://mysite.com"]
        result = filter_bio_links(links, self.social_domains)
        assert result == "https://mysite.com"

    def test_returns_first_when_no_com_match(self):
        links = [{"url": "https://mysite.xyz"}]
        result = filter_bio_links(links, self.social_domains)
        assert result == "https://mysite.xyz"

    def test_http_url(self):
        links = [{"url": "http://tiktok.com/@user"}, {"url": "http://myblog.com"}]
        result = filter_bio_links(links, self.social_domains)
        assert result == "http://myblog.com"

    def test_shop_extension(self):
        links = [{"url": "https://myshop.shop"}]
        result = filter_bio_links(links, self.social_domains)
        assert result == "https://myshop.shop"

    def test_empty_url_in_list_skipped(self):
        links = [{"url": ""}, {"url": "https://mysite.com"}]
        result = filter_bio_links(links, self.social_domains)
        assert result == "https://mysite.com"


# ─────────────────────────────────────────────────────────────────────────────
# detect_pinned_posts
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectPinnedPosts:
    def setup_method(self):
        self.now = time.time()
        self.max_age = 7 * 86400  # 7 days

    def _item(self, age_days):
        return {"taken_at": self.now - age_days * 86400}

    def test_detects_one_pinned_post_at_start(self):
        items = [
            self._item(30),  # old = pinned
            self._item(2),   # recent
            self._item(3),   # recent
            self._item(1),   # recent
        ]
        assert detect_pinned_posts(items, self.now, self.max_age) == 1

    def test_detects_two_pinned_posts(self):
        items = [
            self._item(60),  # old = pinned
            self._item(45),  # old = pinned
            self._item(2),   # recent
            self._item(1),   # recent
        ]
        assert detect_pinned_posts(items, self.now, self.max_age) == 2

    def test_no_pinned_all_recent_returns_zero(self):
        items = [
            self._item(1),
            self._item(2),
            self._item(3),
        ]
        assert detect_pinned_posts(items, self.now, self.max_age) == 0

    def test_single_item_returns_zero(self):
        items = [self._item(30)]
        assert detect_pinned_posts(items, self.now, self.max_age) == 0

    def test_empty_list_returns_zero(self):
        assert detect_pinned_posts([], self.now, self.max_age) == 0

    def test_all_old_returns_zero(self):
        # If all are old, no pinned detection (next isn't recent)
        items = [self._item(30), self._item(40), self._item(50)]
        assert detect_pinned_posts(items, self.now, self.max_age) == 0

    def test_max_3_pinned_detected(self):
        items = [
            self._item(100),
            self._item(90),
            self._item(80),
            self._item(2),
            self._item(1),
        ]
        # Only detects up to 3 pinned
        result = detect_pinned_posts(items, self.now, self.max_age)
        assert result == 3


# ─────────────────────────────────────────────────────────────────────────────
# check_dead_account
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckDeadAccount:
    def _make_user(self, edges=None, count=0):
        return {
            "edge_owner_to_timeline_media": {
                "edges": edges or [],
                "count": count,
            }
        }

    def _make_edge(self, days_ago):
        ts = int(time.time() - days_ago * 86400)
        return {"node": {"taken_at_timestamp": ts}}

    def test_no_edges_but_has_posts_returns_dead(self):
        user = self._make_user(edges=[], count=5)
        is_dead, days = check_dead_account(user)
        assert is_dead is True
        assert days == 9999

    def test_no_edges_no_posts_returns_not_dead(self):
        user = self._make_user(edges=[], count=0)
        is_dead, days = check_dead_account(user)
        assert is_dead is False
        assert days == 0

    def test_recent_post_returns_not_dead(self):
        user = self._make_user(edges=[self._make_edge(5)])
        is_dead, days = check_dead_account(user, dead_threshold_days=365)
        assert is_dead is False
        assert days < 10

    def test_old_post_returns_dead(self):
        user = self._make_user(edges=[self._make_edge(400)])
        is_dead, days = check_dead_account(user, dead_threshold_days=365)
        assert is_dead is True
        assert days > 365

    def test_multiple_edges_uses_newest(self):
        user = self._make_user(edges=[
            self._make_edge(500),
            self._make_edge(3),
        ])
        is_dead, days = check_dead_account(user, dead_threshold_days=365)
        assert is_dead is False
        assert days < 10

    def test_edge_with_zero_taken_at_skipped(self):
        user = self._make_user(edges=[{"node": {"taken_at_timestamp": 0}}], count=1)
        is_dead, days = check_dead_account(user)
        assert is_dead is True
        assert days == 9999


# ─────────────────────────────────────────────────────────────────────────────
# parse_profile
# ─────────────────────────────────────────────────────────────────────────────

class TestParseProfile:
    def setup_method(self):
        self.config = MCPConfig()

    def _make_user(self, **kwargs):
        defaults = {
            "id": "123456",
            "username": "testuser",
            "full_name": "Test User",
            "biography": "Test bio",
            "edge_followed_by": {"count": 1000},
            "edge_follow": {"count": 500},
            "edge_owner_to_timeline_media": {"count": 42},
            "category_name": "Creator",
            "bio_links": [],
            "external_url": "https://example.com",
            "is_private": False,
            "is_verified": True,
            "is_business_account": False,
            "profile_pic_url_hd": "https://cdn.example.com/pic.jpg",
        }
        defaults.update(kwargs)
        return defaults

    def test_builds_instagram_profile_from_old_api_format(self):
        user = self._make_user()
        profile = parse_profile(user, "testuser", self.config)
        assert profile.username == "testuser"
        assert profile.user_id == "123456"
        assert profile.full_name == "Test User"
        assert profile.followers == 1000
        assert profile.following == 500
        assert profile.posts_count == 42
        assert profile.is_verified is True
        assert profile.category == "Creator"

    def test_falls_back_to_argument_username_when_payload_omits_it(self):
        user = self._make_user(username="")
        profile = parse_profile(user, "fallback_user", self.config)
        assert profile.username == "fallback_user"

    def test_resolved_username_unknown_when_all_empty(self):
        user = self._make_user(username="")
        profile = parse_profile(user, "", self.config)
        assert profile.username == "unknown"

    def test_profile_pic_hd_preferred_over_regular(self):
        user = self._make_user(
            profile_pic_url_hd="https://example.com/hd.jpg",
            profile_pic_url="https://example.com/regular.jpg"
        )
        profile = parse_profile(user, "testuser", self.config)
        assert profile.profile_pic_url == "https://example.com/hd.jpg"

    def test_profile_pic_fallback_to_regular(self):
        user = self._make_user(
            profile_pic_url_hd="",
            profile_pic_url="https://example.com/regular.jpg"
        )
        profile = parse_profile(user, "testuser", self.config)
        assert profile.profile_pic_url == "https://example.com/regular.jpg"

    def test_is_private_true(self):
        user = self._make_user(is_private=True)
        profile = parse_profile(user, "testuser", self.config)
        assert profile.is_private is True

    def test_is_business_true(self):
        user = self._make_user(is_business_account=True)
        profile = parse_profile(user, "testuser", self.config)
        assert profile.is_business is True

    def test_extended_fields(self):
        user = self._make_user(
            highlight_reel_count=5,
            pronouns=["he/him"],
            is_professional_account=True,
            account_type=2,
            has_clips=True,
            has_guides=True,
            contact_phone_number="+1234567890",
            public_email="test@example.com",
            city_name="New York",
            usertags_count=100,
            is_joined_recently=True,
            overall_category_name="Music"
        )
        profile = parse_profile(user, "testuser", self.config)
        assert profile.highlight_count == 5
        assert profile.pronouns == ["he/him"]
        assert profile.is_professional is True
        assert profile.account_type == 2
        assert profile.has_reels is True
        assert profile.has_guides is True
        assert profile.contact_phone == "+1234567890"
        assert profile.public_email == "test@example.com"
        assert profile.city == "New York"
        assert profile.usertags_count == 100
        assert profile.is_new_account is True
        assert profile.overall_category == "Music"

    def test_bio_links_filtered(self):
        user = self._make_user(
            bio_links=[
                {"url": "https://tiktok.com/@user"},
                {"url": "https://mysite.com"}
            ]
        )
        profile = parse_profile(user, "testuser", self.config)
        assert profile.website == "https://mysite.com"


# ─────────────────────────────────────────────────────────────────────────────
# parse_feed_tags
# ─────────────────────────────────────────────────────────────────────────────

class TestParseFeedTags:
    def _make_edge(self, shortcode, taken_at, caption="", tags=None):
        node = {
            "shortcode": shortcode,
            "taken_at_timestamp": taken_at,
            "edge_media_to_caption": {
                "edges": [{"node": {"text": caption}}] if caption else []
            },
        }
        if tags:
            node["edge_media_to_tagged_user"] = {
                "edges": [{"node": {"user": {"username": t}}} for t in tags]
            }
        return {"node": node}

    def test_empty_edges_returns_empty_feed_tag_result(self):
        user = {"edge_owner_to_timeline_media": {"edges": []}}
        result = parse_feed_tags(user)
        assert isinstance(result, FeedTagResult)
        assert result.posts_checked == 0
        assert result.tags == []

    def test_missing_key_returns_empty(self):
        result = parse_feed_tags({})
        assert result.posts_checked == 0

    def test_extracts_tags_mentions_shortcodes_timestamps(self):
        now = int(time.time())
        edge = self._make_edge(
            shortcode="ABC123",
            taken_at=now - 3600,
            caption="Hello @friend #cool",
            tags=["tagged_user"]
        )
        user = {"edge_owner_to_timeline_media": {"edges": [edge]}}
        result = parse_feed_tags(user, max_posts=12, max_age_days=30)
        assert "tagged_user" in result.tags
        assert "friend" in result.tags
        assert result.posts_checked == 1
        assert result.tag_shortcodes.get("tagged_user") == "ABC123"


# ─────────────────────────────────────────────────────────────────────────────
# parse_feed_tags_from_edges
# ─────────────────────────────────────────────────────────────────────────────

class TestParseFeedTagsFromEdges:
    def _edge(self, shortcode, age_days=1, caption="", tags=None, coauthors=None):
        ts = int(time.time() - age_days * 86400)
        node = {
            "shortcode": shortcode,
            "taken_at_timestamp": ts,
        }
        if caption:
            node["edge_media_to_caption"] = {
                "edges": [{"node": {"text": caption}}]
            }
        if tags:
            node["edge_media_to_tagged_user"] = {
                "edges": [{"node": {"user": {"username": t}}} for t in tags]
            }
        if coauthors:
            node["coauthor_producers"] = [{"username": c} for c in coauthors]
        return {"node": node}

    def test_deduplicates_shortcodes(self):
        edge = self._edge("DUP001", age_days=1)
        result = parse_feed_tags_from_edges([edge, edge])
        assert result.posts_checked == 1

    def test_respects_max_posts_limit(self):
        edges = [self._edge(f"SC{i:03d}", age_days=1) for i in range(10)]
        result = parse_feed_tags_from_edges(edges, max_posts=3)
        assert result.posts_checked == 3

    def test_empty_edges_returns_empty(self):
        result = parse_feed_tags_from_edges([])
        assert result.posts_checked == 0

    def test_skips_pinned_when_detect_pinned_true(self):
        now = time.time()
        old_ts = int(now - 60 * 86400)  # 60 days old
        new_ts = int(now - 1 * 86400)   # 1 day old

        edges = [
            {"node": {"shortcode": "PINNED", "taken_at_timestamp": old_ts}},
            {"node": {"shortcode": "RECENT1", "taken_at_timestamp": new_ts}},
            {"node": {"shortcode": "RECENT2", "taken_at_timestamp": new_ts}},
            {"node": {"shortcode": "RECENT3", "taken_at_timestamp": new_ts}},
        ]
        result = parse_feed_tags_from_edges(
            edges, max_posts=10, max_age_days=30, detect_pinned=True
        )
        codes = [p.shortcode for p in result.posts]
        assert "PINNED" not in codes
        assert "RECENT1" in codes

    def test_age_based_stop_when_no_date_range(self):
        edges = [
            self._edge("NEW001", age_days=1),
            self._edge("OLD001", age_days=100),  # older than max_age_days=30
        ]
        result = parse_feed_tags_from_edges(edges, max_posts=50, max_age_days=30)
        codes = [p.shortcode for p in result.posts]
        assert "NEW001" in codes
        assert "OLD001" not in codes

    def test_date_range_filtering_skips_posts_outside_window(self):
        now = int(time.time())
        in_range_ts = now - 5 * 86400
        out_range_ts = now - 50 * 86400
        date_range = DateRange(since=now - 10 * 86400, until=now)

        edges = [
            {"node": {"shortcode": "IN_RANGE", "taken_at_timestamp": in_range_ts}},
            {"node": {"shortcode": "OUT_RANGE", "taken_at_timestamp": out_range_ts}},
        ]
        result = parse_feed_tags_from_edges(
            edges, max_posts=50, date_range=date_range
        )
        codes = [p.shortcode for p in result.posts]
        assert "IN_RANGE" in codes
        assert "OUT_RANGE" not in codes

    def test_date_range_forces_min_3_pinned(self):
        now = time.time()
        dr = DateRange(since=int(now - 10 * 86400))
        old_ts = int(now - 60 * 86400)
        new_ts = int(now - 2 * 86400)

        edges = (
            [{"node": {"shortcode": f"OLD{i}", "taken_at_timestamp": old_ts}} for i in range(3)]
            + [{"node": {"shortcode": f"NEW{i}", "taken_at_timestamp": new_ts}} for i in range(5)]
        )
        result = parse_feed_tags_from_edges(
            edges, max_posts=50, max_age_days=30, detect_pinned=True, date_range=dr
        )
        # At least 3 pinned posts were skipped
        codes = [p.shortcode for p in result.posts]
        for i in range(3):
            assert f"OLD{i}" not in codes

    def test_posts_with_tags_incremented(self):
        edges = [self._edge("TAG001", age_days=1, tags=["alice"])]
        result = parse_feed_tags_from_edges(edges, max_posts=10, max_age_days=30)
        assert result.posts_with_tags == 1

    def test_sponsor_tags_extracted(self):
        now = int(time.time()) - 3600
        node = {
            "shortcode": "SPONSOR001",
            "taken_at_timestamp": now,
            "edge_media_to_sponsor_user": {
                "edges": [{"node": {"sponsor": {"username": "brandX"}}}]
            }
        }
        result = parse_feed_tags_from_edges([{"node": node}], max_posts=10, max_age_days=30)
        assert result.posts[0].sponsor_tags == ["brandx"]

    def test_coauthors_extracted(self):
        edges = [self._edge("COAUTH001", age_days=1, coauthors=["collab_user"])]
        result = parse_feed_tags_from_edges(edges, max_posts=10, max_age_days=30)
        assert result.posts[0].coauthors == ["collab_user"]

    def test_no_taken_at_age_is_zero(self):
        edges = [{"node": {"shortcode": "NOTIME001"}}]
        result = parse_feed_tags_from_edges(edges, max_posts=10, max_age_days=30)
        assert result.posts[0].age_days == 0.0

    def test_invalid_timestamp_ts_str_empty(self):
        # Overflow timestamp should produce empty ts_str
        edges = [{"node": {"shortcode": "BADTS", "taken_at_timestamp": 9999999999999}}]
        result = parse_feed_tags_from_edges(edges, max_posts=10, max_age_days=30)
        # Should not crash, ts_str may be empty
        assert isinstance(result.posts[0].taken_at_str, str)

    def test_full_post_info_built_correctly(self):
        now = int(time.time()) - 3600
        node = {
            "shortcode": "FULL001",
            "taken_at_timestamp": now,
            "like_count": 500,
            "comment_count": 20,
            "play_count": 10000,
            "caption": {"text": "Test #photo @mention"},
            "image_versions2": {"candidates": [{"url": "https://cdn.example.com/img.jpg"}]},
            "original_width": 1080,
            "original_height": 1080,
            "media_type": 1,
            "is_video": False,
        }
        result = parse_feed_tags_from_edges([{"node": node}], max_posts=10, max_age_days=30)
        post = result.posts[0]
        assert post.shortcode == "FULL001"
        assert post.likes == 500
        assert post.comments == 20
        assert post.hashtags == ["photo"]
        assert post.mentions == ["mention"]
        assert post.display_url == "https://cdn.example.com/img.jpg"
        assert post.width == 1080
        assert post.height == 1080


# ─────────────────────────────────────────────────────────────────────────────
# extract_page_info
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractPageInfo:
    def test_extracts_end_cursor_has_next_page_first_page_edges(self):
        user = {
            "edge_owner_to_timeline_media": {
                "page_info": {
                    "end_cursor": "cursor_abc",
                    "has_next_page": True,
                },
                "edges": [{"node": {"shortcode": "ABC"}}]
            }
        }
        result = extract_page_info(user)
        assert result["end_cursor"] == "cursor_abc"
        assert result["has_next_page"] is True
        assert len(result["first_page_edges"]) == 1

    def test_missing_page_info_returns_defaults(self):
        user = {}
        result = extract_page_info(user)
        assert result["end_cursor"] == ""
        assert result["has_next_page"] is False
        assert result["first_page_edges"] == []


# ─────────────────────────────────────────────────────────────────────────────
# _pk_to_timestamp
# ─────────────────────────────────────────────────────────────────────────────

class TestPkToTimestamp:
    def test_valid_pk_returns_timestamp_in_bounds(self):
        # A realistic Instagram pk (encoded ~2023)
        # pk = (ts_ms - epoch_ms) << 23 | ...
        # Let's use a known-good pk from the wild
        pk = "3230310414386649088"
        ts = _pk_to_timestamp(pk)
        assert _IG_TS_MIN <= ts <= _IG_TS_MAX

    def test_invalid_pk_non_numeric_returns_zero(self):
        assert _pk_to_timestamp("not_a_number") == 0

    def test_pk_yielding_out_of_bounds_ts_returns_zero(self):
        # A massive pk will yield a timestamp > _IG_TS_MAX
        assert _pk_to_timestamp("9999999999999999999999999") == 0

    def test_empty_string_returns_zero(self):
        assert _pk_to_timestamp("") == 0

    def test_none_returns_zero(self):
        assert _pk_to_timestamp(None) == 0


# ─────────────────────────────────────────────────────────────────────────────
# shortcode_to_media_id
# ─────────────────────────────────────────────────────────────────────────────

class TestShortcodeToMediaId:
    def test_known_conversion(self):
        # "DNnx22NOGnt" — verify it produces a numeric string
        result = shortcode_to_media_id("DNnx22NOGnt")
        assert result.isdigit()

    def test_short_code_A_is_zero(self):
        assert shortcode_to_media_id("A") == "0"

    def test_two_chars(self):
        # "AB" = 0*64 + 1 = 1, then 1*64 + 1 = 65? No: A=0, B=1
        # A=0: n=0; B=1: n=0*64+1=1 → "1"
        assert shortcode_to_media_id("AB") == "1"

    def test_invalid_character_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid shortcode character"):
            shortcode_to_media_id("abc!")

    def test_empty_string_returns_zero(self):
        assert shortcode_to_media_id("") == "0"

    def test_all_A_is_zero(self):
        assert shortcode_to_media_id("AAA") == "0"


# ─────────────────────────────────────────────────────────────────────────────
# parse_comments
# ─────────────────────────────────────────────────────────────────────────────

class TestParseComments:
    def _raw_comment(self, pk="1", text="Test comment", username="user1",
                     created_at=1700000000, comment_like_count=5,
                     child_comment_count=0, has_translation=False,
                     giphy=None):
        comment = {
            "pk": pk,
            "text": text,
            "created_at": created_at,
            "comment_like_count": comment_like_count,
            "child_comment_count": child_comment_count,
            "has_translation": has_translation,
            "user": {
                "username": username,
                "pk": f"uid_{pk}",
                "full_name": f"Full {username}",
                "is_verified": False,
                "is_private": False,
            }
        }
        if giphy is not None:
            comment["giphy_media_info"] = giphy
        return comment

    def _caption_raw(self):
        return {
            "pk": "cap1",
            "text": "This is the caption #nice",
            "created_at": 1699990000,
            "user": {
                "username": "author",
                "pk": "auth_id",
                "full_name": "Author Name",
                "is_verified": True,
            }
        }

    def test_caption_raw_included_first_with_is_caption_true(self):
        comments = parse_comments([], self._caption_raw(), max_comments=10)
        assert len(comments) == 1
        assert comments[0].is_caption is True
        assert comments[0].username == "author"
        assert comments[0].text == "This is the caption #nice"

    def test_regular_comments_parsed(self):
        raws = [self._raw_comment("1", "First comment"), self._raw_comment("2", "Second comment")]
        result = parse_comments(raws, max_comments=10)
        assert len(result) == 2
        assert result[0].text == "First comment"
        assert result[1].text == "Second comment"

    def test_gif_comment_has_gif_true_and_gif_url_set(self):
        giphy = {
            "images": {
                "fixed_height": {"url": "https://media.giphy.com/gif.gif"}
            }
        }
        raw = self._raw_comment("3", text="", giphy=giphy)
        result = parse_comments([raw], max_comments=10)
        assert result[0].has_gif is True
        assert result[0].gif_url == "https://media.giphy.com/gif.gif"

    def test_has_translation_true_for_non_english(self):
        raw = self._raw_comment("4", has_translation=True)
        result = parse_comments([raw], max_comments=10)
        assert result[0].has_translation is True

    def test_max_comments_respected(self):
        raws = [self._raw_comment(str(i)) for i in range(20)]
        result = parse_comments(raws, max_comments=5)
        assert len(result) == 5

    def test_none_entries_skipped(self):
        result = parse_comments([None, None], max_comments=10)
        assert result == []

    def test_malformed_non_dict_entries_skipped(self):
        result = parse_comments(["not_a_dict", 42], max_comments=10)
        assert result == []

    def test_caption_raw_none_excluded(self):
        raws = [self._raw_comment("1")]
        result = parse_comments(raws, caption_raw=None, max_comments=10)
        assert len(result) == 1
        assert result[0].is_caption is False

    def test_comment_with_user_id_key_fallback(self):
        raw = {
            "pk": "5",
            "text": "hello",
            "created_at": 1700000000,
            "user": {"username": "testuser", "id": "user_id_999"},
        }
        result = parse_comments([raw], max_comments=10)
        assert result[0].user_id == "user_id_999"

    def test_caption_raw_not_dict_excluded(self):
        result = parse_comments([], caption_raw="not_a_dict", max_comments=10)
        assert result == []

    def test_caption_with_zero_created_at(self):
        cap = {
            "pk": "c1",
            "text": "caption text",
            "created_at": 0,
            "user": {"username": "author"}
        }
        result = parse_comments([], caption_raw=cap, max_comments=10)
        assert result[0].created_at_str == ""

    def test_max_comments_with_caption(self):
        # With caption, max_comments still counts only regular comments
        raws = [self._raw_comment(str(i)) for i in range(10)]
        result = parse_comments(raws, caption_raw=self._caption_raw(), max_comments=5)
        # 1 caption + 5 regular = 6 total
        assert len(result) == 6
        assert result[0].is_caption is True


# ─────────────────────────────────────────────────────────────────────────────
# parse_tagged_tab_edges
# ─────────────────────────────────────────────────────────────────────────────

class TestParseTaggedTabEdges:
    # Use a valid pk that will decode to a good timestamp
    _VALID_PK = "3230310414386649088"

    def _edge(self, code="SC001", pk=None, media_type=1,
              poster_username="poster", likes=100, comments=5,
              caption_text="hello", display_url=None, carousel=False):
        pk = pk or self._VALID_PK
        node = {
            "code": code,
            "pk": pk,
            "media_type": media_type,
            "user": {"username": poster_username, "pk": "uid123"},
            "like_count": likes,
            "comment_count": comments,
            "caption": {"text": caption_text},
        }
        if display_url:
            node["image_versions2"] = {"candidates": [{"url": display_url, "width": 1080, "height": 1920}]}
        if carousel:
            node["carousel_media"] = [
                {"image_versions2": {"candidates": [{"url": "https://cdn.example.com/slide.jpg", "width": 640, "height": 480}]}}
            ]
        return {"node": node}

    def test_basic_fields_extracted(self):
        result = parse_tagged_tab_edges([self._edge()])
        assert len(result) == 1
        post = result[0]
        assert post.shortcode == "SC001"
        assert post.poster_username == "poster"
        assert post.likes == 100
        assert post.comments == 5
        assert post.post_type == "image"

    def test_empty_returns_empty_list(self):
        assert parse_tagged_tab_edges([]) == []

    def test_max_posts_limit(self):
        edges = [self._edge(f"SC{i:03d}") for i in range(10)]
        result = parse_tagged_tab_edges(edges, max_posts=3)
        assert len(result) == 3

    def test_carousel_image_versions2(self):
        result = parse_tagged_tab_edges([self._edge(display_url="https://cdn.example.com/img.jpg")])
        assert result[0].display_url == "https://cdn.example.com/img.jpg"
        assert result[0].width == 1080
        assert result[0].height == 1920

    def test_carousel_media_fallback(self):
        result = parse_tagged_tab_edges([self._edge(carousel=True)])
        assert result[0].display_url == "https://cdn.example.com/slide.jpg"
        assert result[0].width == 640
        assert result[0].height == 480

    def test_original_width_height_fallback(self):
        edge = {
            "node": {
                "code": "WFALL",
                "pk": self._VALID_PK,
                "media_type": 1,
                "user": {"username": "user"},
                "original_width": 720,
                "original_height": 1280,
            }
        }
        result = parse_tagged_tab_edges([edge])
        assert result[0].width == 720
        assert result[0].height == 1280

    def test_edge_unwrapped(self):
        # Edge may already be unwrapped (no "node" key)
        node = {
            "code": "UNWRAPPED",
            "pk": self._VALID_PK,
            "media_type": 1,
            "user": {"username": "user"},
        }
        result = parse_tagged_tab_edges([node])
        assert result[0].shortcode == "UNWRAPPED"

    def test_non_dict_node_skipped(self):
        result = parse_tagged_tab_edges(["not_a_dict"])
        assert result == []

    def test_media_type_video(self):
        result = parse_tagged_tab_edges([self._edge(media_type=2)])
        assert result[0].post_type == "video"

    def test_media_type_carousel(self):
        result = parse_tagged_tab_edges([self._edge(media_type=8)])
        assert result[0].post_type == "carousel"

    def test_unknown_media_type(self):
        result = parse_tagged_tab_edges([self._edge(media_type=99)])
        assert result[0].post_type == "unknown"

    def test_caption_long_truncated(self):
        long_caption = "x" * 600
        result = parse_tagged_tab_edges([self._edge(caption_text=long_caption)])
        assert len(result[0].caption) == 500


# ─────────────────────────────────────────────────────────────────────────────
# parse_repost_items
# ─────────────────────────────────────────────────────────────────────────────

class TestParseRepostItems:
    _VALID_PK = "3230310414386649088"

    def _item(self, code="SC001", pk=None, media_type=1, product_type="",
              orig_username="creator", likes=200, comments=10,
              caption_text="original post", display_url=None):
        pk = pk or self._VALID_PK
        media = {
            "code": code,
            "pk": pk,
            "media_type": media_type,
            "product_type": product_type,
            "user": {"username": orig_username, "pk": "creator_id"},
            "like_count": likes,
            "comment_count": comments,
            "caption": {"text": caption_text},
        }
        if display_url:
            media["image_versions2"] = {"candidates": [{"url": display_url, "width": 1080, "height": 720}]}
        return {"media": media}

    def test_basic_fields(self):
        result = parse_repost_items([self._item()])
        assert len(result) == 1
        item = result[0]
        assert item.shortcode == "SC001"
        assert item.orig_username == "creator"
        assert item.likes == 200
        assert item.post_type == "image"

    def test_empty_returns_empty_list(self):
        assert parse_repost_items([]) == []

    def test_max_posts_limit(self):
        items = [self._item(f"SC{i:03d}") for i in range(10)]
        result = parse_repost_items(items, max_posts=2)
        assert len(result) == 2

    def test_product_type_clips_becomes_reels(self):
        result = parse_repost_items([self._item(product_type="clips")])
        assert result[0].post_type == "reels"

    def test_display_url_from_image_versions2(self):
        result = parse_repost_items([self._item(display_url="https://cdn.example.com/img.jpg")])
        assert result[0].display_url == "https://cdn.example.com/img.jpg"

    def test_carousel_media_fallback(self):
        media = {
            "code": "CAR001",
            "pk": self._VALID_PK,
            "media_type": 8,
            "user": {"username": "creator"},
            "carousel_media": [
                {"image_versions2": {"candidates": [{"url": "https://cdn.example.com/slide.jpg", "width": 800, "height": 600}]}}
            ]
        }
        result = parse_repost_items([{"media": media}])
        assert result[0].display_url == "https://cdn.example.com/slide.jpg"
        assert result[0].width == 800

    def test_original_width_height_fallback(self):
        media = {
            "code": "WFALL",
            "pk": self._VALID_PK,
            "media_type": 1,
            "user": {"username": "creator"},
            "original_width": 720,
            "original_height": 480,
        }
        result = parse_repost_items([{"media": media}])
        assert result[0].width == 720
        assert result[0].height == 480

    def test_non_dict_media_skipped(self):
        result = parse_repost_items([{"media": "not_a_dict"}])
        assert result == []

    def test_caption_long_truncated(self):
        long_cap = "z" * 600
        result = parse_repost_items([self._item(caption_text=long_cap)])
        assert len(result[0].caption) == 500

    def test_video_post_type(self):
        result = parse_repost_items([self._item(media_type=2)])
        assert result[0].post_type == "video"


# ─────────────────────────────────────────────────────────────────────────────
# parse_reels_edges
# ─────────────────────────────────────────────────────────────────────────────

class TestParseReelsEdges:
    _VALID_PK = "3230310414386649088"

    def _edge(self, code="REEL001", pk=None, taken_at=None,
              play_count=50000, like_count=1000, comment_count=100,
              thumbnail_url=None, is_pinned=False, coauthors=None):
        pk = pk or self._VALID_PK
        taken_at = taken_at or int(time.time()) - 86400
        media = {
            "code": code,
            "pk": pk,
            "taken_at": taken_at,
            "play_count": play_count,
            "like_count": like_count,
            "comment_count": comment_count,
            "is_pinned": is_pinned,
        }
        if thumbnail_url:
            media["image_versions2"] = {
                "candidates": [{"url": thumbnail_url, "width": 720, "height": 1280}]
            }
        if coauthors:
            media["coauthor_producers"] = [{"id": c} for c in coauthors]
        return {"node": {"media": media}}

    def test_basic_reel_fields(self):
        result = parse_reels_edges([self._edge()])
        assert len(result) == 1
        reel = result[0]
        assert reel.shortcode == "REEL001"
        assert reel.play_count == 50000
        assert reel.like_count == 1000
        assert reel.comment_count == 100

    def test_empty_returns_empty_list(self):
        assert parse_reels_edges([]) == []

    def test_max_reels_limit(self):
        edges = [self._edge(f"REEL{i:03d}") for i in range(10)]
        result = parse_reels_edges(edges, max_reels=3)
        assert len(result) == 3

    def test_thumbnail_from_image_versions2(self):
        result = parse_reels_edges([self._edge(thumbnail_url="https://cdn.example.com/thumb.jpg")])
        assert result[0].thumbnail_url == "https://cdn.example.com/thumb.jpg"
        assert result[0].width == 720
        assert result[0].height == 1280

    def test_is_pinned_true(self):
        result = parse_reels_edges([self._edge(is_pinned=True)])
        assert result[0].is_pinned is True

    def test_coauthor_ids_extracted(self):
        result = parse_reels_edges([self._edge(coauthors=["id_aaa", "id_bbb"])])
        assert "id_aaa" in result[0].coauthor_ids
        assert "id_bbb" in result[0].coauthor_ids

    def test_taken_at_from_pk_when_missing(self):
        media = {
            "code": "NOTS",
            "pk": self._VALID_PK,
            "play_count": 100,
        }
        edge = {"node": {"media": media}}
        result = parse_reels_edges([edge])
        # Should have decoded timestamp from pk
        assert result[0].taken_at > 0

    def test_original_width_height_fallback(self):
        media = {
            "code": "WFALL",
            "pk": self._VALID_PK,
            "taken_at": int(time.time()) - 3600,
            "play_count": 1000,
            "original_width": 480,
            "original_height": 854,
        }
        edge = {"node": {"media": media}}
        result = parse_reels_edges([edge])
        assert result[0].width == 480
        assert result[0].height == 854

    def test_edge_unwrapped_no_node(self):
        media = {
            "code": "UNWR",
            "pk": self._VALID_PK,
            "taken_at": int(time.time()) - 3600,
            "play_count": 5000,
        }
        # Edge has no 'node', media is directly inside
        edge = {"media": media}
        result = parse_reels_edges([edge])
        assert result[0].shortcode == "UNWR"

    def test_non_dict_media_skipped(self):
        edge = {"node": {"media": "not_a_dict"}}
        result = parse_reels_edges([edge])
        assert result == []

    def test_non_dict_node_skipped(self):
        result = parse_reels_edges(["not_a_dict"])
        assert result == []

    def test_coauthor_pk_key(self):
        media = {
            "code": "COPK",
            "pk": self._VALID_PK,
            "taken_at": int(time.time()) - 3600,
            "play_count": 100,
            "coauthor_producers": [{"pk": "pk_author"}]
        }
        result = parse_reels_edges([{"node": {"media": media}}])
        assert "pk_author" in result[0].coauthor_ids


# ─────────────────────────────────────────────────────────────────────────────
# parse_post_html
# ─────────────────────────────────────────────────────────────────────────────

class TestParsePostHtml:
    def _build_html(self, fields: dict) -> str:
        """Build minimal fake HTML with embedded JSON fields in a script tag."""
        json_parts = []

        if "taken_at" in fields:
            json_parts.append(f'"taken_at": {fields["taken_at"]}')
        if "username" in fields:
            json_parts.append(f'"username": "{fields["username"]}"')
        if "full_name" in fields:
            json_parts.append(f'"full_name": "{fields["full_name"]}"')
        if "user_id" in fields:
            json_parts.append(f'"owner": {{"id": "{fields["user_id"]}","other": 1}}')
        if "is_verified" in fields:
            val = "true" if fields["is_verified"] else "false"
            json_parts.append(f'"is_verified": {val}')
        if "like_count" in fields:
            json_parts.append(f'"like_count": {fields["like_count"]}')
        if "comment_count" in fields:
            json_parts.append(f'"comment_count": {fields["comment_count"]}')
        if "view_count" in fields:
            json_parts.append(f'"view_count": {fields["view_count"]}')
        if "play_count" in fields:
            json_parts.append(f'"play_count": {fields["play_count"]}')
        if "media_type" in fields:
            json_parts.append(f'"media_type": {fields["media_type"]}')
        if "product_type" in fields:
            json_parts.append(f'"product_type": "{fields["product_type"]}"')
        if "carousel_media_count" in fields:
            json_parts.append(f'"carousel_media_count": {fields["carousel_media_count"]}')
        if "original_width" in fields:
            json_parts.append(f'"original_width": {fields["original_width"]}')
        if "original_height" in fields:
            json_parts.append(f'"original_height": {fields["original_height"]}')
        if "display_url" in fields:
            json_parts.append(f'"display_url": "{fields["display_url"]}"')
        if "video_duration" in fields:
            json_parts.append(f'"video_duration": {fields["video_duration"]}')
        if "caption_text" in fields:
            # Embed as {"caption": {"text": "..."}} JSON-encoded
            cap = fields["caption_text"].replace('"', '\\"').replace('\n', '\\n')
            json_parts.append(f'"caption": {{"text": "{cap}", "pk": "123"}}')
        if "location" in fields:
            loc = fields["location"]
            json_parts.append(f'"location": {{"name": "{loc["name"]}", "lat": {loc.get("lat", 0)}, "lng": {loc.get("lng", 0)}, "pk": "{loc.get("pk", "")}"}}')
        if "coauthors" in fields:
            coauthor_json = ", ".join(f'{{"username": "{u}"}}' for u in fields["coauthors"])
            json_parts.append(f'"coauthor_producers": [{coauthor_json}]')
        if "music_artist" in fields:
            json_parts.append(f'"artist": {{"name": "{fields["music_artist"]}", "pk": "art1"}}')
        if "music_title" in fields:
            json_parts.append(f'"title": "{fields["music_title"]}"')

        json_content = "{" + ", ".join(json_parts) + "}"
        return f'<script type="application/json">{json_content}</script>'

    def test_empty_html_all_zero_defaults(self):
        info = parse_post_html("", "TESTCODE")
        assert info.shortcode == "TESTCODE"
        assert info.taken_at == 0
        assert info.likes == 0
        assert info.comments == 0
        assert info.username == ""

    def test_taken_at_extraction(self):
        html = self._build_html({"taken_at": 1700000000})
        info = parse_post_html(html, "SC001")
        assert info.taken_at == 1700000000
        assert "2023" in info.taken_at_str

    def test_username_fullname_user_id_is_verified(self):
        html = self._build_html({
            "username": "testuser",
            "full_name": "Test User",
            "user_id": "123456789",
            "is_verified": True,
        })
        info = parse_post_html(html, "SC002")
        assert info.username == "testuser"
        assert info.full_name == "Test User"
        assert info.user_id == "123456789"
        assert info.is_verified is True

    def test_is_verified_false(self):
        html = self._build_html({"is_verified": False})
        info = parse_post_html(html, "SC003")
        assert info.is_verified is False

    def test_likes_comments_views_plays(self):
        html = self._build_html({
            "like_count": 12345,
            "comment_count": 678,
            "view_count": 99999,
            "play_count": 50000,
        })
        info = parse_post_html(html, "SC004")
        assert info.likes == 12345
        assert info.comments == 678
        assert info.view_count == 99999
        assert info.play_count == 50000

    def test_media_type_and_product_type(self):
        html = self._build_html({"media_type": 2, "product_type": "clips"})
        info = parse_post_html(html, "SC005")
        assert info.media_type == 2
        assert info.product_type == "clips"
        assert info.post_type == "reels"

    def test_media_type_image_post_type(self):
        html = self._build_html({"media_type": 1})
        info = parse_post_html(html, "SC006")
        assert info.post_type == "image"

    def test_media_type_carousel(self):
        html = self._build_html({"media_type": 8, "carousel_media_count": 5})
        info = parse_post_html(html, "SC007")
        assert info.carousel_count == 5
        assert info.post_type == "carousel"

    def test_caption_with_newline_escapes(self):
        html = self._build_html({"caption_text": "Line1\nLine2"})
        info = parse_post_html(html, "SC008")
        assert "\n" in info.caption or "Line1" in info.caption

    def test_dimensions(self):
        html = self._build_html({"original_width": 1080, "original_height": 1920})
        info = parse_post_html(html, "SC009")
        assert info.width == 1080
        assert info.height == 1920

    def test_display_url(self):
        html = self._build_html({"display_url": "https://scontent.example.com/img.jpg"})
        info = parse_post_html(html, "SC010")
        assert info.display_url == "https://scontent.example.com/img.jpg"

    def test_coauthors_extraction(self):
        html = self._build_html({"coauthors": ["collab1", "collab2"]})
        info = parse_post_html(html, "SC011")
        assert "collab1" in info.coauthors
        assert "collab2" in info.coauthors

    def test_music_artist_and_title(self):
        html = self._build_html({
            "music_artist": "BTS",
            "music_title": "Dynamite"
        })
        info = parse_post_html(html, "SC012")
        assert info.music_artist == "BTS"
        assert info.music_title == "Dynamite"

    def test_location_extraction(self):
        html = self._build_html({
            "location": {"name": "Central Park", "lat": 40.7851, "lng": -73.9683, "pk": "loc123"}
        })
        info = parse_post_html(html, "SC013")
        assert info.location.name == "Central Park"
        assert info.location.lat == pytest.approx(40.7851)
        assert info.location.lng == pytest.approx(-73.9683)
        assert info.location.maps_url.startswith("https://www.google.com/maps")

    def test_video_duration(self):
        html = self._build_html({"video_duration": 30.5})
        info = parse_post_html(html, "SC014")
        assert info.duration_secs == pytest.approx(30.5)

    def test_caption_hashtags_and_mentions_extracted(self):
        html = self._build_html({"caption_text": "Check #travel @friend out!"})
        info = parse_post_html(html, "SC015")
        assert "travel" in info.hashtags
        assert "friend" in info.mentions

    def test_taken_at_out_of_bounds_ignored(self):
        # timestamp of 1 is way below _IG_TS_MIN
        html = '<script type="application/json">{"taken_at": 1}</script>'
        info = parse_post_html(html, "SC016")
        assert info.taken_at == 0

    def test_fallback_script_block_with_taken_at(self):
        # Script without type="application/json" but containing taken_at
        html = f'<script>var data = {{"taken_at": 1700000000, "like_count": 999}};</script>'
        info = parse_post_html(html, "SC017")
        assert info.likes == 999

    def test_location_with_zero_lat_lng_no_maps_url(self):
        html = '<script type="application/json">{"location": {"name": "Unknown", "lat": 0, "lng": 0, "pk": ""}}</script>'
        info = parse_post_html(html, "SC018")
        assert info.location.name == "Unknown"
        assert info.location.maps_url == ""
