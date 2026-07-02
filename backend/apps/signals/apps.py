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
