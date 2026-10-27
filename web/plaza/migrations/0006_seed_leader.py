from django.db import migrations


def seed_leader(apps, schema_editor):
    Leader = apps.get_model("plaza", "Leader")
    Leader.objects.get_or_create(
        address="0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1",
        defaults={"name": "主号", "enabled": True, "source": ""},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("plaza", "0005_leader_source"),
    ]

    operations = [
        migrations.RunPython(seed_leader, migrations.RunPython.noop),
    ]
