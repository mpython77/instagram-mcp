import re
import logging
from typing import Dict, Any
from curl_cffi.requests import AsyncSession

logger = logging.getLogger("instagram_mcp.challenge")

class ChallengeResolver:
    """Handles Instagram challenge verification (SMS/Email/2FA) dynamically."""
    _pending_challenges: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def register_challenge(
        cls,
        alias: str,
        challenge_url: str,
        session: AsyncSession,
        cookies_path: str
    ) -> str:
        """Register a pending challenge for an account, telling the user how to solve it."""
        # Extract challenge ID/path from URL if possible
        match = re.search(r"/challenge/([^/]+)/([^/]+)/", challenge_url)
        path_info = f"{match.group(1)}/{match.group(2)}" if match else "generic"

        cls._pending_challenges[alias] = {
            "alias": alias,
            "challenge_url": challenge_url,
            "session": session,
            "cookies_path": cookies_path,
            "path_info": path_info,
            "status": "pending_code"
        }

        instructions = (
            f"⚠️ Verification Required (Checkpoint) for account '{alias}'!\n"
            f"Please get the 6-digit verification code from your Email/SMS/2FA and run the tool:\n"
            f"👉 `instagram_submit_verification_code(code='YOUR_CODE', alias='{alias}')`"
        )
        logger.warning("Checkpoint challenge registered for account '%s': %s", alias, challenge_url)
        return instructions

    @classmethod
    async def submit_code(cls, code: str, alias: str = "default") -> Dict[str, Any]:
        """Submit the verification code to Instagram to solve the pending challenge."""
        challenge = cls._pending_challenges.get(alias)
        if not challenge:
            return {
                "success": False,
                "message": f"No pending challenge found for account '{alias}'."
            }

        session: AsyncSession = challenge["session"]
        cookies_path: str = challenge["cookies_path"]
        path_info: str = challenge["path_info"]

        # Hitting the Instagram challenge submit endpoint
        # For security_code submission, the URL is typically:
        # https://www.instagram.com/api/v1/challenge/{path_info}/
        # with POST payload: security_code=<code_entered>
        url = f"https://www.instagram.com/api/v1/challenge/{path_info}/"

        csrf = session.cookies.get("csrftoken") or ""
        headers = {
            "x-csrftoken": csrf,
            "x-requested-with": "XMLHttpRequest",
            "Referer": challenge["challenge_url"],
        }

        payload = {
            "security_code": code
        }

        try:
            # Verification codes are short-lived single-use credentials; log only
            # a length hint and the alias/url, never the code itself — Requirement 23.1.
            logger.info(
                "Submitting verification code (len=%d) for '%s' to %s",
                len(code), alias, url,
            )
            resp = await session.post(url, data=payload, headers=headers, timeout=20, allow_redirects=False)

            if resp.status_code == 200:
                body = resp.json()
                if body.get("status") == "ok":
                    # Update cookies in cookies_path!
                    # Save cookies back to file
                    import json
                    from pathlib import Path
                    p = Path(cookies_path)

                    # Identify if JSON or Netscape format
                    if p.suffix.lower() == ".json":
                        # Format as Cookie-Editor JSON array
                        cookie_list = []
                        for c in session.cookies:
                            cookie_list.append({
                                "name": c.name,
                                "value": c.value,
                                "domain": c.domain or ".instagram.com",
                                "path": c.path or "/",
                                "secure": True,
                                "httpOnly": True
                            })
                        p.write_text(json.dumps(cookie_list, indent=2))
                    else:
                        # Simple cookies.txt format
                        lines = ["# Netscape HTTP Cookie File\n"]
                        for c in session.cookies:
                            domain = c.domain or ".instagram.com"
                            include_sub = "TRUE" if domain.startswith(".") else "FALSE"
                            path = c.path or "/"
                            secure = "TRUE"
                            expires = "0"
                            lines.append(f"{domain}\t{include_sub}\t{path}\t{secure}\t{expires}\t{c.name}\t{c.value}\n")
                        p.write_text("".join(lines))

                    # Remove from pending list
                    cls._pending_challenges.pop(alias, None)
                    logger.info("Challenge solved successfully for '%s'! Cookies updated.", alias)

                    return {
                        "success": True,
                        "message": f"Challenge solved successfully for account '{alias}'! Session restored."
                    }
                else:
                    msg = body.get("message") or "Unknown API error"
                    logger.warning("Code submission failed: %s", msg)
                    return {
                        "success": False,
                        "message": f"Instagram API error: {msg}"
                    }
            else:
                logger.warning("HTTP error during code submission: HTTP %d", resp.status_code)
                return {
                    "success": False,
                    "message": f"HTTP error during code submission: HTTP {resp.status_code}"
                }
        except Exception as e:
            logger.error("Exception during challenge code submission: %s", e)
            return {
                "success": False,
                "message": f"Failed to submit code due to exception: {e}"
            }
