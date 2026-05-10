import secrets

from django.apps import apps
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse

from .models import AppGlobalSettings, ManagerQRCodeToken


@admin.register(ManagerQRCodeToken)
class ManagerQRCodeTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "entity", "manager", "location", "action", "live_rotation_enabled", "expires_at", "used_at", "created_at")
    list_filter = ("entity", "action", "used_at", "created_at")
    search_fields = ("manager__full_name", "manager__employee_no", "location__name", "token")
    readonly_fields = ("created_at",)

    def get_fields(self, request, obj=None):
        base_fields = (
            "entity",
            "manager",
            "location",
            "action",
            "live_rotation_enabled",
            "live_rotation_interval_sec",
            "live_rotation_grace_steps",
            "expires_at",
            "used_at",
        )
        if obj is None:
            return base_fields
        return base_fields + ("token", "created_at")

    def get_readonly_fields(self, request, obj=None):
        fields = ["created_at"]
        if obj is not None:
            fields.append("token")
        return fields

    def save_model(self, request, obj, form, change):
        if not obj.token:
            obj.token = secrets.token_urlsafe(32)
        if obj.live_rotation_enabled and not obj.live_secret:
            obj.live_secret = secrets.token_hex(32)
        if not obj.live_rotation_enabled:
            obj.live_secret = ""
        super().save_model(request, obj, form, change)


@admin.register(AppGlobalSettings)
class AppGlobalSettingsAdmin(admin.ModelAdmin):
    list_display = ("store_review_mode", "store_review_user_id", "updated_at")
    readonly_fields = ("updated_at",)

    def changelist_view(self, request, extra_context=None):
        # Singleton: ensure row exists, then open the edit screen (empty changelist confuses admins).
        obj = AppGlobalSettings.get_solo()
        return redirect(
            reverse(
                f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                args=[obj.pk],
            )
        )

    def has_module_permission(self, request):
        return bool(request.user and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_add_permission(self, request):
        # Single row is created via get_solo(); hide redundant Add.
        return False

    def has_change_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            raise PermissionDenied("Only Django superusers can change app global settings.")
        super().save_model(request, obj, form, change)


for model in apps.get_app_config("basmaapp").get_models():
    if model in (ManagerQRCodeToken, AppGlobalSettings):
        continue
    try:
        admin.site.register(model)
    except admin.sites.AlreadyRegistered:
        pass
