import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("plaza", "0002_tradesignal"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExchangeAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("venue", models.CharField(max_length=16)),
                ("name", models.CharField(max_length=40)),
                ("key_last4", models.CharField(max_length=4)),
                ("key_fp", models.CharField(max_length=64)),
                ("key_enc", models.TextField()),
                ("secret_enc", models.TextField()),
                ("enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="exchange_accounts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"unique_together": {("user", "key_fp")}},
        ),
        migrations.AddField(
            model_name="follow",
            name="account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="follows",
                to="plaza.exchangeaccount",
            ),
        ),
    ]
