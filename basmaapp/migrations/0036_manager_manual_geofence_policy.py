from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0035_entitysettings_single_font_family"),
    ]

    operations = [
        migrations.AddField(
            model_name="entitysettings",
            name="manager_manual_require_geofence",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="managerqrcodetoken",
            name="require_geofence",
            field=models.BooleanField(default=True),
        ),
    ]

