from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0027_employeelocationassignment_period_to_take_action"),
    ]

    operations = [
        migrations.CreateModel(
            name="ManagerQRCodeToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[("SIGN_IN", "SIGN_IN"), ("SIGN_CONFIRM", "SIGN_CONFIRM"), ("SIGN_OUT", "SIGN_OUT")], max_length=20)),
                ("token", models.CharField(max_length=120, unique=True)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="received_manager_qr_tokens", to="basmaapp.employee")),
                ("entity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="manager_qr_tokens", to="basmaapp.entity")),
                ("location", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="manager_qr_tokens", to="basmaapp.location")),
                ("manager", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="issued_manager_qr_tokens", to="basmaapp.employee")),
            ],
        ),
        migrations.AddIndex(
            model_name="managerqrcodetoken",
            index=models.Index(fields=["entity", "employee", "created_at"], name="basmaapp_ma_entity__995b9b_idx"),
        ),
        migrations.AddIndex(
            model_name="managerqrcodetoken",
            index=models.Index(fields=["token"], name="basmaapp_ma_token_a18fed_idx"),
        ),
        migrations.AddIndex(
            model_name="managerqrcodetoken",
            index=models.Index(fields=["expires_at", "used_at"], name="basmaapp_ma_expires_8dda2f_idx"),
        ),
    ]

