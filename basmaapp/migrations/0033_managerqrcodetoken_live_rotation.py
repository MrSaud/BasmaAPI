from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0032_entitysettings_normal_sign_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="managerqrcodetoken",
            name="live_rotation_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="managerqrcodetoken",
            name="live_rotation_interval_sec",
            field=models.PositiveIntegerField(default=10),
        ),
        migrations.AddField(
            model_name="managerqrcodetoken",
            name="live_rotation_grace_steps",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="managerqrcodetoken",
            name="live_secret",
            field=models.CharField(blank=True, max_length=128),
        ),
    ]
