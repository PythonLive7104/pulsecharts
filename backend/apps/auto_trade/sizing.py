"""Position sizing — pure functions, no broker/network (so they're unit-testable).

Default model is risk-% of balance: the position is sized so that if the stop is
hit, the loss equals `risk_pct`% of account equity — independent of how wide the
stop is. This is why risk-% beats fixed notional: a wide-stop trade automatically
takes a smaller position.

  risk_amount  = equity × risk_pct/100
  qty (base)   = risk_amount / |entry − stop_loss|

`fixed_usd` and `pct_balance` size by notional instead and ignore stop distance.
"""

from __future__ import annotations


class SizingError(Exception):
    pass


def compute_qty(equity: float, entry: float, stop_loss: float, config) -> float:
    """Total base-asset quantity for the position (unrounded — the broker rounds
    to the instrument lot step)."""
    if equity <= 0:
        raise SizingError("account equity is zero")
    if entry <= 0:
        raise SizingError("invalid entry price")

    mode = config.sizing_mode
    if mode == config.Sizing.RISK_PCT:
        risk_distance = abs(entry - stop_loss)
        if risk_distance <= 0:
            raise SizingError("entry equals stop-loss — no risk distance")
        risk_amount = equity * (config.risk_pct / 100.0)
        return risk_amount / risk_distance
    if mode == config.Sizing.FIXED_USD:
        return config.fixed_usd / entry
    if mode == config.Sizing.PCT_BALANCE:
        return (equity * config.pct_balance / 100.0) / entry
    raise SizingError(f"unknown sizing mode: {mode}")


def split_take_profits(total_qty: float, tp_prices: list[float], distribution: list[float]) -> list[tuple[float, float]]:
    """Map the four TP prices to (price, qty) legs using the % distribution.

    Falls back to an even split if no/invalid distribution is configured. Skips
    legs whose share is zero.
    """
    if not distribution or len(distribution) != len(tp_prices):
        share = 100.0 / len(tp_prices)
        distribution = [share] * len(tp_prices)
    legs = []
    for price, pct in zip(tp_prices, distribution):
        if pct <= 0:
            continue
        legs.append((price, total_qty * pct / 100.0))
    return legs


def filled_tp_legs(filled_fraction: float, distribution: list[float]) -> int:
    """How many take-profit legs have filled, given the fraction of the original
    position that has closed so far.

    The reconciler infers TP progress from the shrinking position size rather than
    polling each TP order. With distribution [25, 25, 25, 25], a position that's
    50% closed has had TP1 and TP2 fill → returns 2. A small epsilon absorbs lot
    rounding so an exact TP fill isn't missed.
    """
    if not distribution:
        return 0
    eps = 1e-6
    cumulative = 0.0
    legs = 0
    for pct in distribution:
        if pct <= 0:
            continue
        cumulative += pct / 100.0
        if filled_fraction + eps >= cumulative:
            legs += 1
        else:
            break
    return legs
