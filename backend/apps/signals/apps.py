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
