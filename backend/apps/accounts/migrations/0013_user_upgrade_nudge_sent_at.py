from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_user_email_verified"),
    ]

    operations = [
        # Null = never nudged, so the first run of the task nudges everyone who is
        # currently without signal access. That's intended: the free tier losing its
        # signal feed is exactly what the nudge announces.
        migrations.AddField(
            model_name="user",
            name="upgrade_nudge_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
