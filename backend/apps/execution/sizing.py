"""Auto-fit leverage + position sizing for the Bybit executor (place-only).

The user commits a small FIXED margin per trade (default $1) so a tiny account
(e.g. $5) can hold several positions. On USDT perps that margin only clears Bybit's
per-symbol minimum order size once multiplied by leverage — but leverage also pulls
the liquidation price closer, and this project's stops are wide (3–4.5×ATR). If
liquidation sits INSIDE the stop, the position is liquidated (full margin lost)
before the stop is ever hit, silently wrecking the strategy's real R.

`plan_order` resolves that tension: it picks the LOWEST leverage that still clears
the exchange minimum (lowest leverage = liquidation as far away as possible), and
only accepts it if the resulting liquidation price stays BEYOND the signal's stop.
If no leverage satisfies both, it returns a skip — the caller then notifies the user
rather than placing an unsafe or sub-minimum order.

Pure / dependency-free so it is fully unit-testable without Bybit or Django.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class OrderPlan:
    ok: bool
    reason: str = ""           # populated when ok is False (Telegram-friendly)
    leverage: int = 0
    qty: float = 0.0
    notional: float = 0.0      # qty × entry, in USDT
    margin: float = 0.0        # notional / leverage — what the trade actually ties up
    liq_price: float = 0.0     # approximate isolated-margin liquidation price


def _floor_step(value: float, step: float) -> float:
    """Largest multiple of `step` not exceeding `value` (round DOWN, so we never
    exceed the margin budget). Guards against binary-float drift near a step edge."""
    if step <= 0:
        return value
    n = math.floor(value / step + 1e-9)
    return round(n * step, 12)


def split_scaleout(qty: float, fractions, *, qty_step: float, min_order_qty: float) -> list[float]:
    """Split ``qty`` into reduce-only take-profit tranches by ``fractions`` (the
    50/25/25 ladder of §19.2 is ``(0.5, 0.25, 0.25)``), each floored to ``qty_step``.

    The last non-zero tranche absorbs the step-rounding remainder so the tranches sum
    to exactly ``qty`` (the whole position is laddered out, none stranded by rounding).
    A tranche that comes out below ``min_order_qty`` — common on the fixed-$1-margin
    tiny positions this app sizes — is returned as 0.0: it can't be placed, and the
    caller simply leaves that quantity on the position under the stop. Returns a list
    the same length as ``fractions``; all-zero means "too small to ladder, fall back
    to a single-TP bracket".
    """
    n = len(fractions)
    if qty <= 0 or n == 0:
        return [0.0] * n
    tranches = [_floor_step(qty * f, qty_step) for f in fractions]
    remainder = _floor_step(qty - sum(tranches), qty_step)
    for i in range(n - 1, -1, -1):  # give the remainder to the last non-zero tranche
        if tranches[i] > 0:
            tranches[i] = round(tranches[i] + remainder, 12)
            break
    return [t if t >= min_order_qty else 0.0 for t in tranches]


def _liq_price(entry: float, leverage: int, mmr: float, is_long: bool) -> float:
    """Approximate isolated-margin liquidation price for a linear USDT perp. The exact
    figure depends on fees/funding and Bybit's tiered maintenance margin; this is the
    standard first-order estimate — the caller should still read the real liq price
    back from Bybit after placing as a backstop."""
    move = (1.0 / leverage) - mmr            # fractional adverse move to liquidation
    return entry * (1 - move) if is_long else entry * (1 + move)


def plan_order(
    allocation_usd: float,
    entry_price: float,
    stop_loss: float,
    *,
    min_order_qty: float,
    qty_step: float,
    min_notional: float,
    max_leverage: int,
    mmr: float = 0.005,
    safety_buffer: float = 0.005,
) -> OrderPlan:
    """Decide leverage + quantity for a fixed-margin perp trade, or skip.

    allocation_usd : margin to commit (e.g. 1.0).
    entry_price    : current mark/entry price.
    stop_loss      : the signal's stop price (defines the loss the trade may take).
    min_order_qty / qty_step / min_notional / max_leverage / mmr : from Bybit
        instruments-info for the symbol.
    safety_buffer  : extra fractional gap required between stop and liquidation.
    """
    if entry_price <= 0 or allocation_usd <= 0:
        return OrderPlan(ok=False, reason="invalid entry/allocation")
    is_long = stop_loss < entry_price
    stop_dist = abs(entry_price - stop_loss) / entry_price
    if stop_dist <= 0:
        return OrderPlan(ok=False, reason="stop equals entry")

    # Lowest leverage that clears BOTH exchange minimums at this margin:
    #   notional = leverage × margin ≥ min_notional
    #   qty = notional / entry ≥ min_order_qty
    need_notional = max(min_notional, min_order_qty * entry_price)
    lev_lo = max(1, math.ceil(need_notional / allocation_usd - 1e-9))

    # Highest leverage that keeps liquidation BEYOND the stop (+ buffer):
    #   (1/L) - mmr > stop_dist + safety_buffer  →  L < 1 / (stop_dist + mmr + buffer)
    denom = stop_dist + mmr + safety_buffer
    lev_safe = math.floor(1.0 / denom) if denom > 0 else max_leverage
    lev_hi = min(max_leverage, lev_safe)

    if lev_lo > lev_hi:
        if lev_lo > max_leverage:
            return OrderPlan(ok=False, reason="balance too small for this symbol's minimum")
        return OrderPlan(ok=False, reason="stop too wide to place safely at this size")

    # Walk from the safest (lowest) leverage up; take the first that survives qty
    # rounding without dropping back under a minimum.
    for lev in range(lev_lo, lev_hi + 1):
        qty = _floor_step(lev * allocation_usd / entry_price, qty_step)
        notional = qty * entry_price
        if qty >= min_order_qty and notional >= min_notional:
            return OrderPlan(
                ok=True, leverage=lev, qty=qty, notional=round(notional, 8),
                margin=round(notional / lev, 8),
                liq_price=round(_liq_price(entry_price, lev, mmr, is_long), 8),
            )
    return OrderPlan(ok=False, reason="cannot meet minimum order size at safe leverage")
