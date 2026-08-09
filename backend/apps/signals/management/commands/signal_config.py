"""Print the EFFECTIVE signal-engine configuration of a running instance.

`.env` is gitignored and edited per-machine, so the repo copy and the server copy
drift silently — and they have: a setting the notes recorded as off was on in
production for weeks, which quietly invalidated a round of tuning conclusions.

This prints what the process actually resolved, including the module-level gate
state that SignalsConfig.ready() seeds from env (which no .env file shows you
directly). Paste its output when comparing environments or reporting a result, so
a number is never argued about again.

    manage.py signal_config
    manage.py signal_config --env      # as .env lines, ready to paste
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

# (env var name, settings attribute). Deliberately ONLY the signal-engine tuning
# block: infrastructure and secrets differ between machines by design and must never
# be copied from production to a dev checkout.
SETTINGS = [
    ("SIGNAL_ENGINE_ENABLED", "SIGNAL_ENGINE_ENABLED"),
    ("SIGNAL_SHADOW_MODE", "SIGNAL_SHADOW_MODE"),
    ("SIGNAL_ENGINE_MODE", "SIGNAL_ENGINE_MODE"),
    ("SIGNAL_PREGATE_ENABLED", "SIGNAL_PREGATE_ENABLED"),
    ("SIGNAL_MIN_CONFIDENCE", "SIGNAL_MIN_CONFIDENCE"),
    ("SIGNAL_MIN_CONFIDENCE_REVERSION", "SIGNAL_MIN_CONFIDENCE_REVERSION"),
    ("SIGNAL_MIN_CONFIDENCE_BY_STRATEGY", "SIGNAL_MIN_CONFIDENCE_BY_STRATEGY"),
    ("SIGNAL_TIMEFRAMES", "SIGNAL_TIMEFRAMES"),
    ("SIGNAL_CONFLUENCE_MIN", "SIGNAL_CONFLUENCE_MIN"),
    ("SIGNAL_CONFLUENCE_MIN_REVERSION", "SIGNAL_CONFLUENCE_MIN_REVERSION"),
    ("SIGNAL_REGIME_FILTER_ENABLED", "SIGNAL_REGIME_FILTER_ENABLED"),
    ("SIGNAL_ADX_MIN", "SIGNAL_ADX_MIN"),
    ("SIGNAL_ADX_MAX_REVERSION", "SIGNAL_ADX_MAX_REVERSION"),
    ("SIGNAL_EMA_SEP_MIN_ATR", "SIGNAL_EMA_SEP_MIN_ATR"),
    ("SIGNAL_EMA_GATE", "SIGNAL_EMA_GATE"),
    ("SIGNAL_EMA200_TREND_FILTER", "SIGNAL_EMA200_TREND_FILTER"),
    ("SIGNAL_STRUCTURE_TREND_FILTER", "SIGNAL_STRUCTURE_TREND_FILTER"),
    ("SIGNAL_HTF_REGIME_ENABLED", "SIGNAL_HTF_REGIME_ENABLED"),
    ("SIGNAL_RSI_OVERBOUGHT", "SIGNAL_RSI_OVERBOUGHT"),
    ("SIGNAL_RSI_OVERSOLD", "SIGNAL_RSI_OVERSOLD"),
    ("SIGNAL_OVEREXT_ATR_MULT", "SIGNAL_OVEREXT_ATR_MULT"),
    ("SIGNAL_REENTRY_COOLDOWN_BARS", "SIGNAL_REENTRY_COOLDOWN_BARS"),
    ("SIGNAL_EXIT_ON_TREND_BREAK", "SIGNAL_EXIT_ON_TREND_BREAK"),
    ("SIGNAL_HTF_STRUCTURE_ENABLED", "SIGNAL_HTF_STRUCTURE_ENABLED"),
    ("SIGNAL_EVAL_BARS", "SIGNAL_EVAL_BARS"),
    ("SIGNAL_FIB_PULLBACK_MIN", "SIGNAL_FIB_PULLBACK_MIN"),
    ("SIGNAL_FIB_PULLBACK_MAX", "SIGNAL_FIB_PULLBACK_MAX"),
    ("SIGNAL_SKIP_CRYPTO_WEEKEND", "SIGNAL_SKIP_CRYPTO_WEEKEND"),
    ("SIGNAL_SCAN_SYMBOL_LIMIT", "SIGNAL_SCAN_SYMBOL_LIMIT"),
    ("SIGNAL_FREE_TRIAL_DAYS", "SIGNAL_FREE_TRIAL_DAYS"),
    ("SIGNAL_UPGRADE_NUDGE_DAYS", "SIGNAL_UPGRADE_NUDGE_DAYS"),
]


# Read straight into CELERY_BEAT_SCHEDULE rather than a settings attribute, so it has
# to be pulled out of the schedule or it reports as missing.
BEAT_INTERVALS = [
    ("SIGNAL_SCAN_INTERVAL", "scan-signals"),
    ("SIGNAL_EVAL_INTERVAL", "evaluate-signals"),
    ("TELEGRAM_PUSH_INTERVAL", "push-telegram-signals"),
]


def _beat(entry):
    return (getattr(settings, "CELERY_BEAT_SCHEDULE", {}).get(entry) or {}).get("schedule", "—")


def _fmt(value):
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return value


class Command(BaseCommand):
    help = "Print the effective signal-engine config (compare server vs repo .env)."

    def add_arguments(self, parser):
        parser.add_argument("--env", action="store_true",
                            help="Print as KEY=value .env lines instead of a table.")

    def handle(self, *args, **opts):
        from apps.signals import pregate
        from apps.signals.models import SignalService
        from apps.signals.tasks import _adx_min_now

        if opts["env"]:
            for name, attr in SETTINGS:
                self.stdout.write(f"{name}={_fmt(getattr(settings, attr, ''))}")
            for name, entry in BEAT_INTERVALS:
                self.stdout.write(f"{name}={_beat(entry)}")
            return

        self.stdout.write(self.style.MIGRATE_HEADING("Effective signal config"))
        for name, attr in SETTINGS:
            self.stdout.write(f"  {name:34s} {_fmt(getattr(settings, attr, '—'))}")

        for name, entry in BEAT_INTERVALS:
            self.stdout.write(f"  {name:34s} {_beat(entry)}")

        # Module state seeded at startup — NOT visible in any .env, and the source of
        # the "two settings both called the 200 EMA" confusion.
        self.stdout.write(self.style.MIGRATE_HEADING("\nResolved gate state (pregate module)"))
        self.stdout.write(f"  {'EMA_GATE_MODE':34s} {pregate.EMA_GATE_MODE}")
        self.stdout.write(f"  {'EMA200_TREND_FILTER':34s} {pregate.EMA200_TREND_FILTER}")
        self.stdout.write(f"  {'STRUCTURE_TREND_FILTER':34s} {pregate.STRUCTURE_TREND_FILTER}")
        self.stdout.write(f"  {'OVEREXT_ATR_MULT':34s} {pregate.OVEREXT_ATR_MULT}")
        self.stdout.write(f"  {'RSI_OVERBOUGHT / OVERSOLD':34s} "
                          f"{pregate.RSI_OVERBOUGHT} / {pregate.RSI_OVERSOLD}")
        self.stdout.write(f"  {'ADX floor in force today':34s} {_adx_min_now()}")
        # Stop geometry: dicts keyed by asset class, plus the reversion pair. These
        # decide how far the stop sits and therefore where every TP lands, so they
        # belong in any config comparison.
        floor, cap = settings.SIGNAL_ATR_STOP_FLOOR, settings.SIGNAL_ATR_STOP_CAP
        self.stdout.write(f"  {'ATR stop crypto (floor-cap)':34s} "
                          f"{floor.get('crypto')}-{cap.get('crypto')}")
        self.stdout.write(f"  {'ATR stop forex  (floor-cap)':34s} "
                          f"{floor.get('forex')}-{cap.get('forex')}")
        self.stdout.write(f"  {'ATR stop reversion (floor-cap)':34s} "
                          f"{settings.SIGNAL_ATR_FLOOR_REVERSION}-{settings.SIGNAL_ATR_CAP_REVERSION}")
        by_day = getattr(settings, "SIGNAL_ADX_MIN_BY_WEEKDAY", {})
        if any(by_day.values()):
            self.stdout.write(f"  {'ADX per-weekday overrides':34s} {by_day}")

        active = list(
            SignalService.objects.filter(is_active=True, owner__isnull=True)
            .values_list("slug", flat=True)
        )
        self.stdout.write(self.style.MIGRATE_HEADING("\nActive built-in strategies"))
        for slug in active:
            self.stdout.write(f"  {slug:34s} {pregate.kind_of(slug)}")
        self.stdout.write(f"  ({len(active)} active)")
