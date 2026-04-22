from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0022_rename_basmaapp_au_entity__f59541_idx_basmaapp_au_entity__933f9e_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="entity",
            name="license_expire_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
