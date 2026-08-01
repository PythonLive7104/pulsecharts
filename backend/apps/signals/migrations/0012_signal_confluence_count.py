from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("signals", "0011_signal_daily_ema200_aligned"),
    ]

    operations = [
        # Null on every existing row: we can't reconstruct how many strategies
        # agreed at delivery time after the fact, and guessing would poison the
        # very comparison this field exists to make. Stats must treat null as
        # "unknown", not as 1.
        migrations.AddField(
            model_name="signal",
            name="confluence_count",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
