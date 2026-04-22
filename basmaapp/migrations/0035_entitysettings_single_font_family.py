from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0034_entitysettings_secondary_theme_and_fonts"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="entitysettings",
            name="font_h1",
        ),
        migrations.RemoveField(
            model_name="entitysettings",
            name="font_h2",
        ),
        migrations.RemoveField(
            model_name="entitysettings",
            name="font_h3",
        ),
        migrations.RemoveField(
            model_name="entitysettings",
            name="font_h4",
        ),
        migrations.RemoveField(
            model_name="entitysettings",
            name="font_p",
        ),
        migrations.RemoveField(
            model_name="entitysettings",
            name="font_label",
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="font_family",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
