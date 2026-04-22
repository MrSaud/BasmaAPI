from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0031_managerqrcodetoken_per_token_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="entitysettings",
            name="normal_sign_require_biometric",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="normal_sign_require_face_liveness",
            field=models.BooleanField(default=True),
        ),
    ]

