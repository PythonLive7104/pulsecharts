from django.apps import AppConfig


class SignalsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.signals"
    label = "signals"

    def ready(self):
        # Apply the env-configured EMA-alignment gate to the module the engine and
        # backtest read, so SIGNAL_EMA_GATE can switch it without a code change.
        from django.conf import settings

        from . import pregate

        mode = getattr(settings, "SIGNAL_EMA_GATE", None)
        if mode:
            pregate.EMA_GATE_MODE = mode

        # 200-EMA trend filter: when False, the 200 EMA is dropped from every strategy
        # trigger (pregate) — the HTF regime check (tasks._regime_ok) reads the setting
        # directly — and the Fib zone becomes the entry confirmation instead.
        ema200 = getattr(settings, "SIGNAL_EMA200_TREND_FILTER", None)
        if ema200 is not None:
            pregate.EMA200_TREND_FILTER = bool(ema200)

        # Overextension guard (A): apply the env-configured ATR stretch limit to the
        # module the engine and backtest read, so it's tunable without a deploy.
        mult = getattr(settings, "SIGNAL_OVEREXT_ATR_MULT", None)
        if mult is not None:
            pregate.OVEREXT_ATR_MULT = float(mult)

        # Overbought/oversold cap (B): same env-driven wiring as the other gates.
        ob = getattr(settings, "SIGNAL_RSI_OVERBOUGHT", None)
        if ob is not None:
            pregate.RSI_OVERBOUGHT = float(ob)
        osold = getattr(settings, "SIGNAL_RSI_OVERSOLD", None)
        if osold is not None:
            pregate.RSI_OVERSOLD = float(osold)

        # Fib-pullback gate (D): env-driven like the other gates. Defaults to disabled
        # (MIN = 0.0) so it changes nothing live until deliberately switched on.
        fib_min = getattr(settings, "SIGNAL_FIB_PULLBACK_MIN", None)
        if fib_min is not None:
            pregate.FIB_PULLBACK_MIN = float(fib_min)
        fib_max = getattr(settings, "SIGNAL_FIB_PULLBACK_MAX", None)
        if fib_max is not None:
            pregate.FIB_PULLBACK_MAX = float(fib_max)

        self._check_confluence_vs_plan_caps(settings)

    @staticmethod
    def _check_confluence_vs_plan_caps(settings):
        """A plan's follow cap must be >= SIGNAL_CONFLUENCE_MIN, or that tier goes dark.

        Confluence counts agreement only among the strategies a user FOLLOWS (the feed
        filters candidates by followed_ids before collapse). If a plan can't follow
        enough strategies to reach the threshold, it is ARITHMETICALLY impossible for
        enough to agree — every setup is dropped and those users get zero signals, with
        no error and nothing in the UI to explain it. Free's cap was 2 when a 3-strategy
        floor was proposed; it would have silently killed the entire free tier.

        Fail at startup instead of in production.
        """
        from django.core.exceptions import ImproperlyConfigured

        from apps.accounts.plans import PLANS

        floor = int(getattr(settings, "SIGNAL_CONFLUENCE_MIN", 1) or 1)
        bad = {
            key: p["strategies"]
            for key, p in PLANS.items()
            if p.get("strategies", 0) < floor
        }
        if bad:
            raise ImproperlyConfigured(
                f"SIGNAL_CONFLUENCE_MIN={floor} exceeds the strategy follow cap of "
                f"{bad} — those users could never get enough strategies to agree and "
                f"would receive NO signals at all. Raise 'strategies' (and "
                f"'default_strategies') for those plans in apps/accounts/plans.py, or "
                f"lower SIGNAL_CONFLUENCE_MIN."
            )
