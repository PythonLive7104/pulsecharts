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
