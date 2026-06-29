from django.db import migrations, models


def deactivate_unlinked(apps, schema_editor):
    """Set telegram_active=False for users who were never actually linked.

    The 0006 migration added telegram_active with default=True, so every existing
    user — including everyone who never touched Telegram — ended up "active" with
    an empty chat_id. Delivery was never wrong (the push task excludes empty
    chat_ids), but the flag was misleading. Bring it in line with reality: only
    rows with a chat on file stay as they are; the rest go inactive.
    """
    User = apps.get_model("accounts", "User")
    User.objects.filter(telegram_chat_id="").update(telegram_active=False)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_user_telegram_active"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="telegram_active",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(deactivate_unlinked, noop),
    ]
