import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("plaza", "0003_exchangeaccount"),
    ]

    operations = [
        migrations.AddField(
            model_name="exchangeaccount",
            name="day_equity",
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name="exchangeaccount",
            name="halt_day",
            field=models.CharField(blank=True, max_length=10),
        ),
        migrations.AddField(
            model_name="exchangeaccount",
            name="halted",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="follow",
            name="armed_ms",
            field=models.BigIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="CopyLot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("coin", models.CharField(max_length=40)),
                ("side", models.CharField(blank=True, max_length=8)),
                ("qty", models.FloatField(default=0)),
                ("ratio", models.FloatField(blank=True, null=True)),
                ("entry", models.FloatField(default=0)),
                ("pending", models.FloatField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "follow",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lots",
                        to="plaza.follow",
                    ),
                ),
            ],
            options={"unique_together": {("follow", "coin")}},
        ),
        migrations.CreateModel(
            name="CopyLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("coin", models.CharField(blank=True, max_length=40)),
                ("status", models.CharField(max_length=16)),
                ("qty", models.FloatField(default=0)),
                ("detail", models.CharField(blank=True, max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "follow",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="copy_logs",
                        to="plaza.follow",
                    ),
                ),
            ],
            options={"ordering": ["-id"]},
        ),
    ]
