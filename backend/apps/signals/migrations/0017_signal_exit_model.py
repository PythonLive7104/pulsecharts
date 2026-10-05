import apps.signals.models
from django.db import migrations, models


class Migration(migrations.Migration):
    """Signal.exit_model.

    Two steps on purpose. Every existing row was issued — and managed by users — under
    the 50/25/25 scale-out, so it is backfilled with the literal "scaleout". Only then
    does the default switch to the live setting for new rows. A single AddField with
    the callable default would stamp history with whatever SIGNAL_EXIT_MODEL is at
    migrate time ("tp1"), silently rescoring every past trade.
    """

    dependencies = [
        ("signals", "0016_strategyversion"),
    ]

    operations = [
        migrations.AddField(
            model_name="signal",
            name="exit_model",
            field=models.CharField(
                choices=[("tp1", "Full exit at TP1"), ("scaleout", "50/25/25 scale-out")],
                default="scaleout",
                max_length=12,
            ),
        ),
        migrations.AlterField(
            model_name="signal",
            name="exit_model",
            field=models.CharField(
                choices=[("tp1", "Full exit at TP1"), ("scaleout", "50/25/25 scale-out")],
                default=apps.signals.models.current_exit_model,
                max_length=12,
            ),
        ),
    ]
