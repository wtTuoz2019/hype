from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("plaza", "0004_copy"),
    ]

    operations = [
        migrations.AddField(
            model_name="leader",
            name="source",
            field=models.CharField(blank=True, max_length=16),
        ),
    ]
