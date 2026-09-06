"""Loss circuit breaker — stop delivering into a market that is stopping everything out.

Three bad days have cost this product real money (2026-08-19, 2026-09-03), and both
share a shape the existing guards cannot see:

    2026-08-19   92 SELL trades in a day, 65 losses (29.3%). Every other day
                 that fortnight ran SELL at 84-100%.
    2026-09-03   28 trades, 20 losses. BUY 20.0%, SELL 30.4% — BOTH sides losing,
                 i.e. whipsaw rather than a directional regime. Strip that one day
                 and the week ran 72%.

Why nothing caught them:

* Confidence floors, ADX and confluence judge a setup on the bar it fires. On these
  days each individual signal looked normal. What was abnormal was the market.
* ``cap_currency_exposure`` / ``cap_crypto_direction`` cap CONCURRENT exposure. On a
  whipsaw day trades stop out within the hour, which frees slots, which lets more in
  — 23 SELLs went out on 2026-09-03 without ever breaching a cap of 12. Those caps
  limit how much is held at once; they say nothing about how fast it is being lost.
* The leader gate reads BTC's trend. It is blind to a chop day where BTC has no
  trend and everything is being wicked out.

So this guard reads OUTCOMES rather than setups: when the last `window` hours have
produced `losses` or more stop-outs, delivery pauses for `cooldown` hours. It is the
only mechanism here that can notice "today is not working" and stop.

Deliberately global per asset class rather than per user: the failure is market-wide,
every user is holding the same book, and a per-user count would let a quiet user keep
receiving the same doomed signals. Reads only stored outcomes — nothing is written,
nothing about generation changes, and clearing the setting restores the old behaviour
exactly.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache

from .models import Signal

logger = logging.getLogger("signals.breaker")

# One cache slot per asset class. Short TTL: the feed is polled often and this must
# not add a COUNT query per request, but it also has to react inside one scan cycle.
_CACHE_TTL = 60
_CACHE_KEY = "signals:breaker:%s"


def _config() -> tuple[int, float, float] | None:
    """(losses, window_hours, cooldown_hours), or None when disabled.

    Format "losses/window_h/cooldown_h", e.g. "6/4/6" = six stop-outs inside four
    hours pauses delivery for six. Empty/malformed disables the breaker and logs —
    a guard that silently fails OPEN is the right default here (never blocking the
    product on a config typo), but it must say so.
    """
    raw = (getattr(settings, "SIGNAL_LOSS_BREAKER", "") or "").strip()
    if not raw:
        return None
    parts = raw.split("/")
    if len(parts) != 3:
        logger.warning("SIGNAL_LOSS_BREAKER malformed (%r) — breaker disabled", raw)
        return None
    try:
        losses, window_h, cooldown_h = int(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        logger.warning("SIGNAL_LOSS_BREAKER malformed (%r) — breaker disabled", raw)
        return None
    if losses < 1 or window_h <= 0 or cooldown_h <= 0:
        logger.warning("SIGNAL_LOSS_BREAKER out of range (%r) — breaker disabled", raw)
        return None
    return losses, window_h, cooldown_h


def breaker_state(asset_class: str, now) -> dict:
    """Whether delivery for `asset_class` is currently paused, and why.

    Counts SL outcomes only. An INVALIDATED trend-flip closes flat at 0R and a
    scratch is not evidence the market is hostile, so counting it would trip the
    breaker on quiet days.
    """
    cfg = _config()
    if cfg is None:
        return {"halted": False, "losses": 0, "threshold": 0, "resumes_at": None}
    losses_needed, window_h, cooldown_h = cfg

    key = _CACHE_KEY % asset_class
    cached = cache.get(key)
    if cached is not None:
        return cached

    # The cooldown is measured from the most recent qualifying loss, so a market that
    # keeps stopping trades out keeps the pause alive rather than reopening on a timer
    # while the same conditions persist.
    since = now - timedelta(hours=window_h + cooldown_h)
    recent = list(
        Signal.objects.filter(
            outcome=Signal.Outcome.SL,
            resolved_at__gte=since,
            symbol__asset_class=asset_class,
        )
        .order_by("-resolved_at")
        .values_list("resolved_at", flat=True)[:200]
    )

    halted, count, last_loss = False, 0, None
    if recent:
        window_start = now - timedelta(hours=window_h)
        in_window = [t for t in recent if t >= window_start]
        count = len(in_window)
        if count >= losses_needed:
            halted, last_loss = True, in_window[0]
        else:
            # Still inside the cooldown from an earlier burst? Look for any window of
            # `window_h` ending within the last `cooldown_h` that met the threshold.
            cooldown_start = now - timedelta(hours=cooldown_h)
            for i, anchor in enumerate(recent):
                if anchor < cooldown_start:
                    break
                burst = [t for t in recent[i:] if t >= anchor - timedelta(hours=window_h)]
                if len(burst) >= losses_needed:
                    halted, count, last_loss = True, len(burst), anchor
                    break

    # When it lifts, so the UI can say "resumes at ..." rather than leaving a user
    # staring at an empty feed with no idea whether it is broken or deliberate.
    resumes_at = (last_loss + timedelta(hours=cooldown_h)) if (halted and last_loss) else None
    state = {"halted": halted, "losses": count, "threshold": losses_needed,
             "resumes_at": resumes_at.isoformat() if resumes_at else None}
    cache.set(key, state, _CACHE_TTL)
    if halted:
        logger.warning(
            "loss breaker ACTIVE for %s: %d stop-outs (threshold %d) — delivery paused",
            asset_class, count, losses_needed,
        )
    return state


def filter_halted(reps: list, now) -> list:
    """Drop signals whose asset class is currently halted. Order preserved."""
    if _config() is None:
        return reps
    checked: dict = {}
    kept = []
    for sig in reps:
        ac = getattr(sig.symbol, "asset_class", "crypto")
        if ac not in checked:
            checked[ac] = breaker_state(ac, now)["halted"]
        if not checked[ac]:
            kept.append(sig)
    return kept


def feed_state(now) -> dict:
    """Breaker summary for the signals feed payload.

    Reports every halted asset class, not just the user's, because a user watching
    both markets needs to know which half of their feed is paused. Cheap: each class
    is one cached lookup, and the whole function short-circuits when the breaker is
    off.
    """
    if _config() is None:
        return {"active": False, "classes": [], "resumes_at": None}
    halted = {}
    for asset_class in ("crypto", "forex"):
        st = breaker_state(asset_class, now)
        if st["halted"]:
            halted[asset_class] = st
    resumes = [st["resumes_at"] for st in halted.values() if st.get("resumes_at")]
    return {
        "active": bool(halted),
        "classes": sorted(halted),
        # The LAST to lift, so the banner never promises an early resume while
        # another market is still paused.
        "resumes_at": max(resumes) if resumes else None,
    }
