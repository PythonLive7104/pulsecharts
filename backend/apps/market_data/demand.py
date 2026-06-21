"""Symbol demand registry (Section 7 — demand-driven upstream subscriptions).

A tiny Redis-backed reference count of how many browser clients are currently
watching each symbol. The consumer maintains the counts (subscribe/unsubscribe/
disconnect); the relay reads the set of demanded symbols and subscribes upstream
to Hyperliquid only for those — so the upstream connection only carries symbols
someone is actually looking at, regardless of how many coins exist.

Stored as a Redis hash:  market:demand  { "BTC-USD": 3, "ETH-USD": 1, ... }

Implementation note: we use the *synchronous* redis client wrapped in
sync_to_async rather than redis.asyncio. An async redis connection binds to the
event loop it's created on, and sharing one inside the Channels consumer led to
read timeouts (a 1011 on the socket). Sync redis-py is thread-safe and runs fine
in asgiref's thread pool for these low-frequency calls.

Caveat: counts are reference-counted, so a client process killed without a clean
disconnect can leave a stale count, keeping a symbol subscribed upstream longer
than needed. Harmless (it self-corrects on relay restart); a per-connection TTL
heartbeat would harden this further if it ever matters at scale.
"""

import redis
from asgiref.sync import sync_to_async
from django.conf import settings

DEMAND_KEY = "market:demand"

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def _increment(ticker: str) -> None:
    _redis().hincrby(DEMAND_KEY, ticker, 1)


def _decrement(ticker: str) -> None:
    """Drop one watcher; remove the field entirely once it hits zero."""
    remaining = _redis().hincrby(DEMAND_KEY, ticker, -1)
    if remaining <= 0:
        _redis().hdel(DEMAND_KEY, ticker)


def _demanded() -> set[str]:
    return set(_redis().hkeys(DEMAND_KEY))


# Async wrappers used by the consumer (async) and relay (async).
increment = sync_to_async(_increment)
decrement = sync_to_async(_decrement)
demanded = sync_to_async(_demanded)
