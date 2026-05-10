from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0042_appglobalsettings_default_row"),
    ]

    operations = [
        migrations.AddField(
            model_name="appglobalsettings",
            name="store_review_user_id",
            field=models.PositiveIntegerField(
                default=1,
                help_text="Django User.pk whose Employee profile is used when store review mode is on.",
            ),
        ),
        migrations.AlterField(
            model_name="appglobalsettings",
            name="store_review_mode",
            field=models.BooleanField(
                default=False,
                help_text="When enabled, verify-uuid skips UUID matching and uses the Employee linked to store_review_user_id.",
            ),
        ),
    ]
