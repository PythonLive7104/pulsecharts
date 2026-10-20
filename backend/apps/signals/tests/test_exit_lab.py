"""`backtest --exit-lab` — the head-to-head that decides how a trade is managed.

This comparison is the only evidence behind SIGNAL_EXIT_MODEL, i.e. behind what a
paying user is told to do with a signal, so the thing being compared has to be the
thing they can actually trade. Two assumptions decide that, and both were wrong:

  * the HORIZON. The lab replayed a fixed 300 bars while the live evaluator closes
    a call at SIGNAL_EVAL_BARS (48). On 4h that is 50 days of road against live's 8,
    and a runner given 6x the road beats a full exit at TP1 on the very same trades
    where the clock-aware aggregate says the opposite. The ranking inverted.
  * the EXPIRY P&L. A position still open at the horizon was closed at its scheme's
    own stop, charging a full -1R to trades that expired without ever reaching TP1.
    Live books those flat, which is also how `_record` scores them. The remainder is
    now credited with what is locked in — the stop if it has been trailed into
    profit, flat otherwise — so the correction does not swing the other way and rob
    a trailing scheme of gains its stop had already secured.

Exact values are pinned rather than orderings: a scheme comparison that is merely
"in the right order" can still be off by the amount that decides which one ships.
"""

from django.test import SimpleTestCase

from apps.signals.management.commands.backtest import EXIT_LAB, _replay_exits


def bar(high, low, open_=None):
    return {"high": high, "low": low, "open": open_ if open_ is not None else (high + low) / 2}


def scheme(label_starts_with):
    """Index of an EXIT_LAB scheme, so the tests survive the list being reordered."""
    for i, (label, *_rest) in enumerate(EXIT_LAB):
        if label.strip().startswith(label_starts_with):
            return i
    raise AssertionError(f"no EXIT_LAB scheme starting {label_starts_with!r}")


# entry 100, stop 90 => 1R = 10 points. TP1/TP2/TP3 = 110/120/130.
ENTRY, STOP, TPS = 100.0, 90.0, [110.0, 120.0, 130.0]


class ReplayHorizonTests(SimpleTestCase):
    def test_horizon_cuts_the_replay_at_the_expiry_clock(self):
        # TP1 on bar 1, then a long grind up to TP3 on bar 5. With a 2-bar clock the
        # runner is still open when the call expires, so only the TP1 tranche pays.
        future = [bar(111, 100), bar(112, 108), bar(118, 110), bar(125, 115), bar(131, 120)]
        i = scheme("50/25/25, no BE")

        early = _replay_exits("BUY", ENTRY, STOP, TPS, future, atr=5.0, horizon=2)
        self.assertAlmostEqual(early[i], 0.5, places=9)   # ½ at TP1, ½ expires flat

        late = _replay_exits("BUY", ENTRY, STOP, TPS, future, atr=5.0, horizon=5)
        # ½ at TP1 (+1R) + ¼ at TP2 (+2R) + ¼ at TP3 (+3R)
        self.assertAlmostEqual(late[i], 0.5 + 0.5 + 0.75, places=9)

    def test_horizon_none_falls_back_to_the_hard_ceiling(self):
        future = [bar(111, 100), bar(121, 110), bar(131, 120)]
        i = scheme("50/25/25, no BE")
        self.assertAlmostEqual(
            _replay_exits("BUY", ENTRY, STOP, TPS, future, atr=5.0)[i],
            _replay_exits("BUY", ENTRY, STOP, TPS, future, atr=5.0, horizon=None)[i],
            places=9)

    def test_horizon_is_clamped_by_the_hard_ceiling(self):
        # A clock longer than REPLAY_BARS must not walk more bars than the ceiling.
        future = [bar(101, 99)] * 400 + [bar(131, 120)]
        i = scheme("all off @TP1")
        self.assertAlmostEqual(
            _replay_exits("BUY", ENTRY, STOP, TPS, future, atr=5.0, horizon=10_000)[i],
            0.0, places=9)  # never reached TP1 inside 300 bars -> expires flat


class ExpiryClosesFlatTests(SimpleTestCase):
    def test_unresolved_trade_expires_flat_not_at_its_stop(self):
        # Price chops between the entry and the stop and the clock runs out: live
        # books this as a 0R scratch, so every scheme must read 0R here. Closing the
        # remainder at the initial stop would report -1R — a loss that never happened.
        future = [bar(104, 96), bar(103, 97), bar(105, 95)]
        rs = _replay_exits("BUY", ENTRY, STOP, TPS, future, atr=5.0, horizon=3)
        for (label, *_rest), r in zip(EXIT_LAB, rs):
            self.assertAlmostEqual(r, 0.0, places=9, msg=f"{label.strip()} should expire flat")

    def test_a_real_stop_out_is_still_a_full_loss(self):
        # The flat-at-expiry rule must not swallow an actual stop hit.
        rs = _replay_exits("BUY", ENTRY, STOP, TPS, [bar(101, 89)], atr=5.0, horizon=5)
        for (label, *_rest), r in zip(EXIT_LAB, rs):
            self.assertAlmostEqual(r, -1.0, places=9, msg=f"{label.strip()} should lose 1R")

    def test_a_trailed_stop_in_profit_is_credited_at_expiry(self):
        # TP1 banks half, then price runs to 130 and stalls, so the 2xATR trail (ATR 5)
        # has dragged the runner's stop up to 120 = +2R before the clock runs out. That
        # 2R is locked in — a stop above entry cannot be taken back — so the runner is
        # credited with it rather than being written down to flat.
        future = [bar(111, 100), bar(130, 112), bar(129, 125)]
        i = scheme("50% TP1 + 50% trail 2xATR")
        rs = _replay_exits("BUY", ENTRY, STOP, TPS, future, atr=5.0, horizon=3)
        self.assertAlmostEqual(rs[i], 0.5 * 1.0 + 0.5 * 2.0, places=9)

    def test_banked_tranches_survive_an_expiry(self):
        # TP1 banked, stop moved to breakeven, then the clock runs out with the runner
        # open. The banked half is real money (§19.2) and the rest closes flat.
        future = [bar(111, 100), bar(112, 105)]
        i = scheme("live 50/25/25")
        self.assertAlmostEqual(
            _replay_exits("BUY", ENTRY, STOP, TPS, future, atr=5.0, horizon=2)[i],
            0.5, places=9)


class SellSideTests(SimpleTestCase):
    def test_sell_mirrors_buy(self):
        # entry 100, stop 110 => TP1/TP2/TP3 at 90/80/70.
        future = [bar(100, 89), bar(95, 79)]
        i = scheme("50/25/25, no BE")
        self.assertAlmostEqual(
            _replay_exits("SELL", 100.0, 110.0, [90.0, 80.0, 70.0], future,
                          atr=5.0, horizon=2)[i],
            0.5 + 0.5, places=9)   # ½ at TP1 (+1R), ¼ at TP2 (+2R), ¼ expires flat

    def test_degenerate_setup_returns_none(self):
        self.assertIsNone(
            _replay_exits("BUY", 100.0, 100.0, TPS, [bar(111, 100)], atr=5.0, horizon=5))
