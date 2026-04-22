from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0033_managerqrcodetoken_live_rotation"),
    ]

    operations = [
        migrations.AddField(
            model_name="entitysettings",
            name="secondary_theme_color",
            field=models.CharField(blank=True, max_length=7),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="font_h1",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="font_h2",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="font_h3",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="font_h4",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="font_p",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="font_label",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
