"""The signal universe — which crypto symbols the signal scan runs on.

Measured on 36 coins over 2019-2026 (backtest --history, 4h, 0.1% round trip, out of
sample from 2024): the strategies' edge is real but thin across the whole roster
(~+0.04R per trade) and strongest on the most-traded coins (~+0.09R on the top six).
Thin coins also cost more to trade than the backtest charges — wider spreads, more
slippage — so their real edge is smaller still. Picking coins one by one from
backtest results would be overfitting (~285 trades per coin is ±0.06R of noise), so
the rule is structural instead: signals run on the N most liquid coins, by
Hyperliquid volume, re-ranked daily.

A coin must also have SIGNAL_UNIVERSE_MIN_HISTORY_DAYS (365) of daily candles on
Hyperliquid to qualify. Hyperliquid volume is dominated at times by brand-new launches
the strategies have never been tested on; they join once they have a track record,
and the next-most-liquid established coins take their slots meanwhile.

Only ``Symbol.signals_enabled`` changes. Every coin stays chartable, searchable and
watchlistable; coins outside the universe simply stop receiving signals. Forex is
untouched (curated separately). SIGNAL_UNIVERSE_TOP_N = 0 switches the whole thing
off and leaves signals_enabled to manual control.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Avg
from django.utils import timezone

from .models import MarketContext, Symbol

logger = logging.getLogger("market_data.universe")

# Rank on the average of the hourly 24h-volume snapshots over this window, so a
# one-day pump can't buy a coin a slot and a quiet day can't evict one.
VOLUME_WINDOW = timedelta(days=7)
# A coin already in the universe keeps its slot while it ranks within
# TOP_N * HYSTERESIS. Without it, coins hovering around #20 would flip in and out
# daily — signals starting and stopping on them for no reason a user could see.
HYSTERESIS = 1.5
# Below this many ranked coins the volume data is presumed broken (failed recorder,
# API change). Applying a ranking that thin could switch off nearly every coin, so
# the refresh refuses and leaves the universe as it was.
MIN_RANKED = 10
# Listing age only ever grows, so a coin that has qualified is re-checked rarely;
# one that hasn't is re-checked daily so it joins soon after its first year.
_AGE_KEY = "universe:old_enough:%s:%s"
_AGE_TTL_OLD = 30 * 86400
_AGE_TTL_YOUNG = 86400
# The age check shares Hyperliquid's per-IP request budget with the live relay and
# the signal scan; firing ~25 lookups back to back got the refresh 429'd in
# production. Each uncached lookup waits this long first.
AGE_LOOKUP_PAUSE = 1.0
# Width of the window probed around "min_days ago". Wide enough to step over a
# day or two of exchange downtime, narrow enough to be the cheapest request.
_AGE_PROBE_DAYS = 7


def old_enough(coin: str, min_days: int, *, sleep=time.sleep) -> bool:
    """Did ``coin`` already trade on Hyperliquid ``min_days`` ago?

    One small question rather than a year of candles: fetch the daily candles in a
    short window ending ``min_days`` ago — any candle there means the coin is at
    least that old. That is the lightest request Hyperliquid's limiter weighs.

    Raises on a failed request: the caller treats that as "can't rank today" and
    leaves the universe unchanged, rather than guessing a coin's age either way.
    """
    key = _AGE_KEY % (coin, min_days)
    cached = cache.get(key)
    if cached is not None:
        return cached
    from .client import fetch_candle_window

    sleep(AGE_LOOKUP_PAUSE)
    end_ms = int((time.time() - min_days * 86400) * 1000)
    start_ms = end_ms - _AGE_PROBE_DAYS * 86400 * 1000
    result = bool(fetch_candle_window(coin, "1d", start_ms, end_ms))
    cache.set(key, result, _AGE_TTL_OLD if result else _AGE_TTL_YOUNG)
    return result


def _ranked_by_history(now) -> list[str]:
    rows = (
        MarketContext.objects.filter(
            bucket__gte=now - VOLUME_WINDOW,
            day_ntl_vlm__isnull=False,
            symbol__asset_class=Symbol.AssetClass.CRYPTO,
            symbol__is_active=True,
        )
        .values("symbol__hl_coin")
        .annotate(vol=Avg("day_ntl_vlm"))
        .order_by("-vol")
    )
    return [r["symbol__hl_coin"] for r in rows if r["vol"]]


def _ranked_live() -> list[str]:
    """Fallback when there's no recorded history yet: one live API call."""
    from .client import fetch_asset_contexts

    ctxs = fetch_asset_contexts()
    vols = []
    for coin, ctx in ctxs.items():
        try:
            vol = float(ctx.get("dayNtlVlm") or 0)
        except (TypeError, ValueError):
            continue
        if vol > 0:
            vols.append((vol, coin))
    return [coin for _vol, coin in sorted(vols, reverse=True)]


