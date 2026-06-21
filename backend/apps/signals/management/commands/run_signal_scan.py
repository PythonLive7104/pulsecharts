"""Run one signal scan synchronously (no Celery worker needed).

    python manage.py run_signal_scan            # uses SIGNAL_SCAN_SYMBOL_LIMIT
    python manage.py run_signal_scan --limit 2  # only 2 symbols (cheap test)

Requires OPENAI_API_KEY to be set (it calls the LLM per symbol/timeframe/strategy).
"""

from django.core.management.base import BaseCommand

from apps.signals.tasks import run_scan


class Command(BaseCommand):
    help = "Run a single signal scan synchronously."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Max symbols to scan.")
        parser.add_argument(
            "--no-pregate",
            action="store_true",
            help="Bypass the rule-based pre-gate so every combo calls the LLM "
            "(forces real OpenAI calls — useful to verify the integration).",
        )

    def handle(self, *args, **options):
        use_pregate = False if options["no_pregate"] else None
        result = run_scan(symbol_limit=options["limit"], use_pregate=use_pregate)
        self.stdout.write(self.style.SUCCESS(f"Scan complete: {result}"))
