from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0029_managerqrcodetoken_manager_level"),
    ]

    operations = [
        migrations.AddField(
            model_name="entitysettings",
            name="manager_manual_require_biometric",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="manager_manual_require_face_liveness",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="manager_manual_require_photo_base64",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="manager_manual_single_use_token",
            field=models.BooleanField(default=True),
        ),
    ]

