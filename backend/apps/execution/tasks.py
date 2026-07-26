"""Celery tasks for the auto-trade executor (Section 13-adjacent, v2).

Both tasks self-gate on ``settings.AUTO_TRADE_ENABLED`` (the executor functions do
the check), so leaving them in the beat schedule is harmless while the feature is
off — they return a 'skipped' summary without touching a broker.
"""

from celery import shared_task

from .executor import run_auto_trades, run_reconcile


@shared_task(name="apps.execution.tasks.place_auto_trades")
def place_auto_trades():
    return run_auto_trades()


@shared_task(name="apps.execution.tasks.reconcile_positions")
def reconcile_positions():
    return run_reconcile()
