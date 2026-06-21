"""Resolve pending signals against later price (no LLM, ~free).

    python manage.py evaluate_signals
    python manage.py evaluate_signals --limit 50
"""

from django.core.management.base import BaseCommand

from apps.signals.tasks import run_evaluation


class Command(BaseCommand):
    help = "Evaluate pending signal outcomes (TP/SL/expired)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Max signals to evaluate.")

    def handle(self, *args, **options):
        result = run_evaluation(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(f"Evaluation complete: {result}"))
