from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("signals", "0010_signal_best_tp_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="signal",
            name="daily_ema200_aligned",
            field=models.BooleanField(blank=True, null=True),
        ),
    ]