def ranked_coins(now=None) -> tuple[list[str], str]:
    """Hyperliquid coins, most liquid first, and where the ranking came from."""
    now = now or timezone.now()
    ranked = _ranked_by_history(now)
    if len(ranked) >= MIN_RANKED:
        return ranked, "7-day average volume"
    return _ranked_live(), "live 24h volume (no recorded history yet)"


def apply_signal_universe(top_n: int | None = None, *, dry_run: bool = False,
                          ranking: list[str] | None = None,
                          min_history_days: int | None = None, age_fn=None) -> dict:
    """Enable signals on the top-N crypto coins by volume, disable the rest.

    Returns a summary: which coins were switched on / off and the resulting
    universe. Never touches forex, never touches inactive symbols, and refuses to
    act on a ranking too thin to trust.
    """
    top_n = settings.SIGNAL_UNIVERSE_TOP_N if top_n is None else top_n
    if top_n <= 0:
        return {"skipped": "SIGNAL_UNIVERSE_TOP_N is 0 — universe is manual"}

    source = "given"
    if ranking is None:
        ranking, source = ranked_coins()
    if len(ranking) < MIN_RANKED:
        logger.warning("signal universe: only %d coins ranked — not applying", len(ranking))
        return {"skipped": f"only {len(ranking)} coins ranked; refusing to apply"}

    crypto = list(
        Symbol.objects.filter(is_active=True, asset_class=Symbol.AssetClass.CRYPTO)
        .exclude(hl_coin="")
    )
    by_coin = {s.hl_coin: s for s in crypto}
    keep_within = int(top_n * HYSTERESIS)

    # Rank only coins we track AND that have a year of history, walking the volume
    # ranking until enough established coins are found to fill the universe and its
    # hysteresis band. A coin's rank is its position among ESTABLISHED coins, so a
    # new launch in the raw top 20 hands its slot to the next established coin.
    min_days = (settings.SIGNAL_UNIVERSE_MIN_HISTORY_DAYS
                if min_history_days is None else min_history_days)
    age_fn = age_fn or old_enough
    ranked, too_new = [], []
    for coin in ranking:
        if len(ranked) >= keep_within:
            break
        if coin not in by_coin:
            continue  # untracked: can't take a slot
        if min_days > 0 and not age_fn(coin, min_days):
            too_new.append(coin)
            continue
        ranked.append(coin)
    rank = {coin: i for i, coin in enumerate(ranked)}

    # Who counts as an existing member. Hysteresis only means something once a
    # universe has actually been applied; before that every coin is enabled (the
    # pre-universe default), and treating all ~200 as members would keep the top
    # `keep_within` forever instead of cutting to top_n. More enabled coins than a
    # universe can ever hold means "not a universe yet" — so cut strictly this once.
    enabled = {s.hl_coin for s in crypto if s.signals_enabled}
    members = enabled if len(enabled) <= keep_within else set()

    universe = set()
    for sym in crypto:
        r = rank.get(sym.hl_coin)
        if r is None:
            continue  # no volume data: never in the universe
        if r < top_n or (sym.hl_coin in members and r < keep_within):
            universe.add(sym.hl_coin)

    switched_on = sorted(s.hl_coin for s in crypto if s.hl_coin in universe and not s.signals_enabled)
    switched_off = sorted(s.hl_coin for s in crypto if s.hl_coin not in universe and s.signals_enabled)

    if not dry_run:
        Symbol.objects.filter(id__in=[by_coin[c].id for c in switched_on]).update(signals_enabled=True)
        Symbol.objects.filter(id__in=[by_coin[c].id for c in switched_off]).update(signals_enabled=False)

    summary = {
        "top_n": top_n,
        "source": source,
        "universe": [c for c in ranked if c in universe],
        "switched_on": switched_on,
        "switched_off": switched_off,
        "too_new": too_new,
        "min_history_days": min_days,
        "dry_run": dry_run,
    }
    logger.info(
        "signal universe (%s): %d coins, +%d / -%d%s", source, len(universe),
        len(switched_on), len(switched_off), " [dry run]" if dry_run else "",
    )
    return summary
