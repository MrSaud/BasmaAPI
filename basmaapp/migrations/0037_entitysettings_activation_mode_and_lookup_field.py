from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0036_manager_manual_geofence_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="entitysettings",
            name="activation_lookup_field",
            field=models.CharField(
                choices=[
                    ("employee_no", "Employee Number"),
                    ("civil_id", "Civil ID"),
                    ("username", "Username"),
                ],
                default="employee_no",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="entitysettings",
            name="activation_mode",
            field=models.CharField(
                choices=[
                    ("option_1", "1.By scan qrcode"),
                    ("option_2", "2. by admin approval"),
                ],
                default="option_1",
                max_length=20,
            ),
        ),
    ]
