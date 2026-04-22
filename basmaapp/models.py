from django.db import models

# Create your models here.
import uuid
from django.db import models
from django.contrib.auth.models import User




class Entity(models.Model):
    """
    SaaS Tenant (Company/Organization).
    """
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=50, unique=True)  # short unique tenant code
    license_expire_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.code})"


class EntitySettings(models.Model):
    """
    Per-entity configuration: logo, gps/beacon mode, etc.
    """
    entity = models.OneToOneField(Entity, on_delete=models.CASCADE, related_name="settings")
    display_name = models.CharField(max_length=200, blank=True)
    theme_color = models.CharField(max_length=7, blank=True)  # e.g. #RRGGBB
    secondary_theme_color = models.CharField(max_length=7, blank=True)  # e.g. #RRGGBB
    font_family = models.CharField(max_length=120, blank=True)
    logo64 = models.TextField(blank=True)  # Base64-encoded logo
    number_employees = models.PositiveIntegerField(null=True, blank=True)
    manager_manual_require_biometric = models.BooleanField(default=True)
    manager_manual_require_face_liveness = models.BooleanField(default=True)
    manager_manual_require_photo_base64 = models.BooleanField(default=False)
    manager_manual_single_use_token = models.BooleanField(default=True)
    manager_manual_require_geofence = models.BooleanField(default=True)
    normal_sign_require_biometric = models.BooleanField(default=True)
    normal_sign_require_face_liveness = models.BooleanField(default=True)
    activation_lookup_field = models.CharField(
        max_length=20,
        choices=(
            ("employee_no", "Employee Number"),
            ("civil_id", "Civil ID"),
            ("username", "Username"),
        ),
        default="employee_no",
    )
    activation_mode = models.CharField(
        max_length=20,
        choices=(
            ("option_1", "1.By scan qrcode"),
            ("option_2", "2. by admin approval"),
        ),
        default="option_1",
    )
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Settings for {self.entity}"


class Employee(models.Model):
    """
    Employee belongs to an Entity. Linked to Django auth user.
    """
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="employees")
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="employee_profile")
      # 🔐 Public UUID used by mobile app
    employee_uuid = models.CharField(max_length=36, default=uuid.uuid4, unique=True)
    last_updated_UUID_at = models.DateTimeField(null=True, blank=True)
    updated_UUID_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="uuid_updates")
    
    # 🔐 Device binding inf
    device_uuid = models.CharField(max_length=36, blank=True)
    device_bound_at = models.DateTimeField(null=True, blank=True)

    photo_base64 = models.TextField(blank=True)  # optional photo evidence
    
    employee_no = models.CharField(max_length=50, blank=True)
    full_name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    civil_id = models.CharField(max_length=50, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)

    employee_parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="subordinates")  # for hierarchy (e.g. manager -> employees)
    is_manager = models.BooleanField(default=False)
    number_working_hours_per_day = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True, default=7.00)  # e.g. 8.00

    # Mobile device / biometric registration reference (do NOT store biometric templates here)
    device_id = models.CharField(max_length=100, blank=True)  # e.g. device UUID
    biometric_enrolled = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    confirm_sign_period_minutes = models.PositiveIntegerField(default=15)  # time window to allow SIGN_CONFIRM after SIGN_IN

    class Meta:
        unique_together = ("entity", "employee_no")

    def __str__(self):
        return f"{self.full_name} - {self.entity.code}"


class MobileActivationRequest(models.Model):
    STATUS_PENDING = "PENDING"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"
    STATUS_CHOICES = (
        (STATUS_PENDING, "PENDING"),
        (STATUS_APPROVED, "APPROVED"),
        (STATUS_REJECTED, "REJECTED"),
    )

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="mobile_activation_requests")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="mobile_activation_requests")
    requested_identifier = models.CharField(max_length=200)
    lookup_field = models.CharField(max_length=20, blank=True, default="employee_no")
    activation_mode = models.CharField(max_length=20, blank=True, default="option_2")
    requested_device_uuid = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="decided_activation_requests")
    decision_note = models.CharField(max_length=255, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["entity", "status", "requested_at"]),
            models.Index(fields=["employee", "status"]),
        ]

    def __str__(self):
        return f"ActivationRequest {self.employee_id} {self.status}"


class Location(models.Model):
    """
    A work site / office location for an entity.
    Can be GPS-based and/or Beacon-based.
    """
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="locations")

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    # GPS center point (nullable if beacon-only)
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    is_GPS_based = models.BooleanField(default=True)
    is_beacon_based = models.BooleanField(default=False)
    major_value = models.PositiveIntegerField(null=True, blank=True)  # for beacon-based location
    minor_value = models.PositiveIntegerField(null=True, blank=True)  # for beacon-based location
    rssi_threshold = models.IntegerField(null=True, blank=True)  # for beacon-based proximity
    beacon_uuid = models.CharField(max_length=64, blank=True)  # for beacon-based location
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.entity.code})"




class EmployeeLocationAssignment(models.Model):
    """
    Entity can assign specific locations to employees.
    If empty => employee can use any entity location (business rule).
    """
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="assignments")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="location_assignments")
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="employee_assignments")

    is_active = models.BooleanField(default=True)
   
    allow_sign_in = models.BooleanField(default=True)
    allow_sign_out = models.BooleanField(default=True)
    allow_sign_confirm = models.BooleanField(default=True)  # allow confirming sign-in (e.g. for managers)
    gps_radius_meters = models.PositiveIntegerField(default=100)
    period_to_take_action = models.PositiveIntegerField(default=1)  # minutes

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    class Meta:
        unique_together = ("employee", "location")

    def __str__(self):
        return f"{self.employee.full_name} -> {self.location.name}"


