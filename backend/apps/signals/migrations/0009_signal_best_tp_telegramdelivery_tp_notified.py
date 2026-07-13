from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("signals", "0008_signalservice_created_at_signalservice_owner_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="signal",
            name="best_tp",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="telegramdelivery",
            name="tp_notified",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
