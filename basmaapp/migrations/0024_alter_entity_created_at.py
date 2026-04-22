from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0023_entity_license_expire_date"),
    ]

    operations = [
        migrations.AlterField(
            model_name="entity",
            name="created_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]
