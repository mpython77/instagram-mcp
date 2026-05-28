import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from instagram_mcp.formatter import analyze_comments_sentiment, format_comment_analysis_markdown
from instagram_mcp.models import AnalyzeCommentsInput
from instagram_mcp.tools import register_tools
from mcp.server.fastmcp import FastMCP, Context

def test_analyze_comments_sentiment_lexicon():
    comments = [
        {"text": "This is amazing! Love it! 😍", "like_count": 5, "child_comment_count": 1, "user": {"username": "user1"}},
        {"text": "Absolute garbage, horrible service and hate this.", "like_count": 10, "child_comment_count": 0, "user": {"username": "user2"}},
        {"text": "Okay, just average.", "like_count": 1, "child_comment_count": 2, "user": {"username": "user3"}},
    ]
    
    result = analyze_comments_sentiment(comments)
    
    assert result["total"] == 3
    assert result["pos_count"] == 1
    assert result["neg_count"] == 1
    assert result["neu_count"] == 1
    assert isinstance(result["score"], float)
    
    # Check highlight comments (processed_comments)
    assert len(result["processed_comments"]) == 3
    
    # Check top emojis (list of tuples)
    assert any(emoji == "😍" for emoji, _ in result["top_emojis"])
    
    # Check keywords
    kws = [kw for kw, _ in result["top_keywords"]]
    assert len(kws) > 0

def test_format_comment_analysis_markdown():
    comments = [
        {"text": "Amazing!", "like_count": 5, "child_comment_count": 1, "user": {"username": "user1"}},
        {"text": "Bad!", "like_count": 10, "child_comment_count": 0, "user": {"username": "user2"}},
    ]
    
    markdown = format_comment_analysis_markdown("shortcode123", comments)
    
    assert "shortcode123" in markdown
    assert "Sentiment Score" in markdown
    assert "Positive" in markdown
    assert "Negative" in markdown
    assert "Highlight Comments" in markdown

@pytest.mark.asyncio
async def test_mcp_tool_instagram_analyze_comments():
    mcp_tools = {}
    mcp = MagicMock(spec=FastMCP)

    def tool_decorator(*args, **kwargs):
        def decorator(f):
            name = kwargs.get("name") or f.__name__
            mcp_tools[name] = f
            return f
        return decorator

    mcp.tool = tool_decorator

    client = MagicMock()
    config = MagicMock()
    config.cache_comments_ttl = 600
    config.enabled_toolsets = {"all"}
    
    exporter = MagicMock()
    exporter.save = AsyncMock()
    
    # Mock client.fetch_comments_paginated and parse_comments
    mock_comment = MagicMock()
    mock_comment.text = "great post!"
    mock_comment.comment_like_count = 2
    mock_comment.child_comment_count = 0
    mock_comment.username = "fan1"
    mock_comment.is_caption = False
    
    client.fetch_comments_paginated = AsyncMock(return_value={
        "comments": [mock_comment],
        "caption": None,
        "comment_count": 1,
        "pages_fetched": 1,
        "has_more": False
    })
    
    with patch("instagram_mcp.tools.analysis.parse_comments", return_value=[mock_comment]):
        # Call register_tools
        register_tools(mcp, client, config, exporter)
        
        # Find our tool
        assert "instagram_analyze_comments" in mcp_tools
        tool = mcp_tools["instagram_analyze_comments"]
        
        # Run the tool
        params = AnalyzeCommentsInput(post="https://www.instagram.com/p/C12345/", max_comments=10, sort_order="popular")
        ctx = MagicMock(spec=Context)
        ctx.info = AsyncMock()
        ctx.report_progress = AsyncMock()
        
        result_md = await tool(params, ctx)
        
        assert "Sentiment" in result_md
        assert "C12345" in result_md or "c12345" in result_md
        
        # Verify client and exporter calls
        client.fetch_comments_paginated.assert_called_once()
        exporter.save.assert_called_once()


def test_analyze_comments_sentiment_multilingual_negation():
    comments = [
        # Negation handling
        {"text": "not bad at all", "like_count": 0, "child_comment_count": 0, "user": {"username": "user1"}}, # Positive (since "bad" is negated by "not")
        {"text": "yomon emas", "like_count": 0, "child_comment_count": 0, "user": {"username": "user2"}}, # Positive (since Uzbek "yomon" is negated by "emas")
        {"text": "не плохо", "like_count": 0, "child_comment_count": 0, "user": {"username": "user3"}}, # Positive (since Russian "плохо" is negated by "не")
        # Multilingual matching
        {"text": "bu juda ajoyib post", "like_count": 0, "child_comment_count": 0, "user": {"username": "user4"}}, # Positive (Uzbek "ajoyib")
        {"text": "это ужасно", "like_count": 0, "child_comment_count": 0, "user": {"username": "user5"}}, # Negative (Russian "ужасно")
    ]
    result = analyze_comments_sentiment(comments)
    assert result["total"] == 5
    assert result["pos_count"] == 4
    assert result["neg_count"] == 1
    assert result["neu_count"] == 0
