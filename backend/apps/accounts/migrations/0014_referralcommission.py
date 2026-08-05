from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_user_upgrade_nudge_sent_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReferralCommission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("referred_email", models.EmailField(blank=True, default="", max_length=254)),
                ("code", models.CharField(blank=True, default="", max_length=40)),
                # Unique: a replayed webhook must never create a second commission
                # for the same charge.
                ("payment_ref", models.CharField(max_length=128, unique=True)),
                ("plan", models.CharField(max_length=16)),
                ("amount_usd", models.DecimalField(decimal_places=2, max_digits=10)),
                ("rate_pct", models.DecimalField(decimal_places=2, max_digits=5)),
                ("commission_usd", models.DecimalField(decimal_places=2, max_digits=10)),
                ("status", models.CharField(
                    choices=[("PENDING", "Pending payout"), ("PAID", "Paid"),
                             ("VOID", "Void (refunded/disputed)")],
                    db_index=True, default="PENDING", max_length=8)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("payout_note", models.CharField(blank=True, default="", max_length=200)),
                ("referred_user", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="commissions_generated", to="accounts.user")),
                ("referrer", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="referral_commissions", to="accounts.user")),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.AddIndex(
            model_name="referralcommission",
            index=models.Index(fields=["referrer", "status"],
                               name="accounts_re_referre_84f077_idx"),
        ),
    ]
