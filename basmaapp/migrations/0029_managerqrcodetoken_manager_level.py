from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0028_managerqrcodetoken"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="managerqrcodetoken",
            name="basmaapp_ma_entity__995b9b_idx",
        ),
        migrations.RemoveField(
            model_name="managerqrcodetoken",
            name="employee",
        ),
        migrations.AddIndex(
            model_name="managerqrcodetoken",
            index=models.Index(fields=["entity", "manager", "created_at"], name="basmaapp_ma_entity__1d37b2_idx"),
        ),
    ]