class AttendanceTransaction(models.Model):
    """
    Immutable audit log for each action: SIGN_IN / SIGN_CONFIRM / SIGN_OUT
    Stores the evidence: GPS, beacon, device, biometric result, etc.
    """
    ACTION_CHOICES = (
        ("SIGN_IN", "SIGN_IN"),
        ("SIGN_CONFIRM", "SIGN_CONFIRM"),
        ("SIGN_OUT", "SIGN_OUT"),
    )

    VERIFY_CHOICES = (
        ("PENDING", "PENDING"),
        ("PASSED", "PASSED"),
        ("FAILED", "FAILED"),
    )

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="attendance_transactions")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="attendance_transactions")

    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    occurred_at = models.DateTimeField(auto_now_add=True)

    # Where was the employee?
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True)

    # Evidence captured by mobile
    gps_latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_accuracy_m = models.PositiveIntegerField(null=True, blank=True)

    beacon_uuid = models.CharField(max_length=64, blank=True, null=True)
    beacon_major = models.PositiveIntegerField(null=True, blank=True)
    beacon_minor = models.PositiveIntegerField(null=True, blank=True)
    beacon_rssi = models.IntegerField(null=True, blank=True)

    device_id = models.CharField(max_length=100, blank=True, null=True)

    # biometric verification result (from mobile)
    biometric_verify = models.CharField(max_length=10, choices=VERIFY_CHOICES, default="PENDING")
    biometric_method = models.CharField(max_length=50, blank=True)  # FaceID/TouchID/AndroidBiometric
    biometric_error = models.CharField(max_length=300, blank=True)
    photo_base64 = models.TextField(blank=True)  # optional photo evidence

    # Optional: store mobile signature / anti-replay
    request_nonce = models.CharField(max_length=120, blank=True)

    transaction_comment = models.CharField(max_length=300, blank=True, null=True)  # e.g. reason for failed verification or other notes
    class Meta:
        indexes = [
            models.Index(fields=["entity", "employee", "occurred_at"]),
            models.Index(fields=["entity", "action", "occurred_at"]),
        ]

    def __str__(self):
        return f"{self.employee.full_name} {self.action} {self.occurred_at}"
    

class ManagerQRCodeToken(models.Model):
    """
    One-time short-lived QR token created by manager for supervised attendance.
    """
    ACTION_CHOICES = AttendanceTransaction.ACTION_CHOICES

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="manager_qr_tokens")
    manager = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="issued_manager_qr_tokens")
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="manager_qr_tokens")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    require_biometric = models.BooleanField(default=True)
    require_face_liveness = models.BooleanField(default=True)
    require_photo_base64 = models.BooleanField(default=False)
    single_use_token = models.BooleanField(default=True)
    require_geofence = models.BooleanField(default=True)
    live_rotation_enabled = models.BooleanField(default=True)
    live_rotation_interval_sec = models.PositiveIntegerField(default=10)
    live_rotation_grace_steps = models.PositiveIntegerField(default=1)
    live_secret = models.CharField(max_length=128, blank=True)
    token = models.CharField(max_length=120, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["entity", "manager", "created_at"]),
            models.Index(fields=["token"]),
            models.Index(fields=["expires_at", "used_at"]),
        ]

    def __str__(self):
        return f"QR token {self.action} by {self.manager.full_name}"
    

class InboxMessage(models.Model):
    """
    Messages sent to employees' inbox.
    """
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="inbox_messages")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="inbox_messages")

    subject = models.CharField(max_length=200)
    body = models.TextField()
    is_deleted = models.BooleanField(default=False)

    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Message to {self.employee.full_name}: {self.subject}"


class UserPrivilege(models.Model):
    """
    Access control for custom admin pages.
    model_name can be empty to apply across all models.
    """
    ACCESS_READ_ONLY = "READ_ONLY"
    ACCESS_ADD_ONLY = "ADD_ONLY"
    ACCESS_EDIT_ONLY = "EDIT_ONLY"
    ACCESS_ALL = "ALL"
    ACCESS_FULL_ADMIN = "FULL_ADMIN"
    ACCESS_CHOICES = (
        (ACCESS_READ_ONLY, "Read Only"),
        (ACCESS_ADD_ONLY, "Add Only"),
        (ACCESS_EDIT_ONLY, "Edit Only"),
        (ACCESS_ALL, "All"),
        (ACCESS_FULL_ADMIN, "Full Admin"),
    )

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="user_privileges")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_privileges")
    model_name = models.CharField(max_length=100, blank=True, help_text="Leave blank for all models")
    access_level = models.CharField(max_length=20, choices=ACCESS_CHOICES, default=ACCESS_READ_ONLY)
    is_active = models.BooleanField(default=True)
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_privileges")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("entity", "user", "model_name")

    def __str__(self):
        target = self.model_name or "ALL_MODELS"
        return f"{self.user.username} {self.access_level} on {target}"


class Audit(models.Model):
    """
    Tracks actions performed on custom admin pages.
    """
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="audit_logs")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    page = models.CharField(max_length=100)
    action = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=100, blank=True)
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["entity", "created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["model_name", "created_at"]),
        ]

    def __str__(self):
        return f"{self.created_at} {self.action} by {self.user_id or 'N/A'}"
