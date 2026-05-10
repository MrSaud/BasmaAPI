# Ensures singleton row exists so Django admin and get_solo() see one record.

from django.db import migrations


def create_singleton(apps, schema_editor):
    AppGlobalSettings = apps.get_model("basmaapp", "AppGlobalSettings")
    AppGlobalSettings.objects.get_or_create(pk=1)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0041_app_global_settings"),
    ]

    operations = [
        migrations.RunPython(create_singleton, noop_reverse),
    ]
