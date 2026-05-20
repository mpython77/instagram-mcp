import asyncio
import random
import logging
from curl_cffi.requests import AsyncSession as CurlAsyncSession

logger = logging.getLogger("instagram_mcp.delay")

class DelaySimulator:
    """Simulate human-like delays (jitter) between HTTP requests to prevent detection."""
    def __init__(self, min_delay_ms: int = 500, max_delay_ms: int = 2000, enabled: bool = True):
        self.min_delay_ms = min_delay_ms
        self.max_delay_ms = max_delay_ms
        self.enabled = enabled

    async def sleep_jitter(self) -> None:
        if not self.enabled:
            return
        if self.max_delay_ms <= self.min_delay_ms:
            delay_sec = self.min_delay_ms / 1000.0
        else:
            delay_sec = random.randint(self.min_delay_ms, self.max_delay_ms) / 1000.0
        logger.debug("Applying jitter delay: %.3f seconds", delay_sec)
        await asyncio.sleep(delay_sec)


class JitterAsyncSession(CurlAsyncSession):
    """Subclass of curl_cffi's AsyncSession that automatically applies jitter delays before requests and monitors account health."""
    def __init__(self, *args, delay_simulator: DelaySimulator = None, account_pool = None, cookies_path: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.delay_simulator = delay_simulator
        self.account_pool = account_pool
        self.cookies_path = cookies_path
        self.account_alias = "default"

    async def get(self, *args, **kwargs):
        if self.delay_simulator:
            await self.delay_simulator.sleep_jitter()
        resp = await super().get(*args, **kwargs)
        self._check_health(resp)
        return resp

    async def post(self, *args, **kwargs):
        if self.delay_simulator:
            await self.delay_simulator.sleep_jitter()
        resp = await super().post(*args, **kwargs)
        self._check_health(resp)
        return resp

    def _check_health(self, resp):
        # 1. Rate limited
        if resp.status_code == 429:
            if self.account_pool and self.account_alias != "default":
                self.account_pool.mark_rate_limited(self.account_alias)
            return

        # 2. Redirects (Checkpoints / Login page)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location", "").lower()
            if "challenge" in loc or "login" in loc:
                if self.account_pool and self.account_alias != "default":
                    self.account_pool.mark_checkpoint(self.account_alias)
                
                from .challenge import ChallengeResolver
                from .exceptions import FetchError
                inst = ChallengeResolver.register_challenge(
                    alias=self.account_alias,
                    challenge_url=resp.headers.get("location", ""),
                    session=self,
                    cookies_path=self.cookies_path
                )
                raise FetchError(inst)

        # 3. JSON body checks
        try:
            content_type = resp.headers.get("content-type", "").lower()
            if "application/json" in content_type:
                data = resp.json()
                if isinstance(data, dict):
                    message = str(data.get("message", "")).lower()
                    if "checkpoint_required" in message or "checkpoint" in message or "challenge" in message:
                        if self.account_pool and self.account_alias != "default":
                            self.account_pool.mark_checkpoint(self.account_alias)
                        
                        from .challenge import ChallengeResolver
                        from .exceptions import FetchError
                        inst = ChallengeResolver.register_challenge(
                            alias=self.account_alias,
                            challenge_url="https://www.instagram.com/challenge/",
                            session=self,
                            cookies_path=self.cookies_path
                        )
                        raise FetchError(inst)
                    elif "login_required" in message or "login" in message:
                        if self.account_pool and self.account_alias != "default":
                            self.account_pool.mark_expired(self.account_alias)
        except FetchError:
            raise
        except Exception:
            pass
