from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market_data", "0005_symbol_min_plan"),
    ]

    operations = [
        # Defaults True: every existing symbol keeps being scanned, so the field is
        # inert until a symbol is explicitly excluded. Charts and watchlists never
        # consult it — see the model comment.
        migrations.AddField(
            model_name="symbol",
            name="signals_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
