"""Per-timeframe stop band and expiry clock.

A stop measured in ATR multiples is not frame-neutral: ATR scales with the bar, so one
multiple makes the faster frame's stop narrower in PERCENT, and since risk is the
denominator of R, the same fee costs more R there. On 7.7 years of BTC that single
confound — not signal quality — is what made the 1h feed unshippable: 1h and 4h won at
the same rate (53.6% / 53.9%) while 1h paid 0.046R a trade in costs against 0.022R.

So these tests guard the resolution ORDER rather than any particular number. A band
that silently falls back to the asset-class default on 1h reintroduces the exact bug,
and it would show up only as a slowly losing feed — never as an error.
"""

from django.test import SimpleTestCase, override_settings

from apps.signals.engine import _stop_mults
from apps.signals.tasks import _eval_bars_for

CRYPTO_BAND = {"crypto": 3.0, "forex": 2.0}
CRYPTO_CAP = {"crypto": 4.5, "forex": 3.0}


@override_settings(
    SIGNAL_ATR_STOP_FLOOR=CRYPTO_BAND,
    SIGNAL_ATR_STOP_CAP=CRYPTO_CAP,
    SIGNAL_ATR_BAND_BY_TIMEFRAME={"1h": (4.5, 6.75)},
)
class StopBandResolutionTests(SimpleTestCase):
    def test_timeframe_band_beats_the_asset_class_band(self):
        self.assertEqual(_stop_mults("crypto", "momentum-crossover", "1h"), (4.5, 6.75))

    def test_unlisted_timeframe_falls_through_to_the_asset_class(self):
        self.assertEqual(_stop_mults("crypto", "momentum-crossover", "4h"), (3.0, 4.5))

    def test_no_timeframe_given_falls_through(self):
        self.assertEqual(_stop_mults("crypto", "momentum-crossover"), (3.0, 4.5))

    def test_forex_keeps_its_own_band_on_an_overridden_frame(self):
        # The 1h override was measured on crypto. Forex has a different cost structure
        # (~11% of risk against crypto's 1-2%), so it must not inherit the number.
        self.assertEqual(_stop_mults("forex", "momentum-crossover", "4h"), (2.0, 3.0))

    @override_settings(
        SIGNAL_ATR_FLOOR_REVERSION={"crypto": 1.0, "forex": 1.5},
        SIGNAL_ATR_CAP_REVERSION={"crypto": 1.5, "forex": 2.0},
    )
    def test_reversion_ignores_the_timeframe_band(self):
        # A fade targets the mean 1-2 ATR away; a 4.5-6.75xATR stop would put TP1 past
        # where the trade was ever going, so the frame override must not reach it.
        self.assertEqual(_stop_mults("crypto", "bb-fade", "1h"), (1.0, 1.5))

    def test_unknown_asset_class_still_falls_back_to_crypto(self):
        self.assertEqual(_stop_mults("equities", "momentum-crossover", "4h"), (3.0, 4.5))


@override_settings(
    SIGNAL_EVAL_BARS=48,
    SIGNAL_EVAL_BARS_BY_ASSET={"crypto": 0, "forex": 192},
    SIGNAL_EVAL_BARS_BY_TIMEFRAME={"1h": 120},
)
class EvalBarsResolutionTests(SimpleTestCase):
    def test_timeframe_clock_wins(self):
        self.assertEqual(_eval_bars_for("crypto", "1h"), 120)

    def test_asset_class_clock_is_next(self):
        self.assertEqual(_eval_bars_for("forex", "4h"), 192)

    def test_timeframe_beats_asset_class(self):
        # Both configured for this call; the frame is the more specific statement.
        self.assertEqual(_eval_bars_for("forex", "1h"), 120)

    def test_global_default_is_the_floor(self):
        self.assertEqual(_eval_bars_for("crypto", "4h"), 48)

    def test_no_timeframe_given_keeps_the_old_behaviour(self):
        self.assertEqual(_eval_bars_for("crypto"), 48)
        self.assertEqual(_eval_bars_for("forex"), 192)


class BandParserTests(SimpleTestCase):
    """The env parsers raise instead of skipping a bad entry: a typo'd frame would
    leave it on the asset-class band while the operator believes it was overridden,
    and a stop geometry that quietly isn't applied is worse than a failed deploy."""

    def setUp(self):
        from config.settings import _parse_atr_bands, _parse_eval_bars_by_tf
        self.bands = _parse_atr_bands
        self.clocks = _parse_eval_bars_by_tf

    def test_parses_several_frames(self):
        self.assertEqual(self.bands("1h:4.5:6.75, 4h:3:4.5"),
                         {"1h": (4.5, 6.75), "4h": (3.0, 4.5)})

    def test_empty_is_no_override(self):
        self.assertEqual(self.bands(""), {})
        self.assertEqual(self.bands(None), {})
        self.assertEqual(self.clocks(""), {})

    def test_rejects_a_malformed_band(self):
        from django.core.exceptions import ImproperlyConfigured
        for raw in ("1h:4.5", "1h", ":4.5:6.75", "1h:x:6.75", "1h:4.5:6.75:9"):
            with self.assertRaises(ImproperlyConfigured, msg=raw):
                self.bands(raw)

    def test_rejects_an_inverted_or_zero_band(self):
        from django.core.exceptions import ImproperlyConfigured
        for raw in ("1h:6.75:4.5", "1h:0:4.5", "1h:-2:4.5"):
            with self.assertRaises(ImproperlyConfigured, msg=raw):
                self.bands(raw)

    def test_parses_clocks(self):
        self.assertEqual(self.clocks("1h:120,4h:48"), {"1h": 120, "4h": 48})

    def test_rejects_a_malformed_clock(self):
        from django.core.exceptions import ImproperlyConfigured
        for raw in ("1h", "1h:x", ":120", "1h:0", "1h:-5"):
            with self.assertRaises(ImproperlyConfigured, msg=raw):
                self.clocks(raw)
