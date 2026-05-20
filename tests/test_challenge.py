import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
from instagram_mcp.challenge import ChallengeResolver
from instagram_mcp.exceptions import FetchError

@pytest.mark.asyncio
async def test_challenge_registration_and_solution(tmp_path):
    cookies_file = tmp_path / "user_one.json"
    cookies_file.write_text(json.dumps([{"name": "sessionid", "value": "old_session"}]))

    # Mock session
    session = AsyncMock()
    # Mock response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok"}
    session.post.return_value = mock_resp
    
    # Mock session cookies
    mock_cookies_jar = MagicMock()
    mock_cookies_jar.get.return_value = "mock_csrf"
    
    mock_cookie = MagicMock()
    mock_cookie.name = "sessionid"
    mock_cookie.value = "new_session"
    mock_cookie.domain = ".instagram.com"
    mock_cookie.path = "/"
    
    mock_cookies_jar.__iter__.return_value = [mock_cookie]
    session.cookies = mock_cookies_jar

    challenge_url = "https://www.instagram.com/challenge/12345/abcde/"
    
    # 1. Register challenge
    instructions = ChallengeResolver.register_challenge(
        alias="user_one",
        challenge_url=challenge_url,
        session=session,
        cookies_path=str(cookies_file)
    )
    
    assert "user_one" in ChallengeResolver._pending_challenges
    assert "instagram_submit_verification_code" in instructions
    assert ChallengeResolver._pending_challenges["user_one"]["path_info"] == "12345/abcde"

    # 2. Submit incorrect code or API fail
    mock_resp.json.return_value = {"status": "fail", "message": "Invalid code"}
    res_fail = await ChallengeResolver.submit_code("111111", "user_one")
    assert not res_fail["success"]
    assert "Invalid code" in res_fail["message"]

    # 3. Submit correct code
    mock_resp.json.return_value = {"status": "ok"}
    res_success = await ChallengeResolver.submit_code("654321", "user_one")
    assert res_success["success"]
    assert "user_one" not in ChallengeResolver._pending_challenges

    # 4. Check cookies updated in file
    content = json.loads(cookies_file.read_text())
    assert len(content) == 1
    assert content[0]["value"] == "new_session"
