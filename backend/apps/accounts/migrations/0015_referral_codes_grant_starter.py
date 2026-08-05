from django.db import migrations, models


def grant_on_all_codes(apps, schema_editor):
    """Every existing referral code now grants the signup plan.

    The flag was previously opt-in (admin codes only), so without this backfill the
    change would apply to codes created from today onward and silently skip every
    user who already shared their link — the people most likely to notice.
    """
    ReferralCode = apps.get_model("accounts", "ReferralCode")
    ReferralCode.objects.filter(grants_signup_plan=False).update(grants_signup_plan=True)


def revoke_on_personal_codes(apps, schema_editor):
    """Reverse: restore the old behaviour — only owner-less (admin) codes grant."""
    ReferralCode = apps.get_model("accounts", "ReferralCode")
    ReferralCode.objects.filter(owner__isnull=False).update(grants_signup_plan=False)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_referralcommission"),
    ]

    operations = [
        migrations.AlterField(
            model_name="referralcode",
            name="grants_signup_plan",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(grant_on_all_codes, revoke_on_personal_codes),
    ]
