from django.db import migrations, models


def verify_existing_users(apps, schema_editor):
    """Backfill: everyone who already exists at rollout is treated as verified.

    They were using the product before the gate existed — flipping the switch on
    without this would lock every current user out of their own account. New users
    (created after this migration) default to False and must verify.
    """
    User = apps.get_model("accounts", "User")
    User.objects.update(email_verified=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_alter_subscription_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_verified",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="email_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Backfill runs AFTER the columns exist. Irreversible on the data side (we
        # can't know who was verified before), so the reverse is a no-op.
        migrations.RunPython(verify_existing_users, migrations.RunPython.noop),
    ]
