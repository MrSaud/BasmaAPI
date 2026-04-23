from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0039_rename_basmaapp_ma_entity__1d37b2_idx_basmaapp_ma_entity__3caab0_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="entitysettings",
            name="manager_manual_use_face_compare",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="manager_manual_use_liveness_check",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="normal_sign_use_face_compare",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="normal_sign_use_liveness_check",
            field=models.BooleanField(default=True),
        ),
    ]
