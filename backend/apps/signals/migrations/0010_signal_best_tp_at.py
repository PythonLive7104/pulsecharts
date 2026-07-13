from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("signals", "0009_signal_best_tp_telegramdelivery_tp_notified"),
    ]

    operations = [
        migrations.AddField(
            model_name="signal",
            name="best_tp_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
