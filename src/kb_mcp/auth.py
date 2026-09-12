"""Authentication: the caller's Knowledge Base API key *is* the identity.

There is no separate user store here. A client sends its key as a bearer token,
this server checks it against the Knowledge Base, and every subsequent call is
made with that same key. So a caller sees exactly the spaces their key can see,
and revoking the key in the app revokes access here too - no second place to
remember.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

from fastmcp.server.auth import AccessToken, TokenVerifier

from kb_mcp.client import KnowledgeBase, KnowledgeBaseError
from kb_mcp.config import settings

logger = logging.getLogger(__name__)

# Every verified key costs an entry until it expires, so the cache needs a
# ceiling: without one a deployment with many keys grows this dictionary for as
# long as the process lives, and nothing ever leaves it.
MAX_CACHED_KEYS = 1024


@dataclass(slots=True)
class _CachedUser:
    token: AccessToken
    checked_at: float


def _fingerprint(token: str) -> str:
    """What the cache is keyed by.

    The cache holds live credentials for every recent caller, so it keys on a
    digest rather than the key itself: a heap dump, a debugger or a stray repr
    then yields nothing anyone can present to the API.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class KnowledgeBaseKeyVerifier(TokenVerifier):
    """Accepts a Knowledge Base API key and resolves it to its owner."""

    def __init__(self, api_url: str | None = None) -> None:
        super().__init__()
        self.api_url = api_url or settings.api_url
        self._cache: OrderedDict[str, _CachedUser] = OrderedDict()

    async def verify_token(self, token: str) -> AccessToken | None:
        key = _fingerprint(token)
        cached = self._cache.get(key)
        now = time.monotonic()
        if cached and now - cached.checked_at < settings.AUTH_CACHE_SECONDS:
            return cached.token

        try:
            user = await KnowledgeBase(token, self.api_url).whoami()
        except KnowledgeBaseError as exc:
            # 401/403 is an ordinary rejection; anything else is worth logging,
            # because it usually means the Knowledge Base itself is unreachable.
            # Only the status is logged: the API's error body is echoed text and
            # may quote the key that was presented, which must not reach a log.
            if exc.status not in (401, 403, 404):
                logger.warning(
                    "Could not verify an API key: the Knowledge Base answered %s",
                    exc.status,
                )
            self._cache.pop(key, None)
            return None
        except Exception as exc:  # noqa: BLE001 - network trouble
            logger.warning(
                "Could not reach the Knowledge Base to verify a key: %s",
                type(exc).__name__,
            )
            return None

        access = AccessToken(
            token=token,
            client_id=str(user.get("id", "")),
            subject=str(user.get("id", "")),
            # An API key is read-only or read/write; the API enforces that on
            # every call, so nothing here needs to re-derive it.
            scopes=[],
            claims={
                "email": user.get("email", ""),
                "full_name": user.get("full_name") or "",
                "is_superuser": bool(user.get("is_superuser")),
            },
        )
        self._remember(key, _CachedUser(token=access, checked_at=now))
        return access

    def _remember(self, key: str, entry: _CachedUser) -> None:
        cutoff = entry.checked_at - settings.AUTH_CACHE_SECONDS
        for stale in [k for k, v in self._cache.items() if v.checked_at <= cutoff]:
            del self._cache[stale]
        # Insertion order is verification order, so the front of the dictionary
        # is the least recently verified key.
        while len(self._cache) >= MAX_CACHED_KEYS:
            self._cache.popitem(last=False)
        self._cache[key] = entry

    def forget(self, token: str) -> None:
        """Drop a cached key, so the next call re-checks it."""
        self._cache.pop(_fingerprint(token), None)
