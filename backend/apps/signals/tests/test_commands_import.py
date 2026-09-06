"""Every management command must at least IMPORT and expose its arguments.

`manage.py check` does not import management commands, so a NameError or a wrong
enum member inside one is invisible until someone runs it — which, for the
diagnostic commands here, means on the production server during an incident. That
has now happened twice:

    Signal.Outcome.INVALID       (the MEMBER is INVALIDATED; INVALID is its VALUE)
    _custom_signal(...)          missing a positional arg added to the signature

Both were one-word mistakes that any import would have caught. This walks every
command in every app and loads it, so the cheapest class of breakage cannot reach
the server again.
"""

from django.core.management import get_commands, load_command_class
from django.test import SimpleTestCase

# Commands owned by this project — third-party and Django built-ins are not ours to
# police, and importing some of them has side effects.
OUR_APPS = ("apps.signals", "apps.accounts", "apps.market_data",
            "apps.watchlists", "apps.billing", "apps.execution", "apps.alerts")


class CommandImportTests(SimpleTestCase):
    def test_every_command_imports_and_builds_its_parser(self):
        checked = []
        for name, app in sorted(get_commands().items()):
            if app not in OUR_APPS:
                continue
            with self.subTest(command=name):
                cmd = load_command_class(app, name)
                # Building the parser exercises add_arguments too, where flag typos
                # and duplicate dests live.
                cmd.create_parser("manage.py", name)
                checked.append(name)
        # Guard against the test silently passing because the app filter stopped
        # matching anything (a rename would otherwise make this a no-op).
        self.assertGreater(len(checked), 10, f"only found {checked}")
