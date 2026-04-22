from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("basmaapp", "0037_entitysettings_activation_mode_and_lookup_field"),
    ]

    operations = [
        migrations.CreateModel(
            name="MobileActivationRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("requested_identifier", models.CharField(max_length=200)),
                ("lookup_field", models.CharField(blank=True, default="employee_no", max_length=20)),
                ("activation_mode", models.CharField(blank=True, default="option_2", max_length=20)),
                ("requested_device_uuid", models.CharField(blank=True, max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[("PENDING", "PENDING"), ("APPROVED", "APPROVED"), ("REJECTED", "REJECTED")],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("decision_note", models.CharField(blank=True, max_length=255)),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "decided_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="decided_activation_requests",
                        to="auth.user",
                    ),
                ),
                (
                    "employee",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="mobile_activation_requests", to="basmaapp.employee"),
                ),
                (
                    "entity",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="mobile_activation_requests", to="basmaapp.entity"),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="mobileactivationrequest",
            index=models.Index(fields=["entity", "status", "requested_at"], name="basmaapp_mo_entity__534f70_idx"),
        ),
        migrations.AddIndex(
            model_name="mobileactivationrequest",
            index=models.Index(fields=["employee", "status"], name="basmaapp_mo_employe_ee34fe_idx"),
        ),
    ]
