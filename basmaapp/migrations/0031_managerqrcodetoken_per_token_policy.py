from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0030_entitysettings_manager_manual_signing_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="managerqrcodetoken",
            name="require_biometric",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="managerqrcodetoken",
            name="require_face_liveness",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="managerqrcodetoken",
            name="require_photo_base64",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="managerqrcodetoken",
            name="single_use_token",
            field=models.BooleanField(default=True),
        ),
    ]

