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

from apps.signals.versioning import ENGINE_SETTINGS

# The engine-tuning block lives in apps.signals.versioning so this report and a
# StrategyVersion snapshot read one list. Deliberately ONLY the signal-engine tuning
# block: infrastructure and secrets differ between machines by design and must never
# be copied from production to a dev checkout.
SETTINGS = ENGINE_SETTINGS


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
                          f"{settings.SIGNAL_ATR_FLOOR_REVERSION['crypto']}-"
                          f"{settings.SIGNAL_ATR_CAP_REVERSION['crypto']} crypto / "
                          f"{settings.SIGNAL_ATR_FLOOR_REVERSION['forex']}-"
                          f"{settings.SIGNAL_ATR_CAP_REVERSION['forex']} forex")
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

        # What the NEXT signal from each strategy will be stamped with. Read-only:
        # computes the fingerprint and looks it up, never writes a version row.
        from apps.signals.models import StrategyVersion
        from apps.signals.versioning import (
            active_roster, code_hash, engine_snapshot, fingerprint, snapshot_for,
        )

        engine, roster = engine_snapshot(), active_roster()
        self.stdout.write(self.style.MIGRATE_HEADING("\nStrategy versions in force"))
        self.stdout.write(f"  {'rule code hash':34s} {code_hash()[:12]}")
        for svc in SignalService.objects.filter(is_active=True, owner__isnull=True):
            fp = fingerprint(snapshot_for(svc, engine=engine, roster=roster))
            known = StrategyVersion.objects.filter(service=svc, fingerprint=fp).first()
            label = f"v{known.number}" if known else "NEW — minted on its next signal"
            self.stdout.write(f"  {svc.slug:34s} {label} ({fp[:12]})")
