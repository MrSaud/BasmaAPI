from django.shortcuts import render, redirect
from django.shortcuts import get_object_or_404

# Create your views here.
from django.http import JsonResponse, Http404
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.contrib import admin
from django.apps import apps
from django.urls.exceptions import NoReverseMatch
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.admin.views.decorators import staff_member_required
from django.forms import modelform_factory, ModelChoiceField
from django import forms
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
import json
import uuid
from .models import Employee, EmployeeLocationAssignment, Entity, EntitySettings, InboxMessage, Location, MobileActivationRequest
from .serializers import UpdateEmployeeUUIDSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.db.models import Count, Q, Max, Min, F, Exists, OuterRef, Case, When, Value, BooleanField
from django.db.models.functions import TruncDate, ExtractHour
from django.db import transaction
from .models import AttendanceTransaction, Audit, ManagerQRCodeToken, UserPrivilege  # Import here to avoid circular import
import csv
import re
import secrets
import hmac
import hashlib
import math
from urllib.parse import urlencode, urlparse, parse_qs, unquote
import base64
from datetime import date, timedelta
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import utils
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet
from .face_detection import run_face_compare, run_liveness_check


MANAGER_QR_LIVE_PREFIX = "mgrlive"


def check_liveness_with_api(photo_base64):
    biometric_error = ""

    if not photo_base64:
        biometric_error = "liveness: photo_base64 missing"
        return False, biometric_error

    result = run_liveness_check(
        image_base64=photo_base64,
        threshold=0.60,
        min_size=60,
        min_neighbors=5,
        pad=0.15,
    )
    if not result.get("ok", False):
        biometric_error = result.get("error") or "liveness: check failed"
        return False, biometric_error

    if result.get("is_live") is True:
        return True, ""

    live_score = result.get("live_score")
    if live_score is not None:
        biometric_error = f"liveness: failed (score {live_score})"
    else:
        biometric_error = "liveness: failed"
    return False, biometric_error



def compare_faces_with_api(photo_base64, employee_photo_base64, use_liveness=True, use_compare=True):
    biometric_verify = "FAILED"
    biometric_method = "FACE_COMPARE_API"
    biometric_error = ""

    if not photo_base64:
        biometric_error = "compare: photo_base64 missing"
        return biometric_verify, biometric_method, biometric_error

    if use_liveness:
        is_live, liveness_error = check_liveness_with_api(photo_base64)
        if not is_live:
            return biometric_verify, biometric_method, liveness_error

    if use_compare:
        if not employee_photo_base64:
            biometric_error = "compare: employee photo_base64 missing"
            return biometric_verify, biometric_method, biometric_error
        result = run_face_compare(
            image1_base64=photo_base64,
            image2_base64=employee_photo_base64,
            threshold=0.35,
            min_size=60,
            min_neighbors=5,
            pad=0.15,
        )
        if not result.get("ok", False):
            biometric_error = result.get("error") or "compare: failed"
            return biometric_verify, biometric_method, biometric_error

        if result.get("is_match") is True:
            biometric_verify = "PASSED"
            biometric_error = ""
        else:
            similarity = result.get("similarity")
            if similarity is not None:
                biometric_error = f"compare: mismatch (similarity {similarity})"
            else:
                biometric_error = "compare: mismatch"
    else:
        biometric_verify = "PASSED"
        biometric_error = ""
        biometric_method = "FACE_LIVENESS_ONLY" if use_liveness else "FACE_CHECK_DISABLED"

    return biometric_verify, biometric_method, biometric_error

def basma(request):
    return JsonResponse({"status": "ok", "app": "AttendanceSaaS"})


def _get_footer_entity_name(request):
    if not request.user.is_authenticated:
        return "AttendanceSaaS"
    employee_profile = (
        Employee.objects.filter(user=request.user, is_active=True)
        .select_related("entity")
        .first()
    )
    if employee_profile and employee_profile.entity:
        return employee_profile.entity.name
    return "AttendanceSaaS"


def _get_footer_user_name(request):
    if not request.user.is_authenticated:
        return "Guest"
    full_name = request.user.get_full_name().strip()
    return full_name or request.user.username


def _get_ui_language(request):
    lang = (request.session.get("ui_lang") or "en").lower()
    return lang if lang in {"en", "ar", "es"} else "en"


def _set_admin_selected_entity_id(request, entity_id):
    request.session["admin_selected_entity_id"] = int(entity_id)
    request.session.modified = True


@staff_member_required(login_url="/admin-login/")
def set_admin_entity_view(request):
    if not request.user.is_superuser:
        return redirect("admin_home")
    entity_id_raw = (request.POST.get("entity_id") or request.GET.get("entity_id") or "").strip()
    next_url = request.POST.get("next") or request.GET.get("next") or reverse("admin_home")
    if entity_id_raw.isdigit():
        selected = Entity.objects.filter(pk=int(entity_id_raw)).first()
        if selected:
            _set_admin_selected_entity_id(request, selected.pk)
    return redirect(next_url)


def set_ui_language(request):
    lang = (request.GET.get("lang") or "").lower()
    next_url = request.GET.get("next") or reverse("admin_home")
    if lang in {"en", "ar", "es"}:
        request.session["ui_lang"] = lang
    return HttpResponseRedirect(next_url)


def _get_model_card_description(model_name, lang="en"):
    descriptions = {
        "en": {
            "entity": "Manage company or tenant records and activation status.",
            "entitysettings": "Configure entity branding and operational settings.",
            "employee": "Manage employee profiles, identity, and work metadata.",
            "location": "Manage work locations, GPS or beacon setup, and status.",
            "employeelocationassignment": "Assign employees to locations and sign permissions.",
            "attendancetransaction": "Review attendance events, actions, and biometric outcomes.",
            "inboxmessage": "Manage employee inbox announcements and read status.",
            "userprivilege": "Assign custom admin access levels per user and model.",
            "audit": "Review tracked actions performed in custom admin pages.",
        },
        "ar": {
            "entity": "إدارة بيانات الشركة أو الجهة وحالة التفعيل.",
            "entitysettings": "ضبط هوية الجهة والإعدادات التشغيلية.",
            "employee": "إدارة ملفات الموظفين والهوية وبيانات العمل.",
            "location": "إدارة مواقع العمل وإعدادات GPS أو Beacon والحالة.",
            "employeelocationassignment": "تعيين الموظفين على المواقع وصلاحيات التسجيل.",
            "attendancetransaction": "مراجعة أحداث الحضور والإجراءات ونتائج التحقق الحيوي.",
            "inboxmessage": "إدارة رسائل صندوق الوارد للموظفين وحالة القراءة.",
            "userprivilege": "تحديد صلاحيات وصول الإدارة المخصصة حسب المستخدم والنموذج.",
            "audit": "مراجعة السجل الرقابي للإجراءات في صفحات الإدارة المخصصة.",
        },
        "es": {
            "entity": "Gestionar registros de empresa o entidad y estado de activación.",
            "entitysettings": "Configurar marca de la entidad y ajustes operativos.",
            "employee": "Gestionar perfiles de empleados, identidad y datos laborales.",
            "location": "Gestionar ubicaciones de trabajo, configuración GPS o beacon y estado.",
            "employeelocationassignment": "Asignar empleados a ubicaciones y permisos de marcación.",
            "attendancetransaction": "Revisar eventos de asistencia, acciones y resultados biométricos.",
            "inboxmessage": "Gestionar mensajes de bandeja de entrada y estado de lectura.",
            "userprivilege": "Asignar niveles de acceso del panel por usuario y modelo.",
            "audit": "Revisar acciones registradas en las páginas de administración personalizadas.",
        },
    }
    localized = descriptions.get(lang, descriptions["en"])
    return localized.get(model_name, {
        "en": "Manage records for this data section.",
        "ar": "إدارة سجلات هذا القسم.",
        "es": "Gestionar registros de esta sección.",
    }.get(lang, "Manage records for this data section."))


def _get_model_card_title(model_name, fallback_name, lang="en"):
    titles = {
        "en": {
            "entity": "Entities",
            "entitysettings": "Entity Settings",
            "employee": "Employees",
            "location": "Locations",
            "employeelocationassignment": "Employee Location Assignments",
            "attendancetransaction": "Attendance Transactions",
            "inboxmessage": "Inbox Messages",
            "userprivilege": "User Privileges",
            "audit": "Audit Logs",
        },
        "ar": {
            "entity": "الجهات",
            "entitysettings": "إعدادات الجهة",
            "employee": "الموظفون",
            "location": "المواقع",
            "employeelocationassignment": "تعيينات الموظفين على المواقع",
            "attendancetransaction": "عمليات الحضور",
            "inboxmessage": "رسائل صندوق الوارد",
            "userprivilege": "صلاحيات المستخدمين",
            "audit": "سجلات التدقيق",
        },
        "es": {
            "entity": "Entidades",
            "entitysettings": "Configuración de entidad",
            "employee": "Empleados",
            "location": "Ubicaciones",
            "employeelocationassignment": "Asignaciones empleado-ubicación",
            "attendancetransaction": "Transacciones de asistencia",
            "inboxmessage": "Mensajes de bandeja",
            "userprivilege": "Privilegios de usuario",
            "audit": "Registros de auditoría",
        },
    }
    localized = titles.get(lang, titles["en"])
    return localized.get(model_name, fallback_name)


def _get_model_group(model_name, lang="en"):
    group_labels = {
        "en": {
            "org": "Organization",
            "workforce": "Workforce",
            "attendance": "Attendance",
            "security": "Security & Audit",
            "messaging": "Messaging",
            "other": "Other",
        },
        "ar": {
            "org": "الجهة",
            "workforce": "القوى العاملة",
            "attendance": "الحضور",
            "security": "الأمان والتدقيق",
            "messaging": "الرسائل",
            "other": "أخرى",
        },
        "es": {
            "org": "Organización",
            "workforce": "Personal",
            "attendance": "Asistencia",
            "security": "Seguridad y auditoría",
            "messaging": "Mensajería",
            "other": "Otros",
        },
    }
    group_by_model = {
        "entity": "org",
        "entitysettings": "org",
        "employee": "workforce",
        "location": "workforce",
        "employeelocationassignment": "workforce",
        "managerqrcodetoken": "attendance",
        "attendancetransaction": "attendance",
        "inboxmessage": "messaging",
        "userprivilege": "security",
        "audit": "security",
    }
    group_key = group_by_model.get(model_name, "other")
    localized = group_labels.get(lang, group_labels["en"])
    return {
        "key": group_key,
        "title": localized.get(group_key, localized.get("other", "Other")),
    }


def _get_entity_logo_src(entity):
    if not entity:
        return ""
    settings_obj = EntitySettings.objects.filter(entity=entity).only("logo64").first()
    if not settings_obj or not settings_obj.logo64:
        return ""
    raw = str(settings_obj.logo64).strip()
    if not raw:
        return ""
    if raw.startswith("data:image/"):
        return raw
    return f"data:image/png;base64,{raw}"


def _is_entity_license_expired(entity):
    if not entity or not entity.license_expire_date:
        return True
    if not isinstance(entity.license_expire_date, date):
        return True
    return entity.license_expire_date < timezone.localdate()


def _get_entity_license_error_message(entity):
    if not entity or not entity.license_expire_date or not isinstance(entity.license_expire_date, date):
        return "Entity license date is missing or invalid."
    if entity.license_expire_date < timezone.localdate():
        return f"Entity license expired on {entity.license_expire_date.strftime('%Y-%m-%d')}."
    return ""


def _get_entity_license_notice(entity):
    if not entity or not entity.license_expire_date or not isinstance(entity.license_expire_date, date):
        return {"kind": "invalid", "message": "Entity license date is missing or invalid."}
    days_left = (entity.license_expire_date - timezone.localdate()).days
    if days_left < 0:
        return {
            "kind": "expired",
            "message": f"Entity license expired on {entity.license_expire_date.strftime('%Y-%m-%d')}.",
        }
    if days_left <= 60:
        return {
            "kind": "expiring",
            "date": entity.license_expire_date.strftime("%Y-%m-%d"),
            "days_left": days_left,
        }
    return None


def _sync_entity_active_by_license(entity):
    if not entity:
        return
    expired = _is_entity_license_expired(entity)
    if expired and entity.is_active:
        entity.is_active = False
        entity.save(update_fields=["is_active"])


def _get_employee_capacity_state(entity):
    settings_obj = EntitySettings.objects.filter(entity=entity).only("number_employees").first()
    max_employees = settings_obj.number_employees if settings_obj else None
    current_employees = Employee.objects.filter(entity=entity).count()
    at_limit = max_employees is not None and current_employees >= max_employees
    return {
        "max": max_employees,
        "current": current_employees,
        "at_limit": at_limit,
    }


def _bool_from_csv(value, default=False):
    raw = str(value or "").strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _number_from_csv(value, cast_type=float):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return cast_type(raw)
    except Exception:
        return None


def _generate_unique_username(base_value):
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", str(base_value or "").strip().lower()).strip("_")
    if not base:
        base = "employee"
    candidate = base
    counter = 1
    while User.objects.filter(username=candidate).exists():
        counter += 1
        candidate = f"{base}_{counter}"
    return candidate


def _build_employee_username(entity, employee_no):
    entity_code = re.sub(r"[^a-zA-Z0-9_]+", "_", str(getattr(entity, "code", "") or "").strip().lower()).strip("_")
    employee_code = re.sub(r"[^a-zA-Z0-9_]+", "_", str(employee_no or "").strip().lower()).strip("_")
    if not entity_code:
        entity_code = "entity"
    base = f"{entity_code}_{employee_code}" if employee_code else f"{entity_code}_employee"
    return _generate_unique_username(base)


def _employee_default_password(employee_no):
    return str(employee_no or "").strip() or "12345678"


def _split_full_name(full_name):
    raw = str(full_name or "").strip()
    if not raw:
        return "", ""
    parts = raw.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _extract_audit_changes(details):
    raw = (details or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    changes = payload.get("changes")
    if not isinstance(changes, list):
        return []
    normalized = []
    for item in changes:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field", "")).strip()
        if not field:
            continue
        normalized.append(
            {
                "field": field,
                "old": str(item.get("old", "-")),
                "new": str(item.get("new", "-")),
            }
        )
    return normalized


def _build_realtime_alerts(entity):
    alerts = []
    now = timezone.now()
    since_24h = now - timedelta(hours=24)
    tx_24h = AttendanceTransaction.objects.filter(entity=entity, occurred_at__gte=since_24h)

    repeated_failures = list(
        tx_24h.filter(biometric_verify="FAILED")
        .values("employee__full_name")
        .annotate(total=Count("id"))
        .filter(total__gte=5)
        .order_by("-total")[:5]
    )
    if repeated_failures:
        top_text = ", ".join(
            f"{(item.get('employee__full_name') or '-')}: {item.get('total', 0)}"
            for item in repeated_failures
        )
        alerts.append(
            {
                "severity": "high",
                "title": "Repeated biometric failures (24h)",
                "message": top_text,
            }
        )

    liveness_fail_count = tx_24h.filter(biometric_error__istartswith="liveness:").count()
    if liveness_fail_count >= 10:
        alerts.append(
            {
                "severity": "high",
                "title": "High liveness failures (24h)",
                "message": f"{liveness_fail_count} liveness failures detected in the last 24 hours.",
            }
        )

    valid_assignment = EmployeeLocationAssignment.objects.filter(
        entity=entity,
        employee_id=OuterRef("employee_id"),
        location_id=OuterRef("location_id"),
        is_active=True,
    ).filter(
        Q(start_date__isnull=True) | Q(start_date__lte=OuterRef("txn_date"))
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=OuterRef("txn_date"))
    )
    outside_assigned_count = (
        tx_24h.exclude(location_id__isnull=True)
        .annotate(txn_date=TruncDate("occurred_at"))
        .annotate(is_assigned=Exists(valid_assignment))
        .filter(is_assigned=False)
        .count()
    )
    if outside_assigned_count > 0:
        alerts.append(
            {
                "severity": "medium",
                "title": "Outside assigned location (24h)",
                "message": f"{outside_assigned_count} transactions were outside assigned locations.",
            }
        )

    capacity = _get_employee_capacity_state(entity)
    if capacity["max"]:
        usage_pct = round((capacity["current"] * 100.0) / capacity["max"], 1)
        if usage_pct >= 90:
            alerts.append(
                {
                    "severity": "medium" if usage_pct < 100 else "high",
                    "title": "Employee capacity usage",
                    "message": f"{capacity['current']} / {capacity['max']} ({usage_pct}%).",
                }
            )

    license_notice = _get_entity_license_notice(entity)
    if license_notice:
        alerts.append(
            {
                "severity": "high" if license_notice.get("kind") in {"invalid", "expired"} else "medium",
                "title": "License notice",
                "message": license_notice.get("message")
                or f"License expires on {license_notice.get('date')} ({license_notice.get('days_left')} days left).",
            }
        )

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda item: severity_rank.get(item.get("severity"), 9))
    return alerts


def _pending_activation_requests_count(entity):
    if not entity:
        return 0
    return MobileActivationRequest.objects.filter(
        entity=entity,
        status=MobileActivationRequest.STATUS_PENDING,
    ).count()


def _querystring_without_page(request):
    params = request.GET.copy()
    for key in ("page", "preset_action"):
        if key in params:
            params.pop(key)
    return params.urlencode()


def root_redirect_view(request):
    return redirect("admin_login")


def _save_filter_preset(request, key, allowed_keys):
    payload = {}
    for item in allowed_keys:
        value = (request.GET.get(item) or "").strip()
        if value:
            payload[item] = value
    request.session[key] = payload
    request.session.modified = True


def _load_filter_preset(request, key):
    payload = request.session.get(key)
    return payload if isinstance(payload, dict) else {}


def _clear_filter_preset(request, key):
    if key in request.session:
        request.session.pop(key, None)
        request.session.modified = True


def _safe_audit_log(request, entity, page, action, model_name="", object_id="", details=""):
    try:
        if not entity:
            return
        Audit.objects.create(
            entity=entity,
            user=request.user if request.user.is_authenticated else None,
            page=page,
            action=action,
            model_name=model_name,
            object_id=str(object_id or ""),
            details=details or "",
        )
    except Exception:
        # Never break business flow because of audit logging failure.
        pass


def _has_access_level_for_action(access_level, action):
    if action == "read":
        return access_level in ("READ_ONLY", "ADD_ONLY", "EDIT_ONLY", "ALL", "FULL_ADMIN")
    if action == "add":
        return access_level in ("ADD_ONLY", "ALL", "FULL_ADMIN")
    if action == "edit":
        return access_level in ("EDIT_ONLY", "ALL", "FULL_ADMIN")
    return False


def _is_full_admin_for_model(user, entity, model_name):
    if user.is_superuser:
        return True
    return UserPrivilege.objects.filter(
        entity=entity,
        user=user,
        is_active=True,
        access_level="FULL_ADMIN",
    ).filter(Q(model_name=model_name) | Q(model_name="")).exists()


def _check_model_privilege(request, entity, model_name, action):
    if request.user.is_superuser:
        return True

    # Entity creation is restricted to super admin only.
    if model_name == "entity" and action == "add":
        return False
    # Entity settings creation is restricted to super admin only.
    if model_name == "entitysettings" and action == "add":
        return False

    # UserPrivilege matrix is visible to staff users; changes require FULL_ADMIN.
    if model_name == "userprivilege":
        if action == "read":
            return True
        return _is_full_admin_for_model(request.user, entity, "userprivilege")

    # Audit logs are readable by staff users; audit remains immutable.
    if model_name == "audit":
        if action in ("add", "edit"):
            return False
        if action == "read":
            return True
        return False

    user_privileges = UserPrivilege.objects.filter(
        entity=entity,
        user=request.user,
        is_active=True,
    )
    if not user_privileges.exists():
        return True

    def _norm_model_key(value):
        return "".join(ch for ch in (value or "").strip().lower() if ch.isalnum())

    target_key = _norm_model_key(model_name)
    scoped_privileges = []
    for privilege in user_privileges:
        row_key = _norm_model_key(privilege.model_name)
        if row_key in {"", target_key}:
            scoped_privileges.append(privilege)

    if not scoped_privileges:
        return False
    return any(_has_access_level_for_action(p.access_level, action) for p in scoped_privileges)


def _require_model_privilege_or_403(request, entity, model_name, action):
    if not _check_model_privilege(request, entity, model_name, action):
        _safe_audit_log(
            request,
            entity,
            page="permission_check",
            action="PERMISSION_DENIED",
            model_name=model_name,
            details=f"required_action={action}",
        )
        raise PermissionDenied(f"You do not have {action} permission for {model_name}.")


def admin_login_view(request):
    ui_lang = _get_ui_language(request)
    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        return redirect("admin_home")

    error_message = ""
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""

        user = authenticate(request, username=username, password=password)
        if user and (user.is_staff or user.is_superuser):
            login(request, user)
            employee_profile = (
                Employee.objects.filter(user=user, is_active=True)
                .select_related("entity")
                .first()
            )
            if employee_profile and employee_profile.entity:
                _sync_entity_active_by_license(employee_profile.entity)
                if _is_entity_license_expired(employee_profile.entity) and not user.is_superuser:
                    error_message = (
                        _get_entity_license_error_message(employee_profile.entity)
                        + " Access is allowed only for super admin."
                    )
                    return render(
                        request,
                        "basmaapp/admin_login.html",
                        {
                            "error_message": error_message,
                            "footer_entity_name": _get_footer_entity_name(request),
                            "footer_user_name": _get_footer_user_name(request),
                            "ui_lang": ui_lang,
                        },
                    )
            if employee_profile and employee_profile.entity:
                _safe_audit_log(
                    request,
                    employee_profile.entity,
                    page="admin_login",
                    action="LOGIN_SUCCESS",
                    details="Staff user logged in.",
                )
            return redirect("admin_home")
        error_message = "Invalid credentials or not an admin user."

    return render(
        request,
        "basmaapp/admin_login.html",
        {
            "error_message": error_message,
            "footer_entity_name": _get_footer_entity_name(request),
            "footer_user_name": _get_footer_user_name(request),
            "ui_lang": ui_lang,
        },
    )


@staff_member_required(login_url="/admin-login/")
def admin_logout_view(request):
    logout(request)
    return redirect("admin_login")


def custom_400(request, exception):
    return render(request, "400.html", status=400)


def custom_403(request, exception):
    return render(request, "403.html", status=403)


def custom_404(request, exception):
    return render(request, "404.html", status=404)


def custom_500(request):
    return render(request, "500.html", status=500)


@staff_member_required(login_url="/admin-login/")
def admin_home_view(request):
    entity = _get_staff_entity_or_403(request)
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    license_notice = _get_entity_license_notice(entity)
    realtime_alerts = _build_realtime_alerts(entity)
    pending_activation_requests_count = _pending_activation_requests_count(entity)
    _safe_audit_log(
        request,
        entity,
        page="admin_home",
        action="VIEW_PAGE",
        details="Opened admin home page.",
    )
    model_menu = []
    for model in apps.get_app_config("basmaapp").get_models():
        opts = model._meta
        can_read = _check_model_privilege(request, entity, opts.model_name, "read")
        can_add = _check_model_privilege(request, entity, opts.model_name, "add")
        can_edit = _check_model_privilege(request, entity, opts.model_name, "edit")
        if not (can_read or can_add or can_edit):
            continue
        changelist_url = None
        add_url = None
        if model in admin.site._registry:
            try:
                changelist_url = reverse(
                    f"admin:{opts.app_label}_{opts.model_name}_changelist"
                )
                add_url = reverse(f"admin:{opts.app_label}_{opts.model_name}_add")
            except NoReverseMatch:
                changelist_url = None
                add_url = None
        model_menu.append(
            {
                "name": _get_model_card_title(
                    opts.model_name,
                    opts.verbose_name_plural.title(),
                    ui_lang,
                ),
                "app_label": opts.app_label,
                "model_name": opts.model_name,
                "description": _get_model_card_description(opts.model_name, ui_lang),
                "manage_url": reverse("model_records", kwargs={"model_name": opts.model_name}),
                "changelist_url": changelist_url,
                "add_url": add_url,
                "registered": model in admin.site._registry,
                "can_read": can_read,
                "can_add": can_add,
                "can_edit": can_edit,
            }
        )

    model_menu.sort(key=lambda item: (item["app_label"], item["name"]))
    group_order = ["org", "workforce", "attendance", "messaging", "security", "other"]
    grouped_model_menu = []
    grouped_index = {}
    for item in model_menu:
        group_meta = _get_model_group(item["model_name"], ui_lang)
        item["group_key"] = group_meta["key"]
        item["group_title"] = group_meta["title"]
        if group_meta["key"] not in grouped_index:
            grouped_index[group_meta["key"]] = {
                "key": group_meta["key"],
                "title": group_meta["title"],
                "items": [],
            }
        grouped_index[group_meta["key"]]["items"].append(item)
    for key in group_order:
        if key in grouped_index and grouped_index[key]["items"]:
            grouped_model_menu.append(grouped_index[key])
    for key, group in grouped_index.items():
        if key not in group_order and group["items"]:
            grouped_model_menu.append(group)
    return render(
        request,
        "basmaapp/admin_home.html",
        {
            "model_menu": model_menu,
            "grouped_model_menu": grouped_model_menu,
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "show_django_admin_button": request.user.is_superuser,
            "can_view_exceptions": _check_model_privilege(request, entity, "attendancetransaction", "read"),
            "can_view_audit_insights": _check_model_privilege(request, entity, "audit", "read"),
            "can_manage_privileges": _check_model_privilege(request, entity, "userprivilege", "read"),
            "can_view_realtime_alerts": _check_model_privilege(request, entity, "attendancetransaction", "read"),
            "can_manage_activation_requests": _check_model_privilege(request, entity, "employee", "edit"),
            "can_import_data": (
                _check_model_privilege(request, entity, "employee", "add")
                or _check_model_privilege(request, entity, "location", "add")
            ),
            "can_manage_user_accounts": request.user.is_superuser,
            "license_notice": license_notice,
            "realtime_alert_count": len(realtime_alerts),
            "realtime_alert_preview": realtime_alerts[:3],
            "pending_activation_requests_count": pending_activation_requests_count,
            "all_entities": Entity.objects.order_by("name", "id") if request.user.is_superuser else [],
            "selected_entity_id": entity.pk if request.user.is_superuser else None,
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
        },
    )


@staff_member_required(login_url="/admin-login/")
def admin_dashboard_view(request):
    entity = _get_staff_entity_or_403(request)
    _require_model_privilege_or_403(request, entity, "attendancetransaction", "read")
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    pending_activation_requests_count = _pending_activation_requests_count(entity)
    can_manage_activation_requests = _check_model_privilege(request, entity, "employee", "edit")
    dashboard_filter_keys = ("start_date", "end_date", "employee_name", "employee_no", "civil_id")
    preset_action = (request.GET.get("preset_action") or "").strip().lower()
    if preset_action == "save":
        _save_filter_preset(request, "dashboard_filters", dashboard_filter_keys)
        return redirect(f"{reverse('admin_dashboard')}?{_querystring_without_page(request)}")
    if preset_action == "clear":
        _clear_filter_preset(request, "dashboard_filters")
        return redirect(reverse("admin_dashboard"))
    if preset_action == "load":
        preset_filters = _load_filter_preset(request, "dashboard_filters")
        if preset_filters:
            q = urlencode(preset_filters)
            return redirect(f"{reverse('admin_dashboard')}?{q}")

    _safe_audit_log(
        request,
        entity,
        page="admin_dashboard",
        action="VIEW_PAGE",
        details=f"start_date={request.GET.get('start_date','')}, end_date={request.GET.get('end_date','')}",
    )
    transactions = AttendanceTransaction.objects.filter(entity=entity)

    start_date_raw = (request.GET.get("start_date") or "").strip()
    end_date_raw = (request.GET.get("end_date") or "").strip()
    employee_name_filter = (request.GET.get("employee_name") or "").strip()
    employee_no_filter = (request.GET.get("employee_no") or "").strip()
    civil_id_filter = (request.GET.get("civil_id") or "").strip()
    start_date = parse_date(start_date_raw) if start_date_raw else None
    end_date = parse_date(end_date_raw) if end_date_raw else None

    if start_date:
        transactions = transactions.filter(occurred_at__date__gte=start_date)
    if end_date:
        transactions = transactions.filter(occurred_at__date__lte=end_date)

    today = timezone.localdate()
    today_transactions = transactions.filter(occurred_at__date=today)

    action_counts = {
        "SIGN_IN": transactions.filter(action="SIGN_IN").count(),
        "SIGN_CONFIRM": transactions.filter(action="SIGN_CONFIRM").count(),
        "SIGN_OUT": transactions.filter(action="SIGN_OUT").count(),
    }

    fail_counts = {
        "total": transactions.filter(biometric_verify="FAILED").count(),
        "SIGN_IN": transactions.filter(action="SIGN_IN", biometric_verify="FAILED").count(),
        "SIGN_CONFIRM": transactions.filter(action="SIGN_CONFIRM", biometric_verify="FAILED").count(),
        "SIGN_OUT": transactions.filter(action="SIGN_OUT", biometric_verify="FAILED").count(),
    }

    top_failed_employees = list(
        transactions.filter(biometric_verify="FAILED")
        .values(
            "employee_id",
            "employee__full_name",
            "employee__employee_no",
            "employee__civil_id",
        )
        .annotate(
            failed_count=Count("id"),
            last_failed_at=Max("occurred_at"),
        )
        .order_by("-failed_count", "-last_failed_at")[:50]
    )
    # Working-hours summary from actual first SIGN_IN and last SIGN_OUT per employee/day.
    transactions_for_hours = transactions
    if employee_name_filter:
        transactions_for_hours = transactions_for_hours.filter(employee__full_name__icontains=employee_name_filter)
    if employee_no_filter:
        transactions_for_hours = transactions_for_hours.filter(employee__employee_no__icontains=employee_no_filter)
    if civil_id_filter:
        transactions_for_hours = transactions_for_hours.filter(employee__civil_id__icontains=civil_id_filter)
    daily_attendance = list(
        transactions_for_hours.annotate(txn_date=TruncDate("occurred_at"))
        .values("employee_id", "employee__full_name", "employee__employee_no", "txn_date")
        .annotate(
            first_sign_in=Min("occurred_at", filter=Q(action="SIGN_IN")),
            last_sign_out=Max("occurred_at", filter=Q(action="SIGN_OUT")),
        )
        .order_by("employee_id", "txn_date")
    )
    expected_hours_map = {
        item["id"]: float(item["number_working_hours_per_day"] or 0)
        for item in Employee.objects.filter(entity=entity).values("id", "number_working_hours_per_day")
    }
    employee_hours_map = {}
    for item in daily_attendance:
        employee_id = item.get("employee_id")
        if not employee_id:
            continue
        row = employee_hours_map.get(employee_id)
        if row is None:
            expected_per_day = float(expected_hours_map.get(employee_id) or 0.0)
            row = {
                "employee_id": employee_id,
                "employee_name": item.get("employee__full_name") or "-",
                "employee_no": item.get("employee__employee_no") or "-",
                "days_count": 0,
                "expected_hours": 0.0,
                "actual_hours": 0.0,
                "expected_per_day": expected_per_day,
            }
            employee_hours_map[employee_id] = row
        first_sign_in = item.get("first_sign_in")
        last_sign_out = item.get("last_sign_out")
        if not first_sign_in or not last_sign_out:
            continue
        actual_hours = (last_sign_out - first_sign_in).total_seconds() / 3600.0
        if actual_hours < 0:
            continue
        row["days_count"] += 1
        row["actual_hours"] += actual_hours
        row["expected_hours"] += row["expected_per_day"]
    employee_working_hours = []
    for item in employee_hours_map.values():
        expected = round(item["expected_hours"], 2)
        actual = round(item["actual_hours"], 2)
        variance = round(actual - expected, 2)
        compliance_pct = round((actual * 100.0 / expected), 1) if expected > 0 else 0.0
        employee_working_hours.append(
            {
                "employee_id": item["employee_id"],
                "employee_name": item["employee_name"],
                "employee_no": item["employee_no"],
                "days_count": item["days_count"],
                "expected_hours": expected,
                "actual_hours": actual,
                "variance_hours": variance,
                "compliance_pct": compliance_pct,
            }
        )
    employee_working_hours.sort(key=lambda x: (x["compliance_pct"], x["actual_hours"]))
    employee_working_hours = employee_working_hours[:100]
    working_hours_totals = {
        "employees": len(employee_working_hours),
        "expected_hours": round(sum(item["expected_hours"] for item in employee_working_hours), 2),
        "actual_hours": round(sum(item["actual_hours"] for item in employee_working_hours), 2),
    }
    working_hours_totals["variance_hours"] = round(
        working_hours_totals["actual_hours"] - working_hours_totals["expected_hours"], 2
    )

    location_totals = {
        "total": Location.objects.filter(entity=entity).count(),
        "active": Location.objects.filter(entity=entity, is_active=True).count(),
        "inactive": Location.objects.filter(entity=entity, is_active=False).count(),
    }

    location_stats = (
        Location.objects.filter(entity=entity)
        .annotate(
            total_transactions=Count(
                "attendancetransaction",
                filter=Q(attendancetransaction__in=transactions),
            ),
            failed_transactions=Count(
                "attendancetransaction",
                filter=Q(
                    attendancetransaction__in=transactions,
                    attendancetransaction__biometric_verify="FAILED",
                ),
            ),
        )
        .order_by("-total_transactions", "name")
    )
    location_stats_list = list(location_stats)
    top_location_stats = location_stats_list[:10]
    employees = Employee.objects.filter(entity=entity)

    duplicate_civil_id_count = (
        employees.exclude(civil_id__isnull=True)
        .exclude(civil_id__exact="")
        .values("civil_id")
        .annotate(c=Count("id"))
        .filter(c__gt=1)
        .count()
    )
    duplicate_employee_no_count = (
        employees.exclude(employee_no__isnull=True)
        .exclude(employee_no__exact="")
        .values("employee_no")
        .annotate(c=Count("id"))
        .filter(c__gt=1)
        .count()
    )
    data_quality = {
        "missing_employee_photo": employees.filter(Q(photo_base64__isnull=True) | Q(photo_base64__exact="")).count(),
        "duplicate_civil_id_groups": duplicate_civil_id_count,
        "duplicate_employee_no_groups": duplicate_employee_no_count,
        "gps_location_missing_coordinates": Location.objects.filter(entity=entity, is_GPS_based=True).filter(
            Q(latitude__isnull=True) | Q(longitude__isnull=True)
        ).count(),
        "inactive_employee_with_active_assignment": EmployeeLocationAssignment.objects.filter(
            entity=entity,
            is_active=True,
            employee__is_active=False,
        ).count(),
    }
    face_api_qs = transactions.filter(biometric_method="FACE_COMPARE_API")
    liveness_fail_count = face_api_qs.filter(biometric_error__istartswith="liveness:").count()
    compare_fail_count = face_api_qs.filter(biometric_error__istartswith="compare:").count()
    api_health = {
        "total_face_api_checks": face_api_qs.count(),
        "face_api_passed": face_api_qs.filter(biometric_verify="PASSED").count(),
        "face_api_failed": face_api_qs.filter(biometric_verify="FAILED").count(),
        "liveness_failures": liveness_fail_count,
        "compare_failures": compare_fail_count,
        "last_face_api_failure_at": face_api_qs.filter(biometric_verify="FAILED").aggregate(Max("occurred_at")).get(
            "occurred_at__max"
        ),
    }
    total_api_checks = api_health["total_face_api_checks"] or 0
    if total_api_checks > 0:
        api_health["success_rate_pct"] = round((api_health["face_api_passed"] * 100.0) / total_api_checks, 1)
        api_health["error_rate_pct"] = round((api_health["face_api_failed"] * 100.0) / total_api_checks, 1)
    else:
        api_health["success_rate_pct"] = 0.0
        api_health["error_rate_pct"] = 0.0

    return render(
        request,
        "basmaapp/admin_dashboard.html",
        {
            "entity": entity,
            "total_transactions": transactions.count(),
            "today_transactions": today_transactions.count(),
            "action_counts": action_counts,
            "fail_counts": fail_counts,
            "location_totals": location_totals,
            "location_stats": location_stats_list,
            "start_date": start_date_raw,
            "end_date": end_date_raw,
            "footer_entity_name": entity.name,
            "action_chart_labels": ["SIGN_IN", "SIGN_CONFIRM", "SIGN_OUT"],
            "action_chart_values": [
                action_counts["SIGN_IN"],
                action_counts["SIGN_CONFIRM"],
                action_counts["SIGN_OUT"],
            ],
            "failure_chart_labels": ["SIGN_IN", "SIGN_CONFIRM", "SIGN_OUT"],
            "failure_chart_values": [
                fail_counts["SIGN_IN"],
                fail_counts["SIGN_CONFIRM"],
                fail_counts["SIGN_OUT"],
            ],
            "location_chart_labels": [item.name for item in top_location_stats],
            "location_chart_values": [item.total_transactions for item in top_location_stats],
            "saved_filters_available": bool(_load_filter_preset(request, "dashboard_filters")),
            "employee_name_filter": employee_name_filter,
            "employee_no_filter": employee_no_filter,
            "civil_id_filter": civil_id_filter,
            "data_quality": data_quality,
            "api_health": api_health,
            "top_failed_employees": top_failed_employees,
            "employee_working_hours": employee_working_hours,
            "working_hours_totals": working_hours_totals,
            "footer_user_name": _get_footer_user_name(request),
            "show_django_admin_button": request.user.is_superuser,
            "can_view_exceptions": _check_model_privilege(request, entity, "attendancetransaction", "read"),
            "can_view_audit_insights": _check_model_privilege(request, entity, "audit", "read"),
            "can_manage_privileges": _check_model_privilege(request, entity, "userprivilege", "read"),
            "can_manage_activation_requests": can_manage_activation_requests,
            "pending_activation_requests_count": pending_activation_requests_count,
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
        },
    )


def _get_privilege_model_choices():
    ordered = [
        "attendancetransaction",
        "audit",
        "employee",
        "employeelocationassignment",
        "entity",
        "entitysettings",
        "inboxmessage",
        "location",
        "userprivilege",
    ]
    existing = {model._meta.model_name for model in apps.get_app_config("basmaapp").get_models()}
    return [name for name in ordered if name in existing]


def _matrix_allowed_levels(row_key, actor_is_superuser):
    base_levels = ["NO_ACCESS", "READ_ONLY", "ADD_ONLY", "EDIT_ONLY", "ALL", "FULL_ADMIN"]
    if row_key in {"all_models", "audit"} and not actor_is_superuser:
        return ["NO_ACCESS"]
    if row_key == "entity" and not actor_is_superuser:
        # Entity add is super-admin only. Staff can still be read/edit scoped.
        return ["NO_ACCESS", "READ_ONLY", "EDIT_ONLY"]
    return base_levels


@staff_member_required(login_url="/admin-login/")
def privilege_matrix_view(request):
    entity = _get_staff_entity_or_403(request)
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    _require_model_privilege_or_403(request, entity, "userprivilege", "read")
    can_edit_privileges = _check_model_privilege(request, entity, "userprivilege", "edit")

    employees = (
        Employee.objects.filter(entity=entity, is_active=True, user__isnull=False)
        .select_related("user")
        .order_by("full_name")
    )
    users = [item.user for item in employees if item.user]
    selected_user_id_raw = (request.POST.get("target_user") or request.GET.get("user") or "").strip()
    selected_user = None
    if selected_user_id_raw.isdigit():
        selected_user = (
            User.objects.filter(
                pk=int(selected_user_id_raw),
                employee_profile__entity=entity,
                employee_profile__is_active=True,
            )
            .distinct()
            .first()
        )

    model_names = _get_privilege_model_choices()
    matrix_rows = []
    existing_map = {}
    if selected_user:
        existing_privileges = UserPrivilege.objects.filter(entity=entity, user=selected_user)
        existing_map = {item.model_name or "all_models": item for item in existing_privileges}
    delete_confirm_required = (request.GET.get("delete_confirm_required") or "").strip() == "1"

    if request.method == "POST" and selected_user:
        if not can_edit_privileges:
            raise PermissionDenied("You do not have edit permission for userprivilege.")
        delete_confirm_text = (request.POST.get("delete_confirm_text") or "").strip().lower()
        delete_requested = False
        for row_key in ["all_models"] + model_names:
            row_editable = not (row_key in {"all_models", "audit"} and not request.user.is_superuser)
            if not row_editable:
                continue
            existing = existing_map.get(row_key)
            if not existing:
                continue
            posted_level = (request.POST.get(f"access_{row_key}") or "NO_ACCESS").strip().upper()
            posted_active = request.POST.get(f"active_{row_key}") == "on"
            if posted_level == "NO_ACCESS" or not posted_active:
                delete_requested = True
                break
        if delete_requested and delete_confirm_text != "confirm":
            _safe_audit_log(
                request,
                entity,
                page="privilege_matrix",
                action="DELETE_CONFIRM_REQUIRED",
                model_name="userprivilege",
                object_id=selected_user.pk,
                details=f"user={selected_user.username}",
            )
            return redirect(f"{reverse('privilege_matrix')}?user={selected_user.pk}&delete_confirm_required=1")
        for row_key in ["all_models"] + model_names:
            allowed_levels = _matrix_allowed_levels(row_key, request.user.is_superuser)
            row_editable = not (row_key in {"all_models", "audit"} and not request.user.is_superuser)
            if not row_editable:
                continue
            posted_level = (request.POST.get(f"access_{row_key}") or "NO_ACCESS").strip().upper()
            posted_active = request.POST.get(f"active_{row_key}") == "on"
            model_name = "" if row_key == "all_models" else row_key
            existing = UserPrivilege.objects.filter(
                entity=entity,
                user=selected_user,
                model_name=model_name,
            ).first()

            if posted_level == "NO_ACCESS" or not posted_active:
                if existing:
                    existing.delete()
                continue

            if posted_level not in set(allowed_levels) - {"NO_ACCESS"}:
                continue

            if existing:
                existing.access_level = posted_level
                existing.is_active = True
                existing.assigned_by = request.user
                existing.save(update_fields=["access_level", "is_active", "assigned_by", "updated_at"])
            else:
                UserPrivilege.objects.create(
                    entity=entity,
                    user=selected_user,
                    model_name=model_name,
                    access_level=posted_level,
                    is_active=True,
                    assigned_by=request.user,
                )

        _safe_audit_log(
            request,
            entity,
            page="privilege_matrix",
            action="UPDATE_MATRIX",
            model_name="userprivilege",
            object_id=selected_user.pk,
            details=f"user={selected_user.username}",
        )
        return redirect(f"{reverse('privilege_matrix')}?user={selected_user.pk}")

    def _display_name(model_name):
        if model_name == "all_models":
            return "ALL_MODELS"
        if model_name == "audit":
            return "Audit Logs"
        model_obj = _get_basma_model_or_404(model_name)
        return _get_model_card_title(model_name, model_obj._meta.verbose_name_plural.title(), ui_lang)

    for row_key in ["all_models"] + model_names:
        existing = existing_map.get(row_key)
        allowed_levels = _matrix_allowed_levels(row_key, request.user.is_superuser)
        row_editable = can_edit_privileges and not (
            row_key in {"all_models", "audit"} and not request.user.is_superuser
        )
        matrix_rows.append(
            {
                "row_key": row_key,
                "model_name": row_key,
                "display_name": _display_name(row_key),
                "access_level": existing.access_level if existing else "NO_ACCESS",
                "is_active": existing.is_active if existing else False,
                "has_existing": bool(existing),
                "allowed_levels": allowed_levels,
                "row_editable": row_editable,
            }
        )

    _safe_audit_log(
        request,
        entity,
        page="privilege_matrix",
        action="VIEW_PAGE",
        model_name="userprivilege",
        details=f"selected_user={selected_user.pk if selected_user else ''}",
    )
    return render(
        request,
        "basmaapp/privilege_matrix.html",
        {
            "entity": entity,
            "users": users,
            "selected_user": selected_user,
            "matrix_rows": matrix_rows,
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "show_django_admin_button": request.user.is_superuser,
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
            "can_edit_privileges": can_edit_privileges,
            "delete_confirm_required": delete_confirm_required,
        },
    )


def _filter_audit_queryset(entity, request):
    logs = Audit.objects.filter(entity=entity).select_related("user").order_by("-created_at", "-id")
    user_id_raw = (request.GET.get("user_id") or "").strip()
    start_date_raw = (request.GET.get("start_date") or "").strip()
    end_date_raw = (request.GET.get("end_date") or "").strip()
    search_description = (request.GET.get("description") or "").strip()
    search_action = (request.GET.get("action") or "").strip()
    search_model = (request.GET.get("model_name") or "").strip()

    if user_id_raw.isdigit():
        logs = logs.filter(user_id=int(user_id_raw))
    start_date = parse_date(start_date_raw) if start_date_raw else None
    end_date = parse_date(end_date_raw) if end_date_raw else None
    if start_date:
        logs = logs.filter(created_at__date__gte=start_date)
    if end_date:
        logs = logs.filter(created_at__date__lte=end_date)
    if search_description:
        logs = logs.filter(details__icontains=search_description)
    if search_action:
        logs = logs.filter(action__icontains=search_action)
    if search_model:
        logs = logs.filter(model_name__icontains=search_model)

    return logs, {
        "user_id": user_id_raw,
        "start_date": start_date_raw,
        "end_date": end_date_raw,
        "description": search_description,
        "action": search_action,
        "model_name": search_model,
    }


@staff_member_required(login_url="/admin-login/")
def audit_insights_view(request):
    entity = _get_staff_entity_or_403(request)
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    _require_model_privilege_or_403(request, entity, "audit", "read")
    audit_filter_keys = ("user_id", "start_date", "end_date", "description", "action", "model_name")
    preset_action = (request.GET.get("preset_action") or "").strip().lower()
    if preset_action == "save":
        _save_filter_preset(request, "audit_insight_filters", audit_filter_keys)
        return redirect(f"{reverse('audit_insights')}?{_querystring_without_page(request)}")
    if preset_action == "clear":
        _clear_filter_preset(request, "audit_insight_filters")
        return redirect(reverse("audit_insights"))
    if preset_action == "load":
        preset_filters = _load_filter_preset(request, "audit_insight_filters")
        if preset_filters:
            q = urlencode(preset_filters)
            return redirect(f"{reverse('audit_insights')}?{q}")

    logs, filter_values = _filter_audit_queryset(entity, request)
    paginator = Paginator(logs, 100)
    page_obj = paginator.get_page(request.GET.get("page"))
    page_logs = list(page_obj.object_list)
    for item in page_logs:
        item.has_diff = bool(_extract_audit_changes(item.details))

    total_logs = logs.count()
    unique_users = logs.values("user_id").distinct().count()
    top_actions_qs = (
        logs.values("action")
        .annotate(total=Count("id"))
        .order_by("-total", "action")[:10]
    )
    top_models_qs = (
        logs.values("model_name")
        .annotate(total=Count("id"))
        .order_by("-total", "model_name")[:10]
    )
    denied_count = logs.filter(action__icontains="DENIED").count()

    _safe_audit_log(
        request,
        entity,
        page="audit_insights",
        action="VIEW_PAGE",
        model_name="audit",
        details=request.GET.urlencode(),
    )
    return render(
        request,
        "basmaapp/audit_insights.html",
        {
            "entity": entity,
            "page_obj": page_obj,
            "logs": page_logs,
            "total_logs": total_logs,
            "unique_users": unique_users,
            "denied_count": denied_count,
            "top_actions": list(top_actions_qs),
            "top_models": list(top_models_qs),
            "users": User.objects.filter(employee_profile__entity=entity).distinct().order_by("username"),
            "filter_values": filter_values,
            "filter_querystring": _querystring_without_page(request),
            "saved_filters_available": bool(_load_filter_preset(request, "audit_insight_filters")),
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "show_django_admin_button": request.user.is_superuser,
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
        },
    )


@staff_member_required(login_url="/admin-login/")
def audit_diff_view(request, audit_id):
    entity = _get_staff_entity_or_403(request)
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    _require_model_privilege_or_403(request, entity, "audit", "read")
    item = get_object_or_404(Audit.objects.filter(entity=entity), pk=audit_id)
    changes = _extract_audit_changes(item.details)
    _safe_audit_log(
        request,
        entity,
        page="audit_diff",
        action="VIEW_DIFF",
        model_name=item.model_name or "audit",
        object_id=item.object_id,
    )
    return render(
        request,
        "basmaapp/audit_diff.html",
        {
            "entity": entity,
            "audit_item": item,
            "changes": changes,
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
        },
    )


@staff_member_required(login_url="/admin-login/")
def audit_export_csv_view(request):
    entity = _get_staff_entity_or_403(request)
    _require_model_privilege_or_403(request, entity, "audit", "read")
    logs, _ = _filter_audit_queryset(entity, request)
    _safe_audit_log(
        request,
        entity,
        page="audit_insights",
        action="EXPORT_CSV",
        model_name="audit",
        details=request.GET.urlencode(),
    )
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="audit_logs.csv"'
    writer = csv.writer(response)
    writer.writerow(["ID", "Created At", "User", "Page", "Action", "Model", "Object ID", "Description"])
    for item in logs:
        writer.writerow(
            [
                item.pk,
                item.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                item.user.username if item.user else "-",
                item.page,
                item.action,
                item.model_name or "-",
                item.object_id or "-",
                item.details or "-",
            ]
        )
    return response


@staff_member_required(login_url="/admin-login/")
def audit_export_pdf_view(request):
    entity = _get_staff_entity_or_403(request)
    _require_model_privilege_or_403(request, entity, "audit", "read")
    logs, _ = _filter_audit_queryset(entity, request)
    generated_by = request.user.get_full_name().strip() or request.user.username
    _safe_audit_log(
        request,
        entity,
        page="audit_insights",
        action="EXPORT_PDF",
        model_name="audit",
        details=request.GET.urlencode(),
    )
    rows = []
    for item in logs[:1000]:
        rows.append(
            [
                str(item.pk),
                item.created_at.strftime("%Y-%m-%d %H:%M"),
                item.user.username if item.user else "-",
                item.page or "-",
                item.action or "-",
                item.model_name or "-",
                (item.details or "-")[:120],
            ]
        )
    return _build_pdf_response(
        "audit_logs_report.pdf",
        "Audit Logs Report",
        entity.name,
        generated_by,
        ["ID", "Created At", "User", "Page", "Action", "Model", "Description"],
        rows or [["-", "-", "-", "-", "-", "-", "No records"]],
        filter_lines=[f"{k}: {v}" for k, v in request.GET.items() if v],
        logo_src=_get_entity_logo_src(entity),
    )


def _get_attendance_exceptions(entity, request):
    transactions = AttendanceTransaction.objects.filter(entity=entity).select_related("employee", "location")
    start_date_raw = (request.GET.get("start_date") or "").strip()
    end_date_raw = (request.GET.get("end_date") or "").strip()
    failure_threshold_raw = (request.GET.get("failure_threshold") or "3").strip()
    late_after_hour_raw = (request.GET.get("late_after_hour") or "10").strip()
    employee_name_raw = (request.GET.get("employee_name") or "").strip()
    employee_no_raw = (request.GET.get("employee_no") or "").strip()
    civil_id_raw = (request.GET.get("civil_id") or "").strip()
    late_after_hour = int(late_after_hour_raw) if late_after_hour_raw.isdigit() else 10
    failure_threshold = int(failure_threshold_raw) if failure_threshold_raw.isdigit() else 3

    start_date = parse_date(start_date_raw) if start_date_raw else None
    end_date = parse_date(end_date_raw) if end_date_raw else None
    if start_date:
        transactions = transactions.filter(occurred_at__date__gte=start_date)
    if end_date:
        transactions = transactions.filter(occurred_at__date__lte=end_date)

    missing_sign_out = list(
        transactions.annotate(txn_date=TruncDate("occurred_at"))
        .values("employee_id", "employee__full_name", "txn_date")
        .annotate(
            sign_in_count=Count("id", filter=Q(action="SIGN_IN")),
            sign_out_count=Count("id", filter=Q(action="SIGN_OUT")),
        )
        .filter(sign_in_count__gt=F("sign_out_count"))
        .order_by("-txn_date", "employee__full_name")[:200]
    )

    late_sign_ins = list(
        transactions.filter(action="SIGN_IN")
        .annotate(hour=ExtractHour("occurred_at"), txn_date=TruncDate("occurred_at"))
        .filter(hour__gte=late_after_hour)
        .values("employee__full_name", "employee__employee_no", "txn_date")
        .annotate(count=Count("id"), last_at=Max("occurred_at"))
        .order_by("-txn_date", "-count")[:200]
    )

    valid_assignment = EmployeeLocationAssignment.objects.filter(
        entity=entity,
        employee_id=OuterRef("employee_id"),
        location_id=OuterRef("location_id"),
        is_active=True,
    ).filter(
        Q(start_date__isnull=True) | Q(start_date__lte=OuterRef("txn_date"))
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=OuterRef("txn_date"))
    )
    outside_assigned_location = list(
        transactions.exclude(location_id__isnull=True)
        .annotate(txn_date=TruncDate("occurred_at"))
        .annotate(is_assigned=Exists(valid_assignment))
        .filter(is_assigned=False)
        .values(
            "id",
            "occurred_at",
            "action",
            "employee__full_name",
            "employee__employee_no",
            "location__name",
        )
        .order_by("-occurred_at")[:200]
    )

    repeated_biometric_failures = list(
        transactions.filter(biometric_verify="FAILED")
        .values("employee_id", "employee__full_name", "employee__employee_no")
        .annotate(failed_count=Count("id"), last_failed_at=Max("occurred_at"))
        .filter(failed_count__gte=failure_threshold)
        .order_by("-failed_count", "-last_failed_at")[:200]
    )
    if employee_name_raw:
        repeated_biometric_failures = [
            item for item in repeated_biometric_failures
            if employee_name_raw.lower() in (item.get("employee__full_name") or "").lower()
        ]
    if employee_no_raw:
        repeated_biometric_failures = [
            item for item in repeated_biometric_failures
            if employee_no_raw.lower() in (item.get("employee__employee_no") or "").lower()
        ]
    if civil_id_raw:
        employee_ids = set(
            Employee.objects.filter(entity=entity, civil_id__icontains=civil_id_raw).values_list("id", flat=True)
        )
        repeated_biometric_failures = [
            item for item in repeated_biometric_failures
            if item.get("employee_id") in employee_ids
        ]

    return {
        "missing_sign_out": missing_sign_out,
        "late_sign_ins": late_sign_ins,
        "outside_assigned_location": outside_assigned_location,
        "repeated_biometric_failures": repeated_biometric_failures,
        "filter_values": {
            "start_date": start_date_raw,
            "end_date": end_date_raw,
            "failure_threshold": failure_threshold,
            "late_after_hour": late_after_hour,
            "employee_name": employee_name_raw,
            "employee_no": employee_no_raw,
            "civil_id": civil_id_raw,
        },
    }


@staff_member_required(login_url="/admin-login/")
def attendance_exceptions_view(request):
    entity = _get_staff_entity_or_403(request)
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    _require_model_privilege_or_403(request, entity, "attendancetransaction", "read")
    exception_filter_keys = (
        "start_date",
        "end_date",
        "failure_threshold",
        "late_after_hour",
        "employee_name",
        "employee_no",
        "civil_id",
    )
    preset_action = (request.GET.get("preset_action") or "").strip().lower()
    if preset_action == "save":
        _save_filter_preset(request, "attendance_exception_filters", exception_filter_keys)
        return redirect(f"{reverse('attendance_exceptions')}?{_querystring_without_page(request)}")
    if preset_action == "clear":
        _clear_filter_preset(request, "attendance_exception_filters")
        return redirect(reverse("attendance_exceptions"))
    if preset_action == "load":
        preset_filters = _load_filter_preset(request, "attendance_exception_filters")
        if preset_filters:
            q = urlencode(preset_filters)
            return redirect(f"{reverse('attendance_exceptions')}?{q}")
    exception_data = _get_attendance_exceptions(entity, request)
    _safe_audit_log(
        request,
        entity,
        page="attendance_exceptions",
        action="VIEW_PAGE",
        model_name="attendancetransaction",
        details=request.GET.urlencode(),
    )
    return render(
        request,
        "basmaapp/attendance_exceptions.html",
        {
            "entity": entity,
            "exceptions": exception_data,
            "saved_filters_available": bool(_load_filter_preset(request, "attendance_exception_filters")),
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "show_django_admin_button": request.user.is_superuser,
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
        },
    )


def _import_locations_from_csv(entity, rows):
    created = 0
    updated = 0
    errors = []
    for index, row in enumerate(rows, start=2):
        name = (row.get("name") or "").strip()
        if not name:
            errors.append(f"Row {index}: name is required.")
            continue
        defaults = {
            "description": (row.get("description") or "").strip(),
            "latitude": _number_from_csv(row.get("latitude"), float),
            "longitude": _number_from_csv(row.get("longitude"), float),
            "is_GPS_based": _bool_from_csv(row.get("is_gps_based"), True),
            "is_beacon_based": _bool_from_csv(row.get("is_beacon_based"), False),
            "major_value": _number_from_csv(row.get("major_value"), int),
            "minor_value": _number_from_csv(row.get("minor_value"), int),
            "rssi_threshold": _number_from_csv(row.get("rssi_threshold"), int),
            "beacon_uuid": (row.get("beacon_uuid") or "").strip(),
            "is_active": _bool_from_csv(row.get("is_active"), True),
        }
        obj, created_flag = Location.objects.update_or_create(
            entity=entity,
            name=name,
            defaults=defaults,
        )
        if created_flag:
            created += 1
        else:
            updated += 1
    return created, updated, errors


def _import_employees_from_csv(entity, rows):
    created = 0
    updated = 0
    errors = []
    for index, row in enumerate(rows, start=2):
        employee_no = (row.get("employee_no") or "").strip()
        full_name = (row.get("full_name") or "").strip()
        username = (row.get("username") or "").strip()
        if not employee_no:
            errors.append(f"Row {index}: employee_no is required.")
            continue
        if not full_name:
            errors.append(f"Row {index}: full_name is required.")
            continue

        employee = Employee.objects.filter(entity=entity, employee_no=employee_no).select_related("user").first()
        user = employee.user if employee else None
        if user is None:
            if not username:
                username = _generate_unique_username(f"{entity.code}_{employee_no}")
            if User.objects.filter(username=username, employee_profile__isnull=False).exists():
                errors.append(f"Row {index}: username '{username}' already linked to another employee.")
                continue
            user = User.objects.filter(username=username).first()
            if user is None:
                user = User(username=username)
            user.email = (row.get("email") or "").strip()
            user.first_name = (row.get("first_name") or "").strip()
            user.last_name = (row.get("last_name") or "").strip()
            password = (row.get("password") or "").strip()
            if password:
                user.set_password(password)
            elif not user.pk:
                user.set_unusable_password()
            user.save()

        defaults = {
            "full_name": full_name,
            "civil_id": (row.get("civil_id") or "").strip(),
            "phone_number": (row.get("phone_number") or "").strip(),
            "device_uuid": (row.get("device_uuid") or "").strip(),
            "photo_base64": (row.get("photo_base64") or "").strip(),
            "is_active": _bool_from_csv(row.get("is_active"), True),
            "is_manager": _bool_from_csv(row.get("is_manager"), False),
        }
        confirm_sign = _number_from_csv(row.get("confirm_sign_period_minutes"), int)
        work_hours = _number_from_csv(row.get("number_working_hours_per_day"), float)
        if confirm_sign is not None:
            defaults["confirm_sign_period_minutes"] = confirm_sign
        if work_hours is not None:
            defaults["number_working_hours_per_day"] = work_hours
        employee_uuid = (row.get("employee_uuid") or "").strip()
        if employee_uuid:
            defaults["employee_uuid"] = employee_uuid

        if employee:
            for key, value in defaults.items():
                setattr(employee, key, value)
            employee.user = user
            employee.save()
            updated += 1
        else:
            capacity = _get_employee_capacity_state(entity)
            if capacity["at_limit"]:
                errors.append(f"Row {index}: employee capacity reached ({capacity['current']} / {capacity['max']}).")
                continue
            Employee.objects.create(
                entity=entity,
                user=user,
                employee_no=employee_no,
                **defaults,
            )
            created += 1
    return created, updated, errors


@staff_member_required(login_url="/admin-login/")
def realtime_alerts_view(request):
    entity = _get_staff_entity_or_403(request)
    _require_model_privilege_or_403(request, entity, "attendancetransaction", "read")
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    alerts = _build_realtime_alerts(entity)
    _safe_audit_log(
        request,
        entity,
        page="realtime_alerts",
        action="VIEW_PAGE",
        model_name="attendancetransaction",
    )
    return render(
        request,
        "basmaapp/realtime_alerts.html",
        {
            "entity": entity,
            "alerts": alerts,
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
        },
    )


@staff_member_required(login_url="/admin-login/")
def activation_requests_view(request):
    entity = _get_staff_entity_or_403(request)
    _require_model_privilege_or_403(request, entity, "employee", "read")
    can_edit = _check_model_privilege(request, entity, "employee", "edit")
    can_decide = True
    is_superuser = bool(getattr(request.user, "is_superuser", False))
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    scope = (request.GET.get("scope") or "").strip().lower()
    status_filter = (request.GET.get("status") or "").strip().upper()
    if status_filter not in {
        MobileActivationRequest.STATUS_PENDING,
        MobileActivationRequest.STATUS_APPROVED,
        MobileActivationRequest.STATUS_REJECTED,
    }:
        status_filter = "ALL"
    is_all_scope = is_superuser and scope in {"all", "all_entities", "global", "1", "true", ""}

    if request.method == "POST":
        if not can_decide:
            raise PermissionDenied("No permission to update activation requests.")
        action = (request.POST.get("action") or "").strip().lower()
        req_id_raw = (request.POST.get("request_id") or "").strip()
        if req_id_raw.isdigit() and action in {"activate", "reject"}:
            req_qs = MobileActivationRequest.objects.select_related("employee", "entity").filter(pk=int(req_id_raw))
            if not is_superuser:
                req_qs = req_qs.filter(entity=entity)
            req_obj = req_qs.first()
            if req_obj and req_obj.status == MobileActivationRequest.STATUS_PENDING:
                now_dt = timezone.now()
                if action == "activate":
                    req_obj.employee.is_active = True
                    req_obj.employee.save(update_fields=["is_active"])
                    req_obj.status = MobileActivationRequest.STATUS_APPROVED
                    req_obj.decided_at = now_dt
                    req_obj.decided_by = request.user
                    req_obj.decision_note = "Approved by admin page"
                    req_obj.save(update_fields=["status", "decided_at", "decided_by", "decision_note", "updated_at"])
                    _safe_audit_log(
                        request,
                        req_obj.entity,
                        page="activation_requests",
                        action="APPROVE_ACTIVATION_REQUEST",
                        model_name="mobileactivationrequest",
                        object_id=req_obj.id,
                        details=f"employee_id={req_obj.employee_id}",
                    )
                else:
                    req_obj.status = MobileActivationRequest.STATUS_REJECTED
                    req_obj.decided_at = now_dt
                    req_obj.decided_by = request.user
                    req_obj.decision_note = "Rejected by admin page"
                    req_obj.save(update_fields=["status", "decided_at", "decided_by", "decision_note", "updated_at"])
                    _safe_audit_log(
                        request,
                        req_obj.entity,
                        page="activation_requests",
                        action="REJECT_ACTIVATION_REQUEST",
                        model_name="mobileactivationrequest",
                        object_id=req_obj.id,
                        details=f"employee_id={req_obj.employee_id}",
                    )
        return redirect("activation_requests")

    requests_qs = (
        MobileActivationRequest.objects.select_related("employee", "employee__user", "decided_by")
        .order_by(
            Case(
                When(status=MobileActivationRequest.STATUS_PENDING, then=Value(0)),
                default=Value(1),
            ),
            "-requested_at",
        )
    )
    if not is_all_scope:
        requests_qs = requests_qs.filter(entity=entity)
    if status_filter != "ALL":
        requests_qs = requests_qs.filter(status=status_filter)

    paginator = Paginator(requests_qs, 100)
    page_obj = paginator.get_page(request.GET.get("page"))

    if is_all_scope:
        pending_count = MobileActivationRequest.objects.filter(
            status=MobileActivationRequest.STATUS_PENDING
        ).count()
    else:
        pending_count = _pending_activation_requests_count(entity)
    _safe_audit_log(
        request,
        entity,
        page="activation_requests",
        action="VIEW_PAGE",
        model_name="mobileactivationrequest",
        details=f"pending={pending_count}; scope={'all' if is_all_scope else 'entity'}; status={status_filter}",
    )
    return render(
        request,
        "basmaapp/activation_requests.html",
        {
            "entity": entity,
            "page_obj": page_obj,
            "pending_count": pending_count,
            "can_edit": can_edit,
            "can_decide": can_decide,
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
            "is_superuser": is_superuser,
            "is_all_scope": is_all_scope,
            "status_filter": status_filter,
        },
    )


@staff_member_required(login_url="/admin-login/")
def data_import_view(request):
    entity = _get_staff_entity_or_403(request)
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    can_add_employee = _check_model_privilege(request, entity, "employee", "add")
    can_add_location = _check_model_privilege(request, entity, "location", "add")
    default_target = "employee" if can_add_employee else "location"
    target = (request.POST.get("target") or request.GET.get("target") or default_target).strip().lower()
    if target not in {"employee", "location"}:
        target = default_target
    model_name = "employee" if target == "employee" else "location"
    _require_model_privilege_or_403(request, entity, model_name, "add")

    result = {"created": 0, "updated": 0, "errors": []}
    if request.method == "POST" and request.FILES.get("csv_file"):
        csv_file = request.FILES.get("csv_file")
        try:
            content = csv_file.read().decode("utf-8-sig")
            reader = csv.DictReader(content.splitlines())
            rows = list(reader)
            headers = {str(h or "").strip().lower() for h in (reader.fieldnames or [])}
            required_headers = {"employee_no", "full_name"} if target == "employee" else {"name"}
            missing_headers = sorted(required_headers - headers)
            if missing_headers:
                result["errors"].append(
                    "Missing mandatory CSV columns: " + ", ".join(missing_headers)
                )
                return render(
                    request,
                    "basmaapp/data_import.html",
                    {
                        "entity": entity,
                        "target": target,
                        "result": result,
                        "footer_entity_name": entity.name,
                        "footer_user_name": _get_footer_user_name(request),
                        "ui_lang": ui_lang,
                        "header_logo_src": entity_logo_src,
                    },
                )
            with transaction.atomic():
                if target == "employee":
                    created, updated, errors = _import_employees_from_csv(entity, rows)
                else:
                    created, updated, errors = _import_locations_from_csv(entity, rows)
            result = {"created": created, "updated": updated, "errors": errors}
            _safe_audit_log(
                request,
                entity,
                page="data_import",
                action="IMPORT_CSV",
                model_name=model_name,
                details=f"created={created};updated={updated};errors={len(errors)}",
            )
        except Exception as exc:
            result["errors"].append(f"Import failed: {exc.__class__.__name__}")
            _safe_audit_log(
                request,
                entity,
                page="data_import",
                action="IMPORT_FAILED",
                model_name=model_name,
                details=str(exc),
            )
    return render(
        request,
        "basmaapp/data_import.html",
        {
            "entity": entity,
            "target": target,
            "result": result,
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
        },
    )


@staff_member_required(login_url="/admin-login/")
def super_admin_user_accounts_view(request):
    entity = _get_staff_entity_or_403(request)
    if not request.user.is_superuser:
        raise PermissionDenied("Only super admin can manage user accounts.")
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)

    users_qs = User.objects.filter(employee_profile__entity=entity).select_related("employee_profile").order_by("username")
    search = (request.GET.get("q") or "").strip()
    if search:
        users_qs = users_qs.filter(
            Q(username__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
        )

    if request.method == "POST":
        user_id_raw = (request.POST.get("user_id") or "").strip()
        target = User.objects.filter(pk=user_id_raw, employee_profile__entity=entity).first() if user_id_raw.isdigit() else None
        if not target:
            raise Http404("User not found.")
        old_username = target.username
        old_is_staff = target.is_staff
        old_is_active = target.is_active
        new_username = (request.POST.get("username") or "").strip()
        if not new_username:
            new_username = target.username
        if User.objects.exclude(pk=target.pk).filter(username=new_username).exists():
            raise PermissionDenied("Username already exists.")
        target.username = new_username
        target.is_staff = request.POST.get("is_staff") == "on"
        target.is_active = request.POST.get("is_active") == "on"
        new_password = (request.POST.get("password") or "").strip()
        if new_password:
            target.set_password(new_password)
        target.save()
        changes = [
            {"field": "username", "old": str(old_username), "new": str(target.username)},
            {"field": "is_staff", "old": str(old_is_staff), "new": str(target.is_staff)},
            {"field": "is_active", "old": str(old_is_active), "new": str(target.is_active)},
        ]
        if new_password:
            changes.append({"field": "password", "old": "***", "new": "***updated***"})
        _safe_audit_log(
            request,
            entity,
            page="super_admin_user_accounts",
            action="UPDATE_USER_ACCOUNT",
            model_name="user",
            object_id=target.pk,
            details=json.dumps({"changes": changes}, ensure_ascii=True),
        )
        return redirect(f"{reverse('super_admin_user_accounts')}?q={search}")

    paginator = Paginator(users_qs, 100)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "basmaapp/super_admin_user_accounts.html",
        {
            "entity": entity,
            "page_obj": page_obj,
            "users": page_obj.object_list,
            "search": search,
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
        },
    )


@staff_member_required(login_url="/admin-login/")
def reports_home_view(request):
    entity = _get_staff_entity_or_403(request)
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    reports_filter_keys = (
        "employee_id",
        "location_id",
        "is_active",
        "name_contains",
        "is_gps_based",
        "is_beacon_based",
        "start_date",
        "end_date",
        "late_after_hour",
        "early_before_hour",
        "employee_name",
        "employee_no",
        "civil_id",
    )
    preset_action = (request.GET.get("preset_action") or "").strip().lower()
    if preset_action == "save":
        _save_filter_preset(request, "reports_filters", reports_filter_keys)
        return redirect(f"{reverse('reports_home')}?{_querystring_without_page(request)}")
    if preset_action == "clear":
        _clear_filter_preset(request, "reports_filters")
        return redirect(reverse("reports_home"))
    if preset_action == "load":
        preset_filters = _load_filter_preset(request, "reports_filters")
        if preset_filters:
            q = urlencode(preset_filters)
            return redirect(f"{reverse('reports_home')}?{q}")
    _safe_audit_log(
        request,
        entity,
        page="reports_home",
        action="VIEW_PAGE",
        details="Opened reports page.",
    )
    employees = Employee.objects.filter(entity=entity, is_active=True).order_by("full_name")
    locations = Location.objects.filter(entity=entity).order_by("name")
    visible_advanced_reports = (
        ADVANCED_REPORTS
        if request.user.is_superuser
        else [item for item in ADVANCED_REPORTS if item[0] != "license-capacity"]
    )
    return render(
        request,
        "basmaapp/reports_home.html",
        {
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "employees": employees,
            "locations": locations,
            "saved_filters_available": bool(_load_filter_preset(request, "reports_filters")),
            "report_filters": {
                "employee_id": (request.GET.get("employee_id") or "").strip(),
                "location_id": (request.GET.get("location_id") or "").strip(),
                "is_active": (request.GET.get("is_active") or "").strip(),
                "name_contains": (request.GET.get("name_contains") or "").strip(),
                "is_gps_based": (request.GET.get("is_gps_based") or "").strip(),
                "is_beacon_based": (request.GET.get("is_beacon_based") or "").strip(),
                "start_date": (request.GET.get("start_date") or "").strip(),
                "end_date": (request.GET.get("end_date") or "").strip(),
                "late_after_hour": (request.GET.get("late_after_hour") or "9").strip(),
                "early_before_hour": (request.GET.get("early_before_hour") or "16").strip(),
                "employee_name": (request.GET.get("employee_name") or "").strip(),
                "employee_no": (request.GET.get("employee_no") or "").strip(),
                "civil_id": (request.GET.get("civil_id") or "").strip(),
            },
            "advanced_reports": visible_advanced_reports,
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
        },
    )


def _build_pdf_response(filename, title, entity_name, generated_by, headers, rows, filter_lines=None, logo_src=""):
    logo_flowable = None
    raw_logo = (logo_src or "").strip()
    if raw_logo:
        if raw_logo.startswith("data:image/"):
            parts = raw_logo.split(",", 1)
            raw_logo = parts[1] if len(parts) > 1 else ""
        try:
            logo_bytes = base64.b64decode(raw_logo, validate=False)
            if logo_bytes:
                img_reader = utils.ImageReader(BytesIO(logo_bytes))
                img_w, img_h = img_reader.getSize()
                if img_w and img_h:
                    max_w = 26 * mm
                    max_h = 26 * mm
                    scale = min(max_w / float(img_w), max_h / float(img_h))
                    logo_flowable = RLImage(
                        BytesIO(logo_bytes),
                        width=float(img_w) * scale,
                        height=float(img_h) * scale,
                    )
        except Exception:
            logo_flowable = None

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    styles = getSampleStyleSheet()
    story = []
    if logo_flowable is not None:
        story.extend([logo_flowable, Spacer(1, 4)])
    story += [
        Paragraph(title, styles["Title"]),
        Paragraph(f"Entity: {entity_name}", styles["Normal"]),
        Paragraph(f"Generated By: {generated_by}", styles["Normal"]),
        Paragraph(f"Generated At: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]),
        Spacer(1, 4),
    ]

    if filter_lines:
        for line in filter_lines:
            story.append(Paragraph(line, styles["Normal"]))
        story.append(Spacer(1, 8))

    story += [
        Spacer(1, 8),
    ]

    table_data = [headers] + rows
    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    pdf_value = buffer.getvalue()
    buffer.close()

    response = HttpResponse(pdf_value, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _filter_employee_location_assignments(entity, params):
    assignments = (
        EmployeeLocationAssignment.objects.filter(entity=entity)
        .select_related("employee", "location")
        .order_by("employee__full_name", "location__name", "pk")
    )
    employee_id = (params.get("employee_id") or "").strip()
    location_id = (params.get("location_id") or "").strip()
    is_active = (params.get("is_active") or "").strip().lower()

    filter_lines = []
    if employee_id:
        assignments = assignments.filter(employee_id=employee_id)
        employee_name = (
            Employee.objects.filter(entity=entity, pk=employee_id)
            .values_list("full_name", flat=True)
            .first()
        )
        if employee_name:
            filter_lines.append(f"Filter - Employee: {employee_name}")
    if location_id:
        assignments = assignments.filter(location_id=location_id)
        location_name = (
            Location.objects.filter(entity=entity, pk=location_id)
            .values_list("name", flat=True)
            .first()
        )
        if location_name:
            filter_lines.append(f"Filter - Location: {location_name}")
    if is_active in ("true", "false"):
        assignments = assignments.filter(is_active=(is_active == "true"))
        filter_lines.append(f"Filter - Active: {'Yes' if is_active == 'true' else 'No'}")
    return assignments, filter_lines


def _filter_locations(entity, params):
    locations = Location.objects.filter(entity=entity).order_by("name")
    is_active = (params.get("is_active") or "").strip().lower()
    is_gps_based = (params.get("is_gps_based") or "").strip().lower()
    is_beacon_based = (params.get("is_beacon_based") or "").strip().lower()
    name_contains = (params.get("name_contains") or "").strip()

    filter_lines = []
    if is_active in ("true", "false"):
        locations = locations.filter(is_active=(is_active == "true"))
        filter_lines.append(f"Filter - Active: {'Yes' if is_active == 'true' else 'No'}")
    if is_gps_based in ("true", "false"):
        locations = locations.filter(is_GPS_based=(is_gps_based == "true"))
        filter_lines.append(f"Filter - GPS Based: {'Yes' if is_gps_based == 'true' else 'No'}")
    if is_beacon_based in ("true", "false"):
        locations = locations.filter(is_beacon_based=(is_beacon_based == "true"))
        filter_lines.append(f"Filter - Beacon Based: {'Yes' if is_beacon_based == 'true' else 'No'}")
    if name_contains:
        locations = locations.filter(name__icontains=name_contains)
        filter_lines.append(f"Filter - Name Contains: {name_contains}")
    return locations, filter_lines


ADVANCED_REPORTS = [
    ("attendance-summary", "Attendance Summary Report"),
    ("late-early-exceptions", "Late & Early Exceptions Report"),
    ("overtime-working-hours", "Overtime & Working Hours Report"),
    ("biometric-failure-analysis", "Biometric Failure Analysis Report"),
    ("location-compliance", "Location Compliance Report"),
    ("manager-team", "Manager Team Report"),
    ("user-activity-admin-actions", "User Activity & Admin Actions Report"),
    ("license-capacity", "License & Capacity Report"),
    ("inactive-anomalous-data", "Inactive & Anomalous Data Report"),
    ("operational-api-health", "Operational API Health Report"),
]


def _get_common_report_filters(params):
    start_date_raw = (params.get("start_date") or "").strip()
    end_date_raw = (params.get("end_date") or "").strip()
    late_after_hour_raw = (params.get("late_after_hour") or "9").strip()
    early_before_hour_raw = (params.get("early_before_hour") or "16").strip()

    return {
        "start_date_raw": start_date_raw,
        "end_date_raw": end_date_raw,
        "start_date": parse_date(start_date_raw) if start_date_raw else None,
        "end_date": parse_date(end_date_raw) if end_date_raw else None,
        "late_after_hour": int(late_after_hour_raw) if late_after_hour_raw.isdigit() else 9,
        "early_before_hour": int(early_before_hour_raw) if early_before_hour_raw.isdigit() else 16,
        "employee_name": (params.get("employee_name") or "").strip(),
        "employee_no": (params.get("employee_no") or "").strip(),
        "civil_id": (params.get("civil_id") or "").strip(),
    }


def _apply_date_range_to_transactions(qs, filters):
    if filters["start_date"]:
        qs = qs.filter(occurred_at__date__gte=filters["start_date"])
    if filters["end_date"]:
        qs = qs.filter(occurred_at__date__lte=filters["end_date"])
    return qs


def _build_advanced_report(report_key, entity, params):
    filters = _get_common_report_filters(params)
    filter_lines = []
    if filters["start_date_raw"]:
        filter_lines.append(f"From Date: {filters['start_date_raw']}")
    if filters["end_date_raw"]:
        filter_lines.append(f"To Date: {filters['end_date_raw']}")

    transactions = _apply_date_range_to_transactions(
        AttendanceTransaction.objects.filter(entity=entity).select_related("employee", "location"),
        filters,
    )

    if report_key == "attendance-summary":
        headers = ["Employee", "Employee No", "Date", "First SIGN_IN", "Last SIGN_OUT", "Total Actions", "Biometric Failures"]
        grouped = (
            transactions.annotate(txn_date=TruncDate("occurred_at"))
            .values("employee__full_name", "employee__employee_no", "txn_date")
            .annotate(
                first_sign_in=Min("occurred_at", filter=Q(action="SIGN_IN")),
                last_sign_out=Max("occurred_at", filter=Q(action="SIGN_OUT")),
                total_actions=Count("id"),
                biometric_failures=Count("id", filter=Q(biometric_verify="FAILED")),
            )
            .order_by("-txn_date", "employee__full_name")[:3000]
        )
        rows = [
            [
                item["employee__full_name"] or "-",
                item["employee__employee_no"] or "-",
                item["txn_date"].strftime("%Y-%m-%d") if item["txn_date"] else "-",
                item["first_sign_in"].strftime("%H:%M:%S") if item["first_sign_in"] else "-",
                item["last_sign_out"].strftime("%H:%M:%S") if item["last_sign_out"] else "-",
                str(item["total_actions"]),
                str(item["biometric_failures"]),
            ]
            for item in grouped
        ]
        return "Attendance Summary Report", headers, rows, filter_lines

    if report_key == "late-early-exceptions":
        headers = ["Employee", "Employee No", "Date", "Late SIGN_IN", "Early SIGN_OUT", "Missing SIGN_OUT"]
        filter_lines.append(f"Late After Hour: {filters['late_after_hour']}")
        filter_lines.append(f"Early Before Hour: {filters['early_before_hour']}")
        grouped = (
            transactions.annotate(txn_date=TruncDate("occurred_at"), hour=ExtractHour("occurred_at"))
            .values("employee__full_name", "employee__employee_no", "txn_date")
            .annotate(
                late_sign_in=Count("id", filter=Q(action="SIGN_IN", hour__gte=filters["late_after_hour"])),
                early_sign_out=Count("id", filter=Q(action="SIGN_OUT", hour__lte=filters["early_before_hour"])),
                sign_in_count=Count("id", filter=Q(action="SIGN_IN")),
                sign_out_count=Count("id", filter=Q(action="SIGN_OUT")),
            )
            .order_by("-txn_date", "employee__full_name")[:3000]
        )
        rows = [
            [
                item["employee__full_name"] or "-",
                item["employee__employee_no"] or "-",
                item["txn_date"].strftime("%Y-%m-%d") if item["txn_date"] else "-",
                str(item["late_sign_in"]),
                str(item["early_sign_out"]),
                "Yes" if item["sign_in_count"] > item["sign_out_count"] else "No",
            ]
            for item in grouped
            if item["late_sign_in"] > 0 or item["early_sign_out"] > 0 or item["sign_in_count"] > item["sign_out_count"]
        ]
        return "Late & Early Exceptions Report", headers, rows, filter_lines

    if report_key == "overtime-working-hours":
        headers = ["Employee", "Employee No", "Date", "First SIGN_IN", "SIGN_CONFIRM Time", "Last SIGN_OUT", "Worked Hours", "Required Hours", "Overtime Hours"]
        if filters["employee_name"]:
            transactions = transactions.filter(employee__full_name__icontains=filters["employee_name"])
            filter_lines.append(f"Employee Name: {filters['employee_name']}")
        if filters["employee_no"]:
            transactions = transactions.filter(employee__employee_no__icontains=filters["employee_no"])
            filter_lines.append(f"Employee No: {filters['employee_no']}")
        if filters["civil_id"]:
            transactions = transactions.filter(employee__civil_id__icontains=filters["civil_id"])
            filter_lines.append(f"Civil ID: {filters['civil_id']}")
        grouped = (
            transactions.annotate(txn_date=TruncDate("occurred_at"))
            .values("employee_id", "employee__full_name", "employee__employee_no", "employee__number_working_hours_per_day", "txn_date")
            .annotate(
                first_sign_in=Min("occurred_at", filter=Q(action="SIGN_IN")),
                sign_confirm_time=Min("occurred_at", filter=Q(action="SIGN_CONFIRM")),
                last_sign_out=Max("occurred_at", filter=Q(action="SIGN_OUT")),
            )
            .order_by("-txn_date", "employee__full_name")[:3000]
        )
        rows = []
        for item in grouped:
            worked = 0.0
            if item["first_sign_in"] and item["last_sign_out"]:
                delta = item["last_sign_out"] - item["first_sign_in"]
                worked = max(0.0, round(delta.total_seconds() / 3600.0, 2))
            required = float(item["employee__number_working_hours_per_day"] or 0.0)
            overtime = round(max(0.0, worked - required), 2)
            rows.append(
                [
                    item["employee__full_name"] or "-",
                    item["employee__employee_no"] or "-",
                    item["txn_date"].strftime("%Y-%m-%d") if item["txn_date"] else "-",
                    item["first_sign_in"].strftime("%H:%M:%S") if item["first_sign_in"] else "-",
                    item["sign_confirm_time"].strftime("%H:%M:%S") if item["sign_confirm_time"] else "-",
                    item["last_sign_out"].strftime("%H:%M:%S") if item["last_sign_out"] else "-",
                    f"{worked:.2f}",
                    f"{required:.2f}",
                    f"{overtime:.2f}",
                ]
            )
        return "Overtime & Working Hours Report", headers, rows, filter_lines

    if report_key == "biometric-failure-analysis":
        headers = ["Date", "Failure Type", "Count"]
        failure_rows = []
        grouped = (
            transactions.filter(biometric_verify="FAILED")
            .annotate(txn_date=TruncDate("occurred_at"))
            .values("txn_date", "biometric_error")
            .annotate(total=Count("id"))
            .order_by("-txn_date")[:5000]
        )
        for item in grouped:
            raw = (item["biometric_error"] or "").lower()
            if raw.startswith("liveness:"):
                kind = "Liveness Failure"
            elif raw.startswith("compare:"):
                kind = "Face Compare Failure"
            elif "no face" in raw:
                kind = "No Face Detected"
            elif raw:
                kind = "API/Other Error"
            else:
                kind = "Unknown"
            failure_rows.append(
                [
                    item["txn_date"].strftime("%Y-%m-%d") if item["txn_date"] else "-",
                    kind,
                    str(item["total"]),
                ]
            )
        return "Biometric Failure Analysis Report", headers, failure_rows, filter_lines

    if report_key == "location-compliance":
        headers = ["Occurred At", "Employee", "Employee No", "Action", "Location", "Compliance"]
        valid_assignment = EmployeeLocationAssignment.objects.filter(
            entity=entity,
            employee_id=OuterRef("employee_id"),
            location_id=OuterRef("location_id"),
            is_active=True,
        ).filter(
            Q(start_date__isnull=True) | Q(start_date__lte=OuterRef("txn_date"))
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=OuterRef("txn_date"))
        )
        checks = (
            transactions.exclude(location_id__isnull=True)
            .annotate(txn_date=TruncDate("occurred_at"))
            .annotate(is_assigned=Exists(valid_assignment))
            .order_by("-occurred_at")[:3000]
        )
        rows = [
            [
                tx.occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
                tx.employee.full_name if tx.employee else "-",
                tx.employee.employee_no if tx.employee else "-",
                tx.action,
                tx.location.name if tx.location else "-",
                "Within Assigned Location" if tx.is_assigned else "Outside Assigned Location",
            ]
            for tx in checks
        ]
        return "Location Compliance Report", headers, rows, filter_lines

    if report_key == "manager-team":
        headers = ["Manager", "Manager Employee No", "Team Size", "Team Transactions", "Team Biometric Failures"]
        managers = Employee.objects.filter(entity=entity, is_manager=True, is_active=True).order_by("full_name")[:1000]
        rows = []
        for manager in managers:
            team_ids = list(manager.subordinates.filter(is_active=True).values_list("id", flat=True))
            if not team_ids:
                rows.append([manager.full_name, manager.employee_no or "-", "0", "0", "0"])
                continue
            team_tx = transactions.filter(employee_id__in=team_ids)
            rows.append(
                [
                    manager.full_name,
                    manager.employee_no or "-",
                    str(len(team_ids)),
                    str(team_tx.count()),
                    str(team_tx.filter(biometric_verify="FAILED").count()),
                ]
            )
        return "Manager Team Report", headers, rows, filter_lines

    if report_key == "user-activity-admin-actions":
        headers = ["User", "Total Audit Actions", "Denied Actions", "Last Action At"]
        audit_logs = Audit.objects.filter(entity=entity)
        if filters["start_date"]:
            audit_logs = audit_logs.filter(created_at__date__gte=filters["start_date"])
        if filters["end_date"]:
            audit_logs = audit_logs.filter(created_at__date__lte=filters["end_date"])
        grouped = (
            audit_logs.values("user__username")
            .annotate(
                total_actions=Count("id"),
                denied_actions=Count("id", filter=Q(action__icontains="DENIED")),
                last_action_at=Max("created_at"),
            )
            .order_by("-total_actions", "user__username")[:3000]
        )
        rows = [
            [
                item["user__username"] or "-",
                str(item["total_actions"]),
                str(item["denied_actions"]),
                item["last_action_at"].strftime("%Y-%m-%d %H:%M:%S") if item["last_action_at"] else "-",
            ]
            for item in grouped
        ]
        return "User Activity & Admin Actions Report", headers, rows, filter_lines

    if report_key == "license-capacity":
        headers = ["Entity", "Code", "License Expire Date", "License Status", "Current Employees", "Max Employees"]
        settings_obj = EntitySettings.objects.filter(entity=entity).only("number_employees").first()
        max_employees = settings_obj.number_employees if settings_obj else None
        current_employees = Employee.objects.filter(entity=entity).count()
        if _is_entity_license_expired(entity):
            status_text = "Expired/Invalid"
        else:
            days_left = (entity.license_expire_date - timezone.localdate()).days
            status_text = f"Valid ({days_left} days left)"
        rows = [[
            entity.name,
            entity.code,
            entity.license_expire_date.strftime("%Y-%m-%d") if entity.license_expire_date else "-",
            status_text,
            str(current_employees),
            str(max_employees) if max_employees is not None else "-",
        ]]
        return "License & Capacity Report", headers, rows, filter_lines

    if report_key == "inactive-anomalous-data":
        headers = ["Check", "Count"]
        duplicate_civil_ids = (
            Employee.objects.filter(entity=entity)
            .exclude(civil_id__isnull=True)
            .exclude(civil_id__exact="")
            .values("civil_id")
            .annotate(c=Count("id"))
            .filter(c__gt=1)
            .count()
        )
        duplicate_employee_no = (
            Employee.objects.filter(entity=entity)
            .exclude(employee_no__isnull=True)
            .exclude(employee_no__exact="")
            .values("employee_no")
            .annotate(c=Count("id"))
            .filter(c__gt=1)
            .count()
        )
        rows = [
            ["Inactive Employees With Active Assignments", str(EmployeeLocationAssignment.objects.filter(entity=entity, is_active=True, employee__is_active=False).count())],
            ["Employees Missing Photo", str(Employee.objects.filter(entity=entity).filter(Q(photo_base64__isnull=True) | Q(photo_base64__exact="")).count())],
            ["Duplicate Civil ID Groups", str(duplicate_civil_ids)],
            ["Duplicate Employee No Groups", str(duplicate_employee_no)],
            ["GPS Locations Missing Coordinates", str(Location.objects.filter(entity=entity, is_GPS_based=True).filter(Q(latitude__isnull=True) | Q(longitude__isnull=True)).count())],
        ]
        return "Inactive & Anomalous Data Report", headers, rows, filter_lines

    if report_key == "operational-api-health":
        headers = ["Date", "Total Checks", "Passed", "Failed", "Success Rate %", "Error Rate %"]
        face_qs = transactions.filter(biometric_method="FACE_COMPARE_API")
        grouped = (
            face_qs.annotate(txn_date=TruncDate("occurred_at"))
            .values("txn_date")
            .annotate(
                total=Count("id"),
                passed=Count("id", filter=Q(biometric_verify="PASSED")),
                failed=Count("id", filter=Q(biometric_verify="FAILED")),
            )
            .order_by("-txn_date")[:3000]
        )
        rows = []
        for item in grouped:
            total = item["total"] or 0
            success_rate = round((item["passed"] * 100.0) / total, 2) if total else 0.0
            error_rate = round((item["failed"] * 100.0) / total, 2) if total else 0.0
            rows.append(
                [
                    item["txn_date"].strftime("%Y-%m-%d") if item["txn_date"] else "-",
                    str(total),
                    str(item["passed"]),
                    str(item["failed"]),
                    f"{success_rate:.2f}",
                    f"{error_rate:.2f}",
                ]
            )
        return "Operational API Health Report", headers, rows, filter_lines

    raise Http404("Unknown report type")


@staff_member_required(login_url="/admin-login/")
def report_advanced_preview(request, report_key):
    entity = _get_staff_entity_or_403(request)
    if report_key == "license-capacity" and not request.user.is_superuser:
        raise PermissionDenied("License & Capacity Report is allowed only for super admin.")
    privilege_model = "attendancetransaction"
    if report_key == "user-activity-admin-actions":
        privilege_model = None
    if report_key in {"license-capacity", "inactive-anomalous-data"}:
        privilege_model = "entitysettings"
    if privilege_model:
        _require_model_privilege_or_403(request, entity, privilege_model, "read")
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    title, headers, rows, filter_lines = _build_advanced_report(report_key, entity, request.GET)
    _safe_audit_log(
        request,
        entity,
        page="report_preview",
        action="PREVIEW_REPORT",
        model_name=report_key,
        details=request.GET.urlencode(),
    )
    paginator = Paginator(rows, 100)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "basmaapp/report_preview.html",
        {
            "title": title,
            "headers": headers,
            "rows": page_obj.object_list,
            "page_obj": page_obj,
            "filter_lines": filter_lines,
            "download_url": reverse("report_advanced_pdf", kwargs={"report_key": report_key}),
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "query_string": query_params.urlencode(),
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
        },
    )


@staff_member_required(login_url="/admin-login/")
def report_advanced_pdf(request, report_key):
    entity = _get_staff_entity_or_403(request)
    if report_key == "license-capacity" and not request.user.is_superuser:
        raise PermissionDenied("License & Capacity Report is allowed only for super admin.")
    privilege_model = "attendancetransaction"
    if report_key == "user-activity-admin-actions":
        privilege_model = None
    if report_key in {"license-capacity", "inactive-anomalous-data"}:
        privilege_model = "entitysettings"
    if privilege_model:
        _require_model_privilege_or_403(request, entity, privilege_model, "read")
    title, headers, rows, filter_lines = _build_advanced_report(report_key, entity, request.GET)
    generated_by = request.user.get_full_name().strip() or request.user.username
    _safe_audit_log(
        request,
        entity,
        page="reports_pdf",
        action="DOWNLOAD_PDF",
        model_name=report_key,
        details=request.GET.urlencode(),
    )
    return _build_pdf_response(
        f"{report_key}_report.pdf",
        title,
        entity.name,
        generated_by,
        headers,
        rows or [["No records"]],
        filter_lines=filter_lines,
        logo_src=_get_entity_logo_src(entity),
    )


@staff_member_required(login_url="/admin-login/")
def report_employee_location_assignments_preview(request):
    entity = _get_staff_entity_or_403(request)
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    _require_model_privilege_or_403(
        request,
        entity,
        "employeelocationassignment",
        "read",
    )
    assignments, filter_lines = _filter_employee_location_assignments(entity, request.GET)
    _safe_audit_log(
        request,
        entity,
        page="report_preview",
        action="PREVIEW_REPORT",
        model_name="employeelocationassignment",
        details=request.GET.urlencode(),
    )
    headers = ["ID", "Employee", "Location", "GPS Radius", "Action Period (min)", "Active", "Sign In", "Sign Confirm", "Sign Out", "Start Date", "End Date"]
    rows = []
    for item in assignments:
        rows.append(
            [
                str(item.pk),
                item.employee.full_name if item.employee else "-",
                item.location.name if item.location else "-",
                str(item.gps_radius_meters),
                str(item.period_to_take_action),
                "Yes" if item.is_active else "No",
                "Yes" if item.allow_sign_in else "No",
                "Yes" if item.allow_sign_confirm else "No",
                "Yes" if item.allow_sign_out else "No",
                str(item.start_date) if item.start_date else "-",
                str(item.end_date) if item.end_date else "-",
            ]
        )
    paginator = Paginator(rows, 100)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "basmaapp/report_preview.html",
        {
            "title": "Employee Location Assignments Report",
            "headers": headers,
            "rows": page_obj.object_list,
            "page_obj": page_obj,
            "filter_lines": filter_lines,
            "download_url": reverse("report_employee_location_assignments_pdf"),
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "query_string": query_params.urlencode(),
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
        },
    )


@staff_member_required(login_url="/admin-login/")
def report_locations_preview(request):
    entity = _get_staff_entity_or_403(request)
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    _require_model_privilege_or_403(request, entity, "location", "read")
    locations, filter_lines = _filter_locations(entity, request.GET)
    _safe_audit_log(
        request,
        entity,
        page="report_preview",
        action="PREVIEW_REPORT",
        model_name="location",
        details=request.GET.urlencode(),
    )
    headers = ["ID", "Name", "Active", "GPS Based", "Beacon Based", "Latitude", "Longitude"]
    rows = []
    for loc in locations:
        rows.append(
            [
                str(loc.pk),
                loc.name or "-",
                "Yes" if loc.is_active else "No",
                "Yes" if loc.is_GPS_based else "No",
                "Yes" if loc.is_beacon_based else "No",
                str(loc.latitude) if loc.latitude is not None else "-",
                str(loc.longitude) if loc.longitude is not None else "-",
            ]
        )
    paginator = Paginator(rows, 100)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "basmaapp/report_preview.html",
        {
            "title": "Locations Report",
            "headers": headers,
            "rows": page_obj.object_list,
            "page_obj": page_obj,
            "filter_lines": filter_lines,
            "download_url": reverse("report_locations_pdf"),
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "query_string": query_params.urlencode(),
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
        },
    )


@staff_member_required(login_url="/admin-login/")
def report_employee_location_assignments_pdf(request):
    entity = _get_staff_entity_or_403(request)
    _require_model_privilege_or_403(
        request,
        entity,
        "employeelocationassignment",
        "read",
    )
    assignments, filter_lines = _filter_employee_location_assignments(entity, request.GET)
    generated_by = request.user.get_full_name().strip() or request.user.username
    _safe_audit_log(
        request,
        entity,
        page="reports_pdf",
        action="DOWNLOAD_PDF",
        model_name="employeelocationassignment",
        details=request.GET.urlencode(),
    )

    rows = []
    for item in assignments:
        rows.append(
            [
                str(item.pk),
                item.employee.full_name if item.employee else "-",
                item.location.name if item.location else "-",
                "Yes" if item.is_active else "No",
                "Yes" if item.allow_sign_in else "No",
                "Yes" if item.allow_sign_confirm else "No",
                "Yes" if item.allow_sign_out else "No",
                str(item.gps_radius_meters),
                str(item.period_to_take_action),
                str(item.start_date) if item.start_date else "-",
                str(item.end_date) if item.end_date else "-",
            ]
        )
    return _build_pdf_response(
        "employee_location_assignments_report.pdf",
        "Employee Location Assignments Report",
        entity.name,
        generated_by,
        [
            "ID",
            "Employee",
            "Location",
            "Active",
            "Sign In",
            "Sign Confirm",
            "Sign Out",
            "GPS Radius",
            "Action Period (min)",
            "Start Date",
            "End Date",
        ],
        rows or [["-", "No records", "-", "-", "-", "-", "-", "-", "-", "-", "-"]],
        filter_lines=filter_lines,
        logo_src=_get_entity_logo_src(entity),
    )


@staff_member_required(login_url="/admin-login/")
def report_locations_pdf(request):
    entity = _get_staff_entity_or_403(request)
    _require_model_privilege_or_403(request, entity, "location", "read")
    locations, filter_lines = _filter_locations(entity, request.GET)
    generated_by = request.user.get_full_name().strip() or request.user.username
    _safe_audit_log(
        request,
        entity,
        page="reports_pdf",
        action="DOWNLOAD_PDF",
        model_name="location",
        details=request.GET.urlencode(),
    )

    rows = []
    for loc in locations:
        rows.append(
            [
                str(loc.pk),
                loc.name or "-",
                "Yes" if loc.is_active else "No",
                "Yes" if loc.is_GPS_based else "No",
                "Yes" if loc.is_beacon_based else "No",
                str(loc.latitude) if loc.latitude is not None else "-",
                str(loc.longitude) if loc.longitude is not None else "-",
            ]
        )
    return _build_pdf_response(
        "locations_report.pdf",
        "Locations Report",
        entity.name,
        generated_by,
        [
            "ID",
            "Name",
            "Active",
            "GPS Based",
            "Beacon Based",
            "Latitude",
            "Longitude",
        ],
        rows or [["-", "No records", "-", "-", "-", "-", "-"]],
        filter_lines=filter_lines,
        logo_src=_get_entity_logo_src(entity),
    )


def _get_basma_model_or_404(model_name):
    for model in apps.get_app_config("basmaapp").get_models():
        if model._meta.model_name == model_name:
            return model
    raise Http404("Model not found")


def _get_staff_entity_or_403(request):
    if request.user.is_superuser:
        selected_entity_id = request.session.get("admin_selected_entity_id")
        entity = None
        if selected_entity_id:
            entity = Entity.objects.filter(pk=selected_entity_id).first()
        if entity is None:
            employee_profile = Employee.objects.filter(user=request.user, is_active=True).select_related("entity").first()
            if employee_profile and employee_profile.entity:
                entity = employee_profile.entity
        if entity is None:
            entity = Entity.objects.order_by("name", "id").first()
        if entity is None:
            raise PermissionDenied("No entity exists in the system.")
        _set_admin_selected_entity_id(request, entity.pk)
    else:
        employee_profile = Employee.objects.filter(user=request.user, is_active=True).select_related("entity").first()
        if not employee_profile or not employee_profile.entity:
            raise PermissionDenied("No entity is linked to this staff user.")
        entity = employee_profile.entity

    _sync_entity_active_by_license(entity)
    if _is_entity_license_expired(entity) and not request.user.is_superuser:
        raise PermissionDenied(
            _get_entity_license_error_message(entity) + " Access is allowed only for super admin."
        )
    return entity


def _get_employee_by_user_id_with_license_check(employee_id):
    employee_id_raw = str(employee_id).strip()
    if not employee_id_raw.isdigit():
        raise Employee.DoesNotExist
    employee_pk = int(employee_id_raw)

    # Accept both employee.id and employee.user_id to be compatible with mobile payloads.
    employee = (
        Employee.objects.select_related("entity", "user")
        .filter(id=employee_pk, is_active=True)
        .first()
    )
    if not employee:
        employee = (
            Employee.objects.select_related("entity", "user")
            .filter(user_id=employee_pk, is_active=True)
            .first()
        )
    if not employee:
        raise Employee.DoesNotExist

    _sync_entity_active_by_license(employee.entity)
    if _is_entity_license_expired(employee.entity):
        raise PermissionDenied(_get_entity_license_error_message(employee.entity))
    return employee


def _get_authorized_employee_for_mobile_request(
    request,
    body,
    employee_id_key="employee_id",
    allow_staff_override=False,
):
    employee_id = body.get(employee_id_key)
    if not employee_id:
        raise Employee.DoesNotExist
    employee = _get_employee_by_user_id_with_license_check(employee_id)

    # Staff web sessions are already entity-scoped by admin guard.
    if request.user.is_authenticated and request.user.is_staff:
        return employee

    if allow_staff_override:
        by_staff_id = str(body.get("by_staff_id") or "").strip()
        if by_staff_id.isdigit():
            actor = (
                Employee.objects.select_related("user", "entity")
                .filter(is_active=True)
                .filter(Q(id=int(by_staff_id)) | Q(user_id=int(by_staff_id)))
                .first()
            )
            if actor and actor.entity_id == employee.entity_id and (actor.user.is_staff or actor.is_manager):
                return employee

    employee_uuid = str(
        body.get("employee_uuid")
        or request.headers.get("X-Employee-UUID")
        or request.META.get("HTTP_X_EMPLOYEE_UUID")
        or ""
    ).strip()
    device_uuid = str(
        body.get("device_uuid")
        or request.headers.get("X-Device-UUID")
        or request.META.get("HTTP_X_DEVICE_UUID")
        or ""
    ).strip()
    if not employee_uuid:
        raise PermissionDenied("identity_credentials_required")
    if str(employee.employee_uuid).strip().lower() != employee_uuid.lower():
        raise PermissionDenied("identity_mismatch")

    bound_device_uuid = str(getattr(employee, "device_uuid", "") or "").strip()
    if bound_device_uuid:
        if not device_uuid:
            raise PermissionDenied("device_uuid_required")
        if bound_device_uuid.lower() != device_uuid.lower():
            raise PermissionDenied("device_mismatch")

    return employee


def _resolve_employee_for_activation_identifier(identifier):
    text = str(identifier or "").strip()
    if not text:
        return None, None, "identifier is required"

    if "-" not in text:
        return None, None, "identifier_format_invalid"

    entity_code_raw, identifier_part_raw = text.split("-", 1)
    entity_code = entity_code_raw.strip()
    identifier_part = identifier_part_raw.strip()
    if not entity_code or not identifier_part:
        return None, None, "identifier_format_invalid"

    entity = Entity.objects.filter(code__iexact=entity_code).first()
    if entity is None:
        return None, None, "entity_not_found"

    settings_obj = getattr(entity, "settings", None)
    lookup_field = str(getattr(settings_obj, "activation_lookup_field", "employee_no") or "employee_no").strip().lower()
    if lookup_field not in {"employee_no", "civil_id", "username"}:
        lookup_field = "employee_no"

    employees = Employee.objects.select_related("entity", "entity__settings", "user").filter(entity=entity)
    if lookup_field == "civil_id":
        matches = list(employees.filter(civil_id__iexact=identifier_part)[:2])
    elif lookup_field == "username":
        matches = list(employees.filter(user__username__iexact=identifier_part)[:2])
    else:
        matches = list(employees.filter(employee_no__iexact=identifier_part)[:2])

    if not matches:
        return None, None, "identifier_not_found"
    if len(matches) > 1:
        return None, None, "identifier_ambiguous"
    employee = matches[0]
    return employee, lookup_field, ""


@csrf_exempt
@require_POST
def start_employee_activation(request):
    """
    POST /api/employee/start_activation/
    Body:
    {
        "identifier": "ENTITYCODE-IDENTIFIER (e.g. SWAP-E1023)",
        "device_uuid": "uuid-string"  # optional for option_1, required for option_2
    }
    """
    try:
        body = json.loads(request.body)
        identifier = (body.get("identifier") or "").strip()
        if not identifier:
            return JsonResponse({"error": "identifier is required"}, status=400)

        employee, lookup_field, resolve_error = _resolve_employee_for_activation_identifier(identifier)
        if not employee:
            if resolve_error == "identifier_format_invalid":
                return JsonResponse(
                    {"error": "Invalid identifier format. Use ENTITYCODE-IDENTIFIER (e.g. SWAP-E1023)."},
                    status=400,
                )
            if resolve_error == "entity_not_found":
                return JsonResponse({"error": "Entity code not found."}, status=404)
            if resolve_error == "identifier_ambiguous":
                return JsonResponse({"error": "Identifier is ambiguous in this entity; contact admin."}, status=409)
            return JsonResponse({"error": "Employee not found for provided identifier in this entity."}, status=404)

        _sync_entity_active_by_license(employee.entity)
        if _is_entity_license_expired(employee.entity):
            return JsonResponse({"error": _get_entity_license_error_message(employee.entity)}, status=403)

        settings_obj = getattr(employee.entity, "settings", None)
        activation_mode = str(getattr(settings_obj, "activation_mode", "option_1") or "option_1").strip().lower()
        if activation_mode not in {"option_1", "option_2"}:
            activation_mode = "option_1"

        label_map = {
            "employee_no": "Employee Number",
            "civil_id": "Civil ID",
            "username": "Username",
        }
        lookup_field = lookup_field if lookup_field in label_map else "employee_no"
        response_payload = {
            "employee_id": employee.user_id,
            "entity_id": employee.entity_id,
            "entity_name": employee.entity.name,
            "lookup_field": lookup_field,
            "lookup_label": label_map.get(lookup_field, "Employee Number"),
            "activation_mode": activation_mode,
            "identifier_verified": True,
        }

        if activation_mode == "option_2":
            generated_uuid = str(uuid.uuid4())
            now_dt = timezone.now()
            requested_device_uuid = str(body.get("device_uuid") or "").strip()
            employee.employee_uuid = generated_uuid
            employee.device_uuid = generated_uuid
            employee.device_bound_at = now_dt
            employee.last_updated_UUID_at = now_dt
            employee.updated_UUID_by = None
            employee.is_active = False
            employee.save(
                update_fields=[
                    "employee_uuid",
                    "device_uuid",
                    "device_bound_at",
                    "last_updated_UUID_at",
                    "updated_UUID_by",
                    "is_active",
                ]
            )
            existing_pending = (
                MobileActivationRequest.objects.filter(
                    entity=employee.entity,
                    employee=employee,
                    status=MobileActivationRequest.STATUS_PENDING,
                )
                .order_by("-requested_at")
                .first()
            )
            if existing_pending:
                existing_pending.requested_identifier = identifier
                existing_pending.lookup_field = lookup_field
                existing_pending.activation_mode = activation_mode
                existing_pending.requested_device_uuid = requested_device_uuid
                existing_pending.decided_at = None
                existing_pending.decided_by = None
                existing_pending.decision_note = ""
                existing_pending.save(
                    update_fields=[
                        "requested_identifier",
                        "lookup_field",
                        "activation_mode",
                        "requested_device_uuid",
                        "decided_at",
                        "decided_by",
                        "decision_note",
                        "updated_at",
                    ]
                )
                activation_request = existing_pending
            else:
                activation_request = MobileActivationRequest.objects.create(
                    entity=employee.entity,
                    employee=employee,
                    requested_identifier=identifier,
                    lookup_field=lookup_field,
                    activation_mode=activation_mode,
                    requested_device_uuid=requested_device_uuid,
                    status=MobileActivationRequest.STATUS_PENDING,
                )
            response_payload.update(
                {
                    "employee_uuid": generated_uuid,
                    "activation_request_id": activation_request.id,
                    "deactivated_for_approval": True,
                    "message": "Activation started. Account is pending admin approval.",
                }
            )
            return JsonResponse(response_payload, status=200)

        response_payload.update(
            {
                "deactivated_for_approval": False,
                "message": "Identifier verified. Continue with UUID flow.",
            }
        )
        return JsonResponse(response_payload, status=200)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


@csrf_exempt
@require_POST
def activate_employee_by_staff(request):
    """
    POST /api/employee/activate_employee_by_staff/
    Body:
    {
        "employee_id": "12",   # target employee.id or user_id
        "by_staff_id": "3",    # staff user id
        "request_id": "77"     # optional activation request id
    }
    """
    try:
        body = json.loads(request.body)
        target_raw = str(body.get("employee_id") or "").strip()
        actor_raw = str(body.get("by_staff_id") or "").strip()
        request_id_raw = str(body.get("request_id") or "").strip()
        if not target_raw or not actor_raw:
            return JsonResponse({"error": "employee_id and by_staff_id are required"}, status=400)
        if not target_raw.isdigit() or not actor_raw.isdigit():
            return JsonResponse({"error": "employee_id and by_staff_id must be numeric"}, status=400)

        target_id = int(target_raw)
        actor_user_id = int(actor_raw)

        actor = (
            Employee.objects.select_related("user", "entity")
            .filter(user_id=actor_user_id, is_active=True)
            .first()
        )
        if not actor or not actor.user.is_staff:
            return JsonResponse({"error": "Only staff can activate users"}, status=403)

        target = (
            Employee.objects.select_related("entity", "user")
            .filter(Q(id=target_id) | Q(user_id=target_id))
            .first()
        )
        if not target:
            return JsonResponse({"error": "Employee not found"}, status=404)
        if target.entity_id != actor.entity_id:
            return JsonResponse({"error": "Cross-entity activation is not allowed"}, status=403)

        _sync_entity_active_by_license(target.entity)
        if _is_entity_license_expired(target.entity):
            return JsonResponse({"error": _get_entity_license_error_message(target.entity)}, status=403)

        if not target.is_active:
            target.is_active = True
            target.save(update_fields=["is_active"])

        now_dt = timezone.now()
        pending_reqs = MobileActivationRequest.objects.filter(
            entity=target.entity,
            employee=target,
            status=MobileActivationRequest.STATUS_PENDING,
        )
        if request_id_raw.isdigit():
            pending_reqs = pending_reqs.filter(pk=int(request_id_raw))
        pending_reqs.update(
            status=MobileActivationRequest.STATUS_APPROVED,
            decided_at=now_dt,
            decided_by=request.user if request.user.is_authenticated else None,
            decision_note="Approved via activate_employee_by_staff",
        )

        _safe_audit_log(
            request,
            target.entity,
            page="api_activate_employee_by_staff",
            action="API_ACTIVATE_EMPLOYEE_SUCCESS",
            model_name="employee",
            object_id=target.id,
            details=f"target_employee_id={target.id}; by_staff_user_id={actor_user_id}",
        )

        return JsonResponse(
            {
                "message": "Employee activated successfully",
                "employee_id": target.id,
                "user_id": target.user_id,
                "is_active": True,
            },
            status=200,
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as exc:
        return JsonResponse({"error": str(exc) or "Failed to activate employee"}, status=400)


def _get_active_assignment_for_employee_location(employee, location):
    today = timezone.localdate()
    return (
        EmployeeLocationAssignment.objects.filter(
            entity=employee.entity,
            employee=employee,
            location=location,
            is_active=True,
        )
        .filter(Q(start_date__isnull=True) | Q(start_date__lte=today))
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .first()
    )


def _is_assignment_action_allowed(assignment, action):
    if action == "SIGN_IN":
        return bool(getattr(assignment, "allow_sign_in", False))
    if action == "SIGN_OUT":
        return bool(getattr(assignment, "allow_sign_out", False))
    if action == "SIGN_CONFIRM":
        return bool(getattr(assignment, "allow_sign_confirm", False))
    return False


def _scope_queryset_by_entity(queryset, entity, current_user=None):
    model = queryset.model
    field_names = {field.name for field in model._meta.fields}

    # Each user can only see his/her own audit logs.
    if model._meta.model_name == "audit":
        if current_user is None:
            return queryset.none()
        return queryset.filter(entity=entity, user=current_user)

    if "entity" in field_names:
        return queryset.filter(entity=entity)
    if model._meta.model_name == "entity":
        return queryset.filter(pk=entity.pk)
    return queryset.none()


def _restrict_form_to_entity(form, entity, actor_user=None):
    model = form._meta.model
    model_field_names = {field.name for field in model._meta.fields}

    # Entity value is controlled by server-side scope.
    if "entity" in form.fields:
        form.fields.pop("entity")
        form.initial["entity"] = entity.pk
    if "assigned_by" in form.fields and actor_user is not None:
        form.fields.pop("assigned_by")
        form.initial["assigned_by"] = actor_user.pk

    for field_name, form_field in form.fields.items():
        if isinstance(form_field, ModelChoiceField):
            qs = form_field.queryset
            related_model = qs.model
            related_field_names = {f.name for f in related_model._meta.fields}
            if "entity" in related_field_names:
                form_field.queryset = qs.filter(entity=entity)
            elif related_model._meta.model_name == "entity":
                form_field.queryset = qs.filter(pk=entity.pk)
            elif related_model._meta.model_name == "user":
                # Restrict user selectors to users linked to current entity employees.
                form_field.queryset = qs.filter(
                    employee_profile__entity=entity,
                    employee_profile__is_active=True,
                ).distinct()

    if "entity" in model_field_names:
        form.instance.entity = entity
    if "assigned_by" in model_field_names and actor_user is not None:
        form.instance.assigned_by = actor_user


def _enable_calendar_widgets(form):
    for form_field in form.fields.values():
        if isinstance(form_field, forms.DateField):
            form_field.widget.input_type = "date"
            form_field.widget.attrs["type"] = "date"
        if isinstance(form_field, forms.DateTimeField):
            form_field.widget.input_type = "datetime-local"
            form_field.widget.attrs["type"] = "datetime-local"


def _has_model_field(model, field_name):
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def _parse_bool_text(raw_value):
    raw = str(raw_value or "").strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _apply_model_records_filters(records, model, request):
    model_name = model._meta.model_name
    filter_values = {
        "name": (request.GET.get("name") or "").strip(),
        "id": (request.GET.get("id") or "").strip(),
        "date_from": (request.GET.get("date_from") or "").strip(),
        "date_to": (request.GET.get("date_to") or "").strip(),
        "civil_id": (request.GET.get("civil_id") or "").strip(),
        "employee_no": (request.GET.get("employee_no") or "").strip(),
        "employee": (request.GET.get("employee") or "").strip(),
        "location": (request.GET.get("location") or "").strip(),
        "user": (request.GET.get("user") or "").strip(),
        "description": (request.GET.get("description") or "").strip(),
        "column": (request.GET.get("column") or "").strip().lower(),
        "column_value": (request.GET.get("column_value") or "").strip(),
    }

    if filter_values["name"]:
        name_query = filter_values["name"]
        q = Q()
        if _has_model_field(model, "full_name"):
            q |= Q(full_name__icontains=name_query)
        if _has_model_field(model, "name"):
            q |= Q(name__icontains=name_query)
        if _has_model_field(model, "display_name"):
            q |= Q(display_name__icontains=name_query)
        if _has_model_field(model, "employee"):
            q |= Q(employee__full_name__icontains=name_query)
        if _has_model_field(model, "manager"):
            q |= Q(manager__full_name__icontains=name_query)
        if _has_model_field(model, "user"):
            q |= Q(user__username__icontains=name_query)
            q |= Q(user__first_name__icontains=name_query)
            q |= Q(user__last_name__icontains=name_query)
        if q:
            records = records.filter(q)

    if filter_values["id"]:
        id_query = filter_values["id"]
        if id_query.isdigit():
            records = records.filter(pk=int(id_query))
        elif _has_model_field(model, "code"):
            records = records.filter(code__icontains=id_query)

    if filter_values["civil_id"]:
        civil_id_query = filter_values["civil_id"]
        q = Q()
        if _has_model_field(model, "civil_id"):
            q |= Q(civil_id__icontains=civil_id_query)
        if _has_model_field(model, "employee"):
            q |= Q(employee__civil_id__icontains=civil_id_query)
        if q:
            records = records.filter(q)

    if filter_values["employee_no"]:
        employee_no_query = filter_values["employee_no"]
        q = Q()
        if _has_model_field(model, "employee_no"):
            q |= Q(employee_no__icontains=employee_no_query)
        if _has_model_field(model, "employee"):
            q |= Q(employee__employee_no__icontains=employee_no_query)
        if q:
            records = records.filter(q)

    if filter_values["employee"]:
        employee_query = filter_values["employee"]
        q = Q()
        if _has_model_field(model, "full_name"):
            q |= Q(full_name__icontains=employee_query)
        if _has_model_field(model, "employee"):
            q |= Q(employee__full_name__icontains=employee_query)
        if q:
            records = records.filter(q)

    if filter_values["location"]:
        location_query = filter_values["location"]
        q = Q()
        if _has_model_field(model, "location"):
            q |= Q(location__name__icontains=location_query)
        if model_name == "location" and _has_model_field(model, "name"):
            q |= Q(name__icontains=location_query)
        if q:
            records = records.filter(q)

    if filter_values["user"]:
        user_query = filter_values["user"]
        q = Q()
        if _has_model_field(model, "user"):
            q |= Q(user__username__icontains=user_query)
            q |= Q(user__first_name__icontains=user_query)
            q |= Q(user__last_name__icontains=user_query)
            q |= Q(user__email__icontains=user_query)
        if q:
            records = records.filter(q)

    if filter_values["description"]:
        desc_query = filter_values["description"]
        q = Q()
        for field_name in ("description", "details", "body", "subject", "transaction_comment", "biometric_error"):
            if _has_model_field(model, field_name):
                q |= Q(**{f"{field_name}__icontains": desc_query})
        if q:
            records = records.filter(q)

    parsed_date_from = parse_date(filter_values["date_from"]) if filter_values["date_from"] else None
    parsed_date_to = parse_date(filter_values["date_to"]) if filter_values["date_to"] else None
    if parsed_date_from:
        q_from = Q()
        for field in model._meta.fields:
            if field.get_internal_type() == "DateField":
                q_from |= Q(**{f"{field.name}__gte": parsed_date_from})
            elif field.get_internal_type() == "DateTimeField":
                q_from |= Q(**{f"{field.name}__date__gte": parsed_date_from})
        if q_from:
            records = records.filter(q_from)
    if parsed_date_to:
        q_to = Q()
        for field in model._meta.fields:
            if field.get_internal_type() == "DateField":
                q_to |= Q(**{f"{field.name}__lte": parsed_date_to})
            elif field.get_internal_type() == "DateTimeField":
                q_to |= Q(**{f"{field.name}__date__lte": parsed_date_to})
        if q_to:
            records = records.filter(q_to)

    column_name = filter_values["column"]
    column_value = filter_values["column_value"]
    if column_name and column_value:
        try:
            field = model._meta.get_field(column_name)
        except Exception:
            field = None

        if field is not None:
            if field.name == "id" and column_value.isdigit():
                records = records.filter(pk=int(column_value))
            elif field.is_relation and field.many_to_one:
                related_q = Q()
                for rel_name in ("name", "full_name", "username", "code"):
                    try:
                        field.related_model._meta.get_field(rel_name)
                        related_q |= Q(**{f"{field.name}__{rel_name}__icontains": column_value})
                    except Exception:
                        continue
                if related_q:
                    records = records.filter(related_q)
            elif field.get_internal_type() in {"CharField", "TextField", "EmailField"}:
                records = records.filter(**{f"{field.name}__icontains": column_value})
            elif field.get_internal_type() in {"BooleanField", "NullBooleanField"}:
                bool_value = _parse_bool_text(column_value)
                if bool_value is not None:
                    records = records.filter(**{field.name: bool_value})
            elif field.get_internal_type() in {"DateField", "DateTimeField"}:
                parsed = parse_date(column_value)
                if parsed:
                    lookup = f"{field.name}__date" if field.get_internal_type() == "DateTimeField" else field.name
                    records = records.filter(**{lookup: parsed})
            else:
                records = records.filter(**{field.name: column_value})

    if model_name == "employee":
        # Employee filtering is a common case and should always include username fields.
        if filter_values["name"]:
            records = records.filter(
                Q(full_name__icontains=filter_values["name"])
                | Q(user__username__icontains=filter_values["name"])
                | Q(user__first_name__icontains=filter_values["name"])
                | Q(user__last_name__icontains=filter_values["name"])
            )
        if filter_values["user"]:
            records = records.filter(
                Q(user__username__icontains=filter_values["user"])
                | Q(user__first_name__icontains=filter_values["user"])
                | Q(user__last_name__icontains=filter_values["user"])
                | Q(user__email__icontains=filter_values["user"])
            )

    return records.distinct(), filter_values


@staff_member_required(login_url="/admin-login/")
def model_records_view(request, model_name):
    if str(model_name or "").strip().lower() == "mobileactivationrequest":
        qs = request.GET.urlencode()
        target = reverse("activation_requests")
        if qs:
            target = f"{target}?{qs}"
        return redirect(target)
    model = _get_basma_model_or_404(model_name)
    entity = _get_staff_entity_or_403(request)
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    _require_model_privilege_or_403(request, entity, model._meta.model_name, "read")
    can_add = _check_model_privilege(request, entity, model._meta.model_name, "add")
    can_edit = _check_model_privilege(request, entity, model._meta.model_name, "edit")
    if model._meta.model_name in {"attendancetransaction", "audit"} and not request.user.is_superuser:
        can_edit = False
    employee_capacity = None
    if model._meta.model_name == "employee":
        employee_capacity = _get_employee_capacity_state(entity)
        if employee_capacity["at_limit"]:
            can_add = False
    _safe_audit_log(
        request,
        entity,
        page="model_records",
        action="VIEW_LIST",
        model_name=model._meta.model_name,
    )
    fields = [f for f in model._meta.fields]
    if model._meta.model_name == "managerqrcodetoken":
        fields = [f for f in fields if f.name != "require_photo_base64"]
    if model._meta.model_name == "entitysettings":
        fields = [f for f in fields if f.name != "manager_manual_require_photo_base64"]
    supports_soft_toggle = any(f.name == "is_active" for f in fields)
    can_soft_toggle = supports_soft_toggle and (
        model._meta.model_name != "entitysettings" or request.user.is_superuser
    )
    delete_confirm_required = (request.GET.get("delete_confirm_required") or "").strip() == "1"
    if request.method == "POST" and can_soft_toggle and can_edit:
        action = (request.POST.get("action") or "").strip().lower()
        object_id_raw = (request.POST.get("object_id") or "").strip()
        if action in {"deactivate", "restore"} and object_id_raw.isdigit():
            if action == "deactivate":
                confirm_text = (request.POST.get("delete_confirm_text") or "").strip().lower()
                if confirm_text != "confirm":
                    _safe_audit_log(
                        request,
                        entity,
                        page="model_records",
                        action="DELETE_CONFIRM_REQUIRED",
                        model_name=model._meta.model_name,
                        object_id=object_id_raw,
                    )
                    return redirect(f"{reverse('model_records', kwargs={'model_name': model._meta.model_name})}?delete_confirm_required=1")
            target = get_object_or_404(
                _scope_queryset_by_entity(model.objects.all(), entity, current_user=request.user),
                pk=int(object_id_raw),
            )
            target.is_active = action == "restore"
            target.save(update_fields=["is_active"])
            _safe_audit_log(
                request,
                entity,
                page="model_records",
                action="RESTORE_RECORD" if action == "restore" else "SOFT_DELETE_RECORD",
                model_name=model._meta.model_name,
                object_id=target.pk,
                details=f"is_active={target.is_active}",
            )
        return redirect("model_records", model_name=model._meta.model_name)

    records = _scope_queryset_by_entity(model.objects.all(), entity, current_user=request.user).order_by("-pk")
    records, filter_values = _apply_model_records_filters(records, model, request)
    if model._meta.model_name == "entity":
        records = records.select_related("settings")
    if model._meta.model_name == "entitysettings":
        records = records.select_related("entity")
    if model._meta.model_name == "attendancetransaction":
        # Performance: reduce N+1 for FK columns and avoid loading huge base64 blobs unless requested.
        records = records.select_related("entity", "employee", "location")
        records = records.defer("photo_base64").annotate(
            has_photo=Case(
                When(Q(photo_base64__isnull=False) & ~Q(photo_base64__exact=""), then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            )
        )
    if model._meta.model_name == "managerqrcodetoken":
        records = records.select_related("entity", "manager", "location")
    page_size = 50 if model._meta.model_name == "attendancetransaction" else 100
    paginator = Paginator(records, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    page_records = page_obj.object_list
    query_params = request.GET.copy()
    query_params.pop("page", None)
    score_re = re.compile(r"score\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)

    def _extract_liveness_percent_from_error(raw_error):
        text = (raw_error or "").strip()
        if not text.lower().startswith("liveness:"):
            return ""
        match = score_re.search(text)
        if not match:
            return "N/A"
        score_value = float(match.group(1))
        percent_value = score_value * 100.0 if score_value <= 1.5 else score_value
        return f"{percent_value:.1f}%"

    record_rows = []
    field_names_lower = {field.name.lower() for field in fields}
    has_name_filter = any("name" in name for name in field_names_lower) or any(
        _has_model_field(model, rel) for rel in ("employee", "manager", "user")
    )
    has_id_filter = True
    has_date_filter = (
        any("date" in name for name in field_names_lower)
        or any(field.get_internal_type() in ("DateField", "DateTimeField") for field in fields)
    )
    has_civil_id_filter = any(name in {"civil_id", "civilid"} for name in field_names_lower) or _has_model_field(model, "employee")
    has_employee_no_filter = any(name in {"employee_no", "employee_number"} for name in field_names_lower) or _has_model_field(model, "employee")
    has_employee_filter = _has_model_field(model, "employee") or _has_model_field(model, "full_name")
    has_location_filter = _has_model_field(model, "location") or model._meta.model_name == "location"
    has_user_filter = _has_model_field(model, "user")
    has_description_filter = any(name in {"description", "details", "body", "subject"} for name in field_names_lower)
    if model._meta.model_name == "attendancetransaction":
        has_id_filter = False
        has_employee_no_filter = True
        has_date_filter = True
    def _base64_size_text(raw_value):
        raw = (raw_value or "").strip()
        if not raw:
            return ""
        if raw.startswith("data:image/"):
            parts = raw.split(",", 1)
            raw = parts[1] if len(parts) > 1 else ""
        if not raw:
            return ""
        padding = raw.count("=")
        byte_len = max(0, (len(raw) * 3) // 4 - padding)
        return f"{byte_len / 1024:.1f} KB"

    for obj in page_records:
        values = []
        name_parts = []
        id_parts = [str(obj.pk)]
        date_parts = []
        civil_id_parts = []
        employee_no_parts = []
        employee_parts = []
        location_parts = []
        user_parts = []
        description_parts = []
        for field in fields:
            if field.name == "id":
                continue
            field_name_lower = field.name.lower()
            if model._meta.model_name == "attendancetransaction" and field_name_lower == "photo_base64":
                values.append(
                    {
                        "text": "-",
                        "is_image": False,
                        "image_src": "",
                        "image_size": 300,
                        "size_text": "",
                        "liveness_score": "",
                        "is_color": False,
                        "color_hex": "",
                        "is_boolean": False,
                        "boolean_value": False,
                        "field_name": field.name,
                    }
                )
                continue
            raw_value = getattr(obj, field.name, None)
            if raw_value in (None, ""):
                values.append({"text": "-", "is_image": False, "image_src": "", "image_size": 300, "size_text": "", "liveness_score": "", "is_color": False, "color_hex": "", "is_boolean": False, "boolean_value": False, "field_name": field.name})
                continue

            raw_text = str(raw_value).strip()
            is_base64_field = ("base64" in field_name_lower) or (field_name_lower == "logo64")
            is_theme_color_field = field_name_lower in {"theme_color", "secondary_theme_color"}
            if is_base64_field:
                image_size = 100 if field_name_lower == "logo64" else 300
                size_text = _base64_size_text(raw_text)
                if raw_text.startswith("data:image/"):
                    image_src = raw_text
                else:
                    image_src = f"data:image/jpeg;base64,{raw_text}"
                values.append(
                    {
                        "text": "",
                        "is_image": True,
                        "image_src": image_src,
                        "image_size": image_size,
                        "size_text": size_text,
                        "liveness_score": (
                            _extract_liveness_percent_from_error(getattr(obj, "biometric_error", ""))
                            if model._meta.model_name == "attendancetransaction" and field_name_lower == "photo_base64"
                            else ""
                        ),
                        "is_color": False,
                        "color_hex": "",
                        "is_boolean": False,
                        "boolean_value": False,
                        "field_name": field.name,
                    }
                )
            else:
                normalized_color = raw_text
                if is_theme_color_field and normalized_color and not normalized_color.startswith("#"):
                    normalized_color = f"#{normalized_color}"
                valid_color = (
                    is_theme_color_field
                    and len(normalized_color) == 7
                    and normalized_color.startswith("#")
                    and all(ch in "0123456789abcdefABCDEF#" for ch in normalized_color)
                )
                values.append(
                    {
                        "text": raw_text,
                        "is_image": False,
                        "image_src": "",
                        "image_size": 300,
                        "size_text": "",
                        "liveness_score": "",
                        "is_color": valid_color,
                        "color_hex": normalized_color if valid_color else "",
                        "is_boolean": isinstance(raw_value, bool),
                        "boolean_value": bool(raw_value) if isinstance(raw_value, bool) else False,
                        "field_name": field.name,
                    }
                )

            if "name" in field_name_lower:
                has_name_filter = True
                name_parts.append(raw_text.lower())
            if "id" in field_name_lower:
                has_id_filter = True
                id_parts.append(raw_text.lower())
            if (
                "date" in field_name_lower
                or field.get_internal_type() in ("DateField", "DateTimeField")
            ):
                has_date_filter = True
                date_parts.append(raw_text.lower())
                if hasattr(raw_value, "strftime"):
                    date_parts.append(raw_value.strftime("%Y-%m-%d"))
            if field_name_lower in ("civil_id", "civilid"):
                has_civil_id_filter = True
                civil_id_parts.append(raw_text.lower())
            if field_name_lower in ("employee_no", "employee_number"):
                has_employee_no_filter = True
                employee_no_parts.append(raw_text.lower())
            if field_name_lower == "employee":
                has_employee_filter = True
                employee_parts.append(raw_text.lower())
                if getattr(obj, "employee", None):
                    if getattr(obj.employee, "employee_no", None):
                        has_employee_no_filter = True
                        employee_no_parts.append(str(obj.employee.employee_no).lower())
                    if getattr(obj.employee, "civil_id", None):
                        has_civil_id_filter = True
                        civil_id_parts.append(str(obj.employee.civil_id).lower())
            if field_name_lower == "location":
                has_location_filter = True
                location_parts.append(raw_text.lower())
            if field_name_lower == "user":
                has_user_filter = True
                user_parts.append(raw_text.lower())
            if field_name_lower in ("details", "description"):
                has_description_filter = True
                description_parts.append(raw_text.lower())
        entity_logo = None
        manager_qr_payload = ""
        manager_qr_location = ""
        manager_qr_action = ""
        manager_qr_expires_at = ""
        manager_qr_manager_name = ""
        manager_qr_manager_title = ""
        manager_qr_is_expired = False
        manager_qr_live_enabled = False
        manager_qr_live_interval_sec = 10
        if model._meta.model_name == "entity":
            logo64 = ""
            settings_obj = getattr(obj, "settings", None)
            if settings_obj and settings_obj.logo64:
                logo64 = str(settings_obj.logo64).strip()
            if logo64:
                size_text = _base64_size_text(logo64)
                if logo64.startswith("data:image/"):
                    image_src = logo64
                else:
                    image_src = f"data:image/png;base64,{logo64}"
                entity_logo = {
                    "is_image": True,
                    "image_src": image_src,
                    "image_size": 100,
                    "size_text": size_text,
                }
            else:
                entity_logo = {"is_image": False, "text": "-", "image_size": 100, "size_text": ""}
        if model._meta.model_name == "managerqrcodetoken":
            manager_qr_location = (
                str(getattr(getattr(obj, "location", None), "name", "") or "")
            )
            manager_qr_action = str(getattr(obj, "action", "") or "")
            manager_qr_expires_at = (
                obj.expires_at.strftime("%Y-%m-%d %H:%M:%S")
                if getattr(obj, "expires_at", None)
                else ""
            )
            manager_qr_is_expired = bool(
                getattr(obj, "expires_at", None) and obj.expires_at <= timezone.now()
            )
            manager_qr_live_enabled = bool(getattr(obj, "live_rotation_enabled", False))
            manager_qr_live_interval_sec = int(getattr(obj, "live_rotation_interval_sec", 10) or 10)
            manager_qr_manager_name = str(getattr(getattr(obj, "manager", None), "full_name", "") or "")
            manager_qr_manager_title = "Manager"
            if manager_qr_live_enabled:
                manager_qr_payload = ""
            else:
                manager_qr_payload = json.dumps(
                    {
                        "token": str(getattr(obj, "token", "") or ""),
                        "action": str(getattr(obj, "action", "") or ""),
                        "location_id": int(getattr(obj, "location_id", 0) or 0),
                        "manager_id": int(getattr(obj, "manager_id", 0) or 0),
                        "expires_at": (
                            obj.expires_at.isoformat()
                            if getattr(obj, "expires_at", None)
                            else ""
                        ),
                    },
                    ensure_ascii=True,
                )

        record_rows.append(
            {
                "pk": obj.pk,
                "values": values,
                "search_name": " ".join(name_parts),
                "search_id": " ".join(id_parts),
                "search_date": " ".join(date_parts),
                "search_civil_id": " ".join(civil_id_parts),
                "search_employee_no": " ".join(employee_no_parts),
                "search_employee": " ".join(employee_parts),
                "search_location": " ".join(location_parts),
                "search_user": " ".join(user_parts),
                "search_description": " ".join(description_parts),
                "entity_logo": entity_logo,
                "has_photo": bool(getattr(obj, "has_photo", False)) if model._meta.model_name == "attendancetransaction" else False,
                "is_active": getattr(obj, "is_active", None) if supports_soft_toggle else None,
                "license_expire_soon": (
                    model._meta.model_name == "entity"
                    and getattr(obj, "license_expire_date", None)
                    and isinstance(obj.license_expire_date, date)
                    and 0 <= (obj.license_expire_date - timezone.localdate()).days <= 60
                ),
                "entity_license_expire_date": (
                    obj.entity.license_expire_date.strftime("%Y-%m-%d")
                    if model._meta.model_name == "entitysettings"
                    and getattr(obj, "entity", None)
                    and obj.entity.license_expire_date
                    else "-"
                ),
                "manager_qr_payload": manager_qr_payload,
                "manager_qr_location": manager_qr_location,
                "manager_qr_action": manager_qr_action,
                "manager_qr_expires_at": manager_qr_expires_at,
                "manager_qr_manager_name": manager_qr_manager_name,
                "manager_qr_manager_title": manager_qr_manager_title,
                "manager_qr_is_expired": manager_qr_is_expired,
                "manager_qr_live_enabled": manager_qr_live_enabled,
                "manager_qr_live_interval_sec": manager_qr_live_interval_sec,
            }
        )
    return render(
        request,
        "basmaapp/model_records.html",
        {
            "model": model,
            "model_name": model._meta.model_name,
            "model_title": model._meta.verbose_name_plural.title(),
            "record_rows": record_rows,
            "fields": fields,
            "page_obj": page_obj,
            "has_name_filter": has_name_filter,
            "has_id_filter": has_id_filter,
            "has_date_filter": has_date_filter,
            "has_civil_id_filter": has_civil_id_filter,
            "has_employee_no_filter": has_employee_no_filter,
            "has_employee_filter": has_employee_filter,
            "has_location_filter": has_location_filter,
            "has_user_filter": has_user_filter,
            "has_description_filter": has_description_filter,
            "allow_filters": (model._meta.model_name != "entitysettings") or request.user.is_superuser,
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "can_add": can_add,
            "can_edit": can_edit,
            "supports_soft_toggle": supports_soft_toggle,
            "can_soft_toggle": can_soft_toggle,
            "is_super_admin": request.user.is_superuser,
            "employee_capacity": employee_capacity,
            "delete_confirm_required": delete_confirm_required,
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
            "filter_values": filter_values,
            "query_string": query_params.urlencode(),
        },
    )


@staff_member_required(login_url="/admin-login/")
def attendance_transaction_image_view(request, pk):
    entity = _get_staff_entity_or_403(request)
    _require_model_privilege_or_403(request, entity, "attendancetransaction", "read")
    tx = get_object_or_404(
        AttendanceTransaction.objects.filter(entity=entity).only("id", "photo_base64"),
        pk=pk,
    )
    raw = (tx.photo_base64 or "").strip()
    if not raw:
        return JsonResponse({"ok": False, "error": "No image available."}, status=404)
    image_src = raw if raw.startswith("data:image/") else f"data:image/jpeg;base64,{raw}"
    return JsonResponse({"ok": True, "image_src": image_src})


@staff_member_required(login_url="/admin-login/")
def manager_qr_live_payload_view(request, pk):
    entity = _get_staff_entity_or_403(request)
    _require_model_privilege_or_403(request, entity, "managerqrcodetoken", "read")
    qr = get_object_or_404(
        ManagerQRCodeToken.objects.select_related("entity", "manager", "location"),
        pk=pk,
        entity=entity,
    )
    if qr.expires_at and qr.expires_at <= timezone.now():
        return JsonResponse({"ok": False, "error": "Token expired"}, status=410)

    if bool(getattr(qr, "live_rotation_enabled", False)):
        interval = max(1, int(getattr(qr, "live_rotation_interval_sec", 10) or 10))
        step = _manager_qr_live_now_step(interval)
        token_value = _build_manager_qr_live_token(qr, step)
        seconds_left = interval - (int(timezone.now().timestamp()) % interval)
    else:
        token_value = str(getattr(qr, "token", "") or "")
        interval = 0
        step = None
        seconds_left = None

    payload = json.dumps(
        {
            "token": token_value,
            "action": str(getattr(qr, "action", "") or ""),
            "location_id": int(getattr(qr, "location_id", 0) or 0),
            "manager_id": int(getattr(qr, "manager_id", 0) or 0),
            "expires_at": qr.expires_at.isoformat() if qr.expires_at else "",
        },
        ensure_ascii=True,
    )
    return JsonResponse(
        {
            "ok": True,
            "payload": payload,
            "live_rotation": {
                "enabled": bool(getattr(qr, "live_rotation_enabled", False)),
                "interval_sec": interval,
                "step": step,
                "seconds_left": seconds_left,
            },
        },
        status=200,
    )


@staff_member_required(login_url="/admin-login/")
def model_create_view(request, model_name):
    if str(model_name or "").strip().lower() == "mobileactivationrequest":
        return redirect("activation_requests")
    model = _get_basma_model_or_404(model_name)
    # Bootstrap path: allow super admin to create the first Entity even when none exists yet.
    if request.user.is_superuser and model._meta.model_name == "entity":
        try:
            entity = _get_staff_entity_or_403(request)
        except PermissionDenied as exc:
            if str(exc) != "No entity exists in the system.":
                raise
            entity = None
    else:
        entity = _get_staff_entity_or_403(request)
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    add_prefix = {"en": "Add", "ar": "إضافة", "es": "Agregar"}.get(ui_lang, "Add")
    create_label = {"en": "Create", "ar": "إنشاء", "es": "Crear"}.get(ui_lang, "Create")
    _require_model_privilege_or_403(request, entity, model._meta.model_name, "add")
    form_class = modelform_factory(model, fields="__all__")
    employee_capacity = _get_employee_capacity_state(entity) if model._meta.model_name == "employee" else None
    employee_no_preview = (
        (request.POST.get("employee_no") if request.method == "POST" else "") or ""
    ).strip()
    employee_default_username = (
        _build_employee_username(entity, employee_no_preview)
        if model._meta.model_name == "employee" and entity is not None
        else ""
    )
    employee_username_input = (
        (request.POST.get("username") if request.method == "POST" else "") or employee_default_username
    ).strip()
    employee_default_password = (
        _employee_default_password(employee_no_preview)
        if model._meta.model_name == "employee"
        else ""
    )
    notify_team_qr_checked = str(request.POST.get("notify_team_qr", "")).strip().lower() in {"1", "true", "on", "yes"}
    notify_team_message = (request.POST.get("notify_team_message") or "").strip()

    if request.method == "POST":
        post_data = request.POST
        if model._meta.model_name == "entitysettings" and request.FILES.get("logo_file"):
            uploaded_logo = request.FILES.get("logo_file")
            encoded_logo = base64.b64encode(uploaded_logo.read()).decode("ascii")
            post_data = request.POST.copy()
            post_data["logo64"] = encoded_logo
        form = form_class(post_data, request.FILES)
        _restrict_form_to_entity(form, entity, actor_user=request.user)
        if model._meta.model_name == "employee":
            for hidden_field in ("last_updated_UUID_at", "updated_UUID_by", "user"):
                if hidden_field in form.fields:
                    form.fields.pop(hidden_field)
        if model._meta.model_name == "managerqrcodetoken" and "token" in form.fields:
            form.fields.pop("token")
        if model._meta.model_name == "managerqrcodetoken" and "live_secret" in form.fields:
            form.fields.pop("live_secret")
        if model._meta.model_name == "managerqrcodetoken" and "require_photo_base64" in form.fields:
            form.fields.pop("require_photo_base64")
        if model._meta.model_name == "managerqrcodetoken" and "used_at" in form.fields:
            form.fields["used_at"].disabled = True
            form.fields["used_at"].required = False
            form.fields["used_at"].widget.attrs["readonly"] = "readonly"
            form.fields["used_at"].widget.attrs["disabled"] = "disabled"
        _enable_calendar_widgets(form)
        over_capacity = model._meta.model_name == "employee" and employee_capacity and employee_capacity["at_limit"]
        if form.is_valid() and over_capacity:
            form.add_error(
                None,
                f"Employee limit reached: {employee_capacity['current']} / {employee_capacity['max']}.",
            )
        if form.is_valid() and not over_capacity and request.POST.get("confirm_changes") == "1":
            instance = form.save(commit=False)
            if model._meta.model_name == "employee":
                # Enforce tenant boundary: employee is always created in current admin entity.
                instance.entity = entity
                employee_no_value = str(form.cleaned_data.get("employee_no") or "").strip()
                full_name_value = str(form.cleaned_data.get("full_name") or "").strip()
                user_first_name, user_last_name = _split_full_name(full_name_value)
                username_value = (request.POST.get("username") or "").strip()
                if not username_value:
                    username_value = _build_employee_username(entity, employee_no_value)
                if User.objects.filter(username=username_value).exists():
                    form.add_error(None, f"Username '{username_value}' already exists.")
                    _safe_audit_log(
                        request,
                        entity,
                        page="model_create",
                        action="CREATE_FAILED",
                        model_name=model._meta.model_name,
                        details=f"username_exists={username_value}",
                    )
                    return render(
                        request,
                        "basmaapp/model_form.html",
                        {
                            "form": form,
                            "model": model,
                            "model_name": model._meta.model_name,
                            "app_label": model._meta.app_label,
                            "model_title": model._meta.verbose_name.title(),
                            "page_title": f"{add_prefix} {model._meta.verbose_name.title()}",
                            "submit_label": create_label,
                            "show_change_summary": False,
                            "change_summary_items": [],
                            "footer_entity_name": entity.name if entity else "System",
                            "footer_user_name": _get_footer_user_name(request),
                            "employee_capacity": employee_capacity,
                            "ui_lang": ui_lang,
                            "header_logo_src": entity_logo_src,
                            "is_create_mode": True,
                            "notify_team_qr_checked": notify_team_qr_checked,
                            "notify_team_message": notify_team_message,
                            "employee_default_username": employee_default_username,
                            "employee_username_input": username_value,
                            "employee_default_password": employee_default_password,
                        },
                    )
                user = User(
                    username=username_value,
                    first_name=user_first_name,
                    last_name=user_last_name,
                    is_active=True,
                )
                user.set_password(_employee_default_password(employee_no_value))
                user.save()
                instance.user = user
            if model._meta.model_name == "managerqrcodetoken" and not getattr(instance, "token", ""):
                instance.token = secrets.token_urlsafe(32)
            if model._meta.model_name == "managerqrcodetoken":
                if bool(getattr(instance, "live_rotation_enabled", True)) and not str(getattr(instance, "live_secret", "") or "").strip():
                    instance.live_secret = secrets.token_hex(32)
            instance.save()
            if model._meta.model_name == "managerqrcodetoken" and notify_team_qr_checked:
                _notify_manager_team_about_qr(
                    manager=instance.manager,
                    location=instance.location,
                    action=instance.action,
                    expires_at=instance.expires_at,
                    custom_message=notify_team_message,
                )
            if hasattr(form, "save_m2m"):
                form.save_m2m()
            _safe_audit_log(
                request,
                entity,
                page="model_create",
                action="CREATE_RECORD",
                model_name=model._meta.model_name,
                object_id=instance.pk,
            )
            return redirect("model_records", model_name=model._meta.model_name)
        if form.is_valid() and not over_capacity:
            _safe_audit_log(
                request,
                entity,
                page="model_create",
                action="VIEW_CHANGE_SUMMARY",
                model_name=model._meta.model_name,
            )
        else:
            _safe_audit_log(
                request,
                entity,
                page="model_create",
                action="CREATE_FAILED",
                model_name=model._meta.model_name,
                details=str(form.errors),
            )
    else:
        form = form_class()
        _restrict_form_to_entity(form, entity, actor_user=request.user)
        if model._meta.model_name == "employee":
            for hidden_field in ("last_updated_UUID_at", "updated_UUID_by", "user"):
                if hidden_field in form.fields:
                    form.fields.pop(hidden_field)
        if model._meta.model_name == "managerqrcodetoken" and "token" in form.fields:
            form.fields.pop("token")
        if model._meta.model_name == "managerqrcodetoken" and "live_secret" in form.fields:
            form.fields.pop("live_secret")
        if model._meta.model_name == "managerqrcodetoken" and "require_photo_base64" in form.fields:
            form.fields.pop("require_photo_base64")
        if model._meta.model_name == "managerqrcodetoken":
            policy = _manager_manual_signing_policy(entity)
            if "require_biometric" in form.fields:
                form.initial["require_biometric"] = policy["require_biometric"]
            if "require_face_liveness" in form.fields:
                form.initial["require_face_liveness"] = policy["require_face_liveness"]
            if "require_photo_base64" in form.fields:
                form.initial["require_photo_base64"] = policy["require_photo_base64"]
            if "single_use_token" in form.fields:
                form.initial["single_use_token"] = policy["single_use_token"]
            if "require_geofence" in form.fields:
                form.initial["require_geofence"] = policy["require_geofence"]
            if "live_rotation_enabled" in form.fields:
                form.initial["live_rotation_enabled"] = True
            if "live_rotation_interval_sec" in form.fields:
                form.initial["live_rotation_interval_sec"] = 10
            if "live_rotation_grace_steps" in form.fields:
                form.initial["live_rotation_grace_steps"] = 1
        if model._meta.model_name == "managerqrcodetoken" and "used_at" in form.fields:
            form.fields["used_at"].disabled = True
            form.fields["used_at"].required = False
            form.fields["used_at"].widget.attrs["readonly"] = "readonly"
            form.fields["used_at"].widget.attrs["disabled"] = "disabled"
        _enable_calendar_widgets(form)
        if model._meta.model_name == "employee" and employee_capacity and employee_capacity["at_limit"]:
            form.add_error(
                None,
                f"Employee limit reached: {employee_capacity['current']} / {employee_capacity['max']}.",
            )
        _safe_audit_log(
            request,
            entity,
            page="model_create",
            action="VIEW_CREATE_FORM",
            model_name=model._meta.model_name,
        )

    return render(
        request,
        "basmaapp/model_form.html",
        {
            "form": form,
            "model": model,
            "model_name": model._meta.model_name,
            "app_label": model._meta.app_label,
            "model_title": model._meta.verbose_name.title(),
            "page_title": f"{add_prefix} {model._meta.verbose_name.title()}",
            "submit_label": create_label,
            "show_change_summary": request.method == "POST" and form.is_valid() and request.POST.get("confirm_changes") != "1",
            "change_summary_items": [
                {
                    "field": name,
                    "old": "-",
                    "new": str(form.cleaned_data.get(name, "-")),
                }
                for name in form.cleaned_data.keys()
            ] if request.method == "POST" and form.is_valid() and request.POST.get("confirm_changes") != "1" else [],
            "footer_entity_name": entity.name if entity else "System",
            "footer_user_name": _get_footer_user_name(request),
            "employee_capacity": employee_capacity,
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
            "is_create_mode": True,
            "notify_team_qr_checked": notify_team_qr_checked,
            "notify_team_message": notify_team_message,
            "employee_default_username": employee_default_username,
            "employee_username_input": employee_username_input,
            "employee_default_password": employee_default_password,
        },
    )


@staff_member_required(login_url="/admin-login/")
def model_edit_view(request, model_name, pk):
    if str(model_name or "").strip().lower() == "mobileactivationrequest":
        return redirect("activation_requests")
    model = _get_basma_model_or_404(model_name)
    entity = _get_staff_entity_or_403(request)
    ui_lang = _get_ui_language(request)
    entity_logo_src = _get_entity_logo_src(entity)
    edit_prefix = {"en": "Edit", "ar": "تعديل", "es": "Editar"}.get(ui_lang, "Edit")
    update_label = {"en": "Update", "ar": "تحديث", "es": "Actualizar"}.get(ui_lang, "Update")
    _require_model_privilege_or_403(request, entity, model._meta.model_name, "edit")
    if model._meta.model_name in {"attendancetransaction", "audit"} and not request.user.is_superuser:
        raise PermissionDenied("Only super admin can edit this model.")
    instance = get_object_or_404(
        _scope_queryset_by_entity(model.objects.all(), entity, current_user=request.user),
        pk=pk,
    )
    can_edit_license_expire_date = (
        model._meta.model_name == "entitysettings"
        and request.user.is_superuser
        and getattr(instance, "entity", None) is not None
    )
    license_expire_date_value = (
        instance.entity.license_expire_date.strftime("%Y-%m-%d")
        if model._meta.model_name == "entitysettings"
        and getattr(instance, "entity", None) is not None
        and instance.entity.license_expire_date
        else ""
    )
    license_expire_date_raw = ""
    form_class = modelform_factory(model, fields="__all__")

    if request.method == "POST":
        post_data = request.POST
        if model._meta.model_name == "entitysettings" and request.FILES.get("logo_file"):
            uploaded_logo = request.FILES.get("logo_file")
            encoded_logo = base64.b64encode(uploaded_logo.read()).decode("ascii")
            post_data = request.POST.copy()
            post_data["logo64"] = encoded_logo
        form = form_class(post_data, request.FILES, instance=instance)
        _restrict_form_to_entity(form, entity, actor_user=request.user)
        if model._meta.model_name == "employee":
            for hidden_field in ("last_updated_UUID_at", "updated_UUID_by", "user"):
                if hidden_field in form.fields:
                    form.fields.pop(hidden_field)
        if model._meta.model_name == "managerqrcodetoken" and "live_secret" in form.fields:
            form.fields.pop("live_secret")
        if model._meta.model_name == "managerqrcodetoken" and "require_photo_base64" in form.fields:
            form.fields.pop("require_photo_base64")
        if model._meta.model_name == "managerqrcodetoken" and "used_at" in form.fields:
            form.fields["used_at"].disabled = True
            form.fields["used_at"].required = False
            form.fields["used_at"].widget.attrs["readonly"] = "readonly"
            form.fields["used_at"].widget.attrs["disabled"] = "disabled"
        _enable_calendar_widgets(form)
        license_expire_date_new_value = None
        license_expire_date_raw = (request.POST.get("license_expire_date") or "").strip()
        if model._meta.model_name == "entitysettings":
            if can_edit_license_expire_date:
                if license_expire_date_raw:
                    parsed_license_date = parse_date(license_expire_date_raw)
                    if not parsed_license_date:
                        form.add_error(None, "License expire date is invalid.")
                    else:
                        license_expire_date_new_value = parsed_license_date
                else:
                    license_expire_date_new_value = None
            else:
                license_expire_date_new_value = instance.entity.license_expire_date if getattr(instance, "entity", None) else None
        if model._meta.model_name == "entitysettings" and not request.user.is_superuser:
            if "is_active" in form.fields:
                form.fields.pop("is_active")
            if "number_employees" in form.fields:
                form.fields["number_employees"].disabled = True
                form.fields["number_employees"].widget.attrs["disabled"] = "disabled"
            form.instance.is_active = instance.is_active
            form.instance.number_employees = instance.number_employees
        if form.is_valid() and request.POST.get("confirm_changes") == "1":
            changed_fields = []
            for name in form.changed_data:
                old_value = form.initial.get(name, "-")
                new_value = form.cleaned_data.get(name, "-")
                changed_fields.append(
                    {
                        "field": name,
                        "old": str(old_value),
                        "new": str(new_value),
                    }
                )
            if model._meta.model_name == "entitysettings" and can_edit_license_expire_date:
                old_license = instance.entity.license_expire_date if getattr(instance, "entity", None) else None
                if str(old_license or "") != str(license_expire_date_new_value or ""):
                    changed_fields.append(
                        {
                            "field": "license_expire_date",
                            "old": str(old_license or "-"),
                            "new": str(license_expire_date_new_value or "-"),
                        }
                    )
            # Entity updates are applied with queryset.update() to prevent accidental INSERT paths.
            if model._meta.model_name == "entity":
                Entity.objects.filter(pk=instance.pk).update(
                    name=form.cleaned_data.get("name", instance.name),
                    code=form.cleaned_data.get("code", instance.code),
                    license_expire_date=form.cleaned_data.get("license_expire_date"),
                    is_active=form.cleaned_data.get("is_active", instance.is_active),
                )
                updated = Entity.objects.get(pk=instance.pk)
            else:
                # Defensive save path: keep original PK/created_at to guarantee UPDATE (not INSERT).
                updated = form.save(commit=False)
                updated.pk = instance.pk
                if model._meta.model_name == "managerqrcodetoken":
                    existing_secret = str(getattr(instance, "live_secret", "") or "").strip()
                    if bool(getattr(updated, "live_rotation_enabled", False)):
                        updated.live_secret = existing_secret or secrets.token_hex(32)
                    else:
                        updated.live_secret = ""
                if hasattr(updated, "created_at"):
                    existing_created_at = getattr(instance, "created_at", None)
                    updated_created_at = getattr(updated, "created_at", None)
                    # Legacy rows may have NULL created_at in DB; ensure non-null before UPDATE.
                    if updated_created_at is None:
                        updated.created_at = existing_created_at or timezone.now()
                updated.save()
                if hasattr(form, "save_m2m"):
                    form.save_m2m()
            if model._meta.model_name == "entitysettings" and getattr(updated, "entity", None) is not None:
                updated.entity.license_expire_date = license_expire_date_new_value
                updated.entity.save(update_fields=["license_expire_date"])
            _safe_audit_log(
                request,
                entity,
                page="model_edit",
                action="UPDATE_RECORD",
                model_name=model._meta.model_name,
                object_id=updated.pk,
                details=json.dumps({"changes": changed_fields}, ensure_ascii=True),
            )
            return redirect("model_records", model_name=model._meta.model_name)
        if form.is_valid():
            _safe_audit_log(
                request,
                entity,
                page="model_edit",
                action="VIEW_CHANGE_SUMMARY",
                model_name=model._meta.model_name,
                object_id=pk,
            )
        else:
            _safe_audit_log(
                request,
                entity,
                page="model_edit",
                action="UPDATE_FAILED",
                model_name=model._meta.model_name,
                object_id=pk,
                details=str(form.errors),
            )
    else:
        form = form_class(instance=instance)
        _restrict_form_to_entity(form, entity, actor_user=request.user)
        if model._meta.model_name == "employee":
            for hidden_field in ("last_updated_UUID_at", "updated_UUID_by", "user"):
                if hidden_field in form.fields:
                    form.fields.pop(hidden_field)
        if model._meta.model_name == "managerqrcodetoken" and "live_secret" in form.fields:
            form.fields.pop("live_secret")
        if model._meta.model_name == "managerqrcodetoken" and "require_photo_base64" in form.fields:
            form.fields.pop("require_photo_base64")
        if model._meta.model_name == "managerqrcodetoken" and "used_at" in form.fields:
            form.fields["used_at"].disabled = True
            form.fields["used_at"].required = False
            form.fields["used_at"].widget.attrs["readonly"] = "readonly"
            form.fields["used_at"].widget.attrs["disabled"] = "disabled"
        _enable_calendar_widgets(form)
        if model._meta.model_name == "entitysettings" and not request.user.is_superuser:
            if "is_active" in form.fields:
                form.fields.pop("is_active")
            if "number_employees" in form.fields:
                form.fields["number_employees"].disabled = True
                form.fields["number_employees"].widget.attrs["disabled"] = "disabled"
            form.instance.is_active = instance.is_active
            form.instance.number_employees = instance.number_employees
        _safe_audit_log(
            request,
            entity,
            page="model_edit",
            action="VIEW_EDIT_FORM",
            model_name=model._meta.model_name,
            object_id=pk,
        )

    return render(
        request,
        "basmaapp/model_form.html",
        {
            "form": form,
            "model": model,
            "model_name": model._meta.model_name,
            "app_label": model._meta.app_label,
            "model_title": model._meta.verbose_name.title(),
            "page_title": f"{edit_prefix} {model._meta.verbose_name.title()}",
            "submit_label": update_label,
            "show_change_summary": request.method == "POST" and form.is_valid() and request.POST.get("confirm_changes") != "1",
            "change_summary_items": [
                {
                    "field": name,
                    "old": str(form.initial.get(name, "-")),
                    "new": str(form.cleaned_data.get(name, "-")),
                }
                for name in form.cleaned_data.keys()
                if str(form.initial.get(name, "")) != str(form.cleaned_data.get(name, ""))
            ] if request.method == "POST" and form.is_valid() and request.POST.get("confirm_changes") != "1" else [],
            "footer_entity_name": entity.name,
            "footer_user_name": _get_footer_user_name(request),
            "can_edit_license_expire_date": can_edit_license_expire_date,
            "license_expire_date_value": (
                license_expire_date_raw
                if request.method == "POST"
                else license_expire_date_value
            ),
            "ui_lang": ui_lang,
            "header_logo_src": entity_logo_src,
            "is_create_mode": False,
            "notify_team_qr_checked": False,
            "notify_team_message": "",
        },
    )


@staff_member_required(login_url="/admin-login/")
def list_users(request):
    entity = _get_staff_entity_or_403(request)
    users = User.objects.filter(
        employee_profile__entity=entity,
        employee_profile__is_active=True,
    ).values('id', 'username', 'email', 'first_name', 'last_name').distinct()
    return JsonResponse(list(users), safe=False)

class VerifyEmployeeUUIDView(APIView):
    """
    POST /api/employee/verify-uuid/
    Body:
    {
        
        "employee_uuid": "550e8400-e29b-41d4-a716-446655440000",
      
    }
    """

    def post(self, request):
        try:
            body = request.data
           
            employee_uuid = body.get("employee_uuid")
            

            employee = Employee.objects.select_related("user").get(
            employee_uuid=employee_uuid,
                is_active=True,
            )
            _sync_entity_active_by_license(employee.entity)
            if _is_entity_license_expired(employee.entity):
                return Response(
                    {"error": _get_entity_license_error_message(employee.entity)},
                    status=status.HTTP_403_FORBIDDEN,
                )

            if (
                str(employee.employee_uuid) == employee_uuid
               
            ):
                return Response(
                    {
                        "message": "UUIDs match",
                        "user_id": employee.id,
                    },
                    status=status.HTTP_200_OK,
                )
            else:
                return Response(
                    {"error": "UUIDs do not match"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        except Employee.DoesNotExist:
            return Response(
                {"error": "Employee not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except PermissionDenied as exc:
            return Response(
                {"error": str(exc) or "Entity license is not valid."},
                status=status.HTTP_403_FORBIDDEN,
            )


class CheckEmployeeLicenseView(APIView):
    """
    POST /api/employee/check_license/
    Body:
    {
        "employee_uuid": "550e8400-e29b-41d4-a716-446655440000"
    }
    """

    def post(self, request):
        try:
            employee_uuid = (request.data or {}).get("employee_uuid")
            if not employee_uuid:
                return Response(
                    {"error": "employee_uuid is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            employee = Employee.objects.select_related("entity").get(
                employee_uuid=employee_uuid,
                is_active=True,
            )
            _sync_entity_active_by_license(employee.entity)
            expired = _is_entity_license_expired(employee.entity)
            message = _get_entity_license_error_message(employee.entity) if expired else "License is valid."

            return Response(
                {
                    "is_valid": not expired,
                    "message": message,
                    "employee_id": employee.id,
                    "entity_id": employee.entity_id,
                    "license_expire_date": (
                        employee.entity.license_expire_date.strftime("%Y-%m-%d")
                        if employee.entity and employee.entity.license_expire_date
                        else None
                    ),
                },
                status=status.HTTP_200_OK,
            )
        except Employee.DoesNotExist:
            return Response(
                {"error": "Employee not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as exc:
            return Response(
                {"error": str(exc) or "Failed to check entity license."},
                status=status.HTTP_400_BAD_REQUEST,
            )


class UpdateEmployeeUUIDView(APIView):
    """
    POST /api/employee/update-uuid/
    Body:
    {
        "employee_no": 12,
        "employee_uuid": "550e8400-e29b-41d4-a716-446655440000",
        "device_uuid": "550e8400-e29b-41d4-a716-446655440001",
        "by_staff_id": 1  # Optional, if update is done by an admin/staff user
    }
    """

    def post(self, request):
        serializer = UpdateEmployeeUUIDSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        actor_user = None
        actor_employee = None
        by_staff_id = data.get("by_staff_id")
        if by_staff_id:
            actor_employee = Employee.objects.select_related("user", "entity").filter(user_id=by_staff_id).first()
            actor_user = actor_employee.user if actor_employee else User.objects.filter(id=by_staff_id).first()

        employee = None
        try:
            employee = Employee.objects.get(
                employee_no=data["employee_no"],
                is_active=True,
            )
            _sync_entity_active_by_license(employee.entity)
            if _is_entity_license_expired(employee.entity):
                _safe_audit_log(
                    request,
                    employee.entity,
                    page="api_update_uuid",
                    action="API_UPDATE_UUID_DENIED_LICENSE",
                    model_name="employee",
                    object_id=employee.id,
                    details=f"employee_no={employee.employee_no}",
                )
                return Response(
                    {"error": _get_entity_license_error_message(employee.entity)},
                    status=status.HTTP_403_FORBIDDEN,
                )
        except Exception as e:
            fallback_entity = actor_employee.entity if actor_employee else None
            _safe_audit_log(
                request,
                fallback_entity,
                page="api_update_uuid",
                action="API_UPDATE_UUID_FAILED_NOT_FOUND",
                model_name="employee",
                object_id=data.get("employee_no", ""),
                details=f"employee lookup failed: {e.__class__.__name__}",
            )
            return Response(
                {"error": "Employee not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Optional: reset device binding when UUID changes
        employee.employee_uuid = data["employee_uuid"]
        employee.device_uuid = data["device_uuid"]
        employee.device_bound_at = None
        employee.last_updated_UUID_at = timezone.now()
        employee.updated_UUID_by = actor_user

        employee.save(
            update_fields=["employee_uuid", "device_uuid", "device_bound_at", "last_updated_UUID_at", "updated_UUID_by"]
        )

        try:
            Audit.objects.create(
                entity=employee.entity,
                user=actor_user if actor_user else (request.user if request.user.is_authenticated else None),
                page="api_update_uuid",
                action="API_UPDATE_UUID_SUCCESS",
                model_name="employee",
                object_id=str(employee.id),
                details=f"employee_no={employee.employee_no}; by_staff_id={by_staff_id or ''}",
            )
        except Exception:
            pass

        return Response(
            {
                "message": "Employee UUID updated successfully",
                "user_id": employee.user_id,
                "employee_uuid": employee.employee_uuid,
                "device_uuid": employee.device_uuid,
            },
            status=status.HTTP_200_OK,
        )

@csrf_exempt
@require_POST
def load_employee_data(request):

    """
    POST /api/employee/load-data/
    Body:
    {
        "employee_id": "1"
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        if not employee_id:
            return JsonResponse({"error": "employee_id is required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(
            request,
            body,
            "employee_id",
            allow_staff_override=True,
        )
        manual_policy = _manager_manual_signing_policy(employee.entity)
        normal_policy = _normal_signing_policy(employee.entity)

        data = {
            "user_id": employee.user_id,
            "username": employee.user.username,
            "employee_no": employee.employee_no,
            "first_name": employee.user.first_name,
            "last_name": employee.user.last_name,
            "email": employee.user.email,
            "employee_uuid": str(employee.employee_uuid),
            "device_uuid": employee.device_uuid,
            "is_active": employee.is_active,
            "entity_id": employee.entity.id,
            "entity_name": employee.entity.name,
            "civil_id": employee.civil_id,
            "is_admin": employee.user.is_staff,
            "photo_base64": employee.photo_base64,
            "confirm_sign_period_minutes": employee.confirm_sign_period_minutes,
            "number_working_hours_per_day": float(employee.number_working_hours_per_day) if employee.number_working_hours_per_day is not None else None,
            "is_manager": employee.is_manager,
            "employee_parent_id": employee.employee_parent.id if employee.employee_parent else None,
            "manager_manual_require_biometric": manual_policy["require_biometric"],
            "manager_manual_require_face_liveness": manual_policy["require_face_liveness"],
            "manager_manual_require_photo_base64": manual_policy["require_photo_base64"],
            "manager_manual_single_use_token": manual_policy["single_use_token"],
            "manager_manual_require_geofence": manual_policy["require_geofence"],
            "normal_sign_require_biometric": normal_policy["require_biometric"],
            "normal_sign_require_face_liveness": normal_policy["require_face_liveness"],
            "activation_mode": (
                str(getattr(getattr(employee.entity, "settings", None), "activation_mode", "option_1") or "option_1")
                .strip()
                .lower()
            ),
        }

        return JsonResponse(data, status=200)

    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

@csrf_exempt
@require_POST
def load_employee_locations_beacons(request):

    """
    POST /api/employee/employee_locations_beacons/
    Body:
    {
        "employee_id": "1"
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        if not employee_id:
            return JsonResponse({"error": "employee_id is required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(
            request,
            body,
            "employee_id",
            allow_staff_override=True,
        )

        assignments = EmployeeLocationAssignment.objects.filter(
            employee=employee,
            location__is_active=True,
            is_active=True,
            start_date__lte=timezone.now().date(),
            end_date__gte=timezone.now().date(),

        ).select_related("location")

        data = []
        for assignment in assignments:
            loc = assignment.location
            data.append({
                "location_id": loc.id,
                "location_name": loc.name,
                "is_GPS_based": loc.latitude is not None and loc.longitude is not None,
                "latitude": str(loc.latitude) if loc.latitude is not None else None,
                "longitude": str(loc.longitude) if loc.longitude is not None else None,
                "gps_radius_meters": assignment.gps_radius_meters,
                "is_beacon_based": loc.is_beacon_based,
                "beacon_uuid": loc.beacon_uuid,
                "beacon_major": loc.major_value,
                "beacon_minor": loc.minor_value,
                "beacon_rssi_threshold": loc.rssi_threshold,
                "assignment_allow_sign_in": assignment.allow_sign_in,
                "assignment_allow_sign_out": assignment.allow_sign_out,
                "assignment_allow_sign_confirm": assignment.allow_sign_confirm,
                "assignment_period_to_take_action": assignment.period_to_take_action,
            })

        return JsonResponse({"assignments": data}, status=200)

    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    


@csrf_exempt
@require_POST
def load_entity_locations(request):
    """
    POST /api/employee/load_entity_locations/
    Body:
    {
        "employee_id": "1"
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        if not employee_id:
            return JsonResponse({"error": "employee_id is required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")
        locations = Location.objects.filter(
            entity=employee.entity,
            is_active=True
        ).order_by("name")

        data = []
        for loc in locations:
            data.append({
                "location_id": loc.id,
                "location_name": loc.name,
                "is_GPS_based": loc.latitude is not None and loc.longitude is not None,
                "latitude": str(loc.latitude) if loc.latitude is not None else None,
                "longitude": str(loc.longitude) if loc.longitude is not None else None,
                "is_beacon_based": loc.is_beacon_based,
                "beacon_uuid": loc.beacon_uuid,
                "beacon_major": loc.major_value,
                "beacon_minor": loc.minor_value,
                "beacon_rssi_threshold": loc.rssi_threshold,
            })
        return JsonResponse({"locations": data}, status=200)
    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


@csrf_exempt
@require_POST
def assign_employee_location(request):
    """
    POST /api/employee/assign_employee_location/
    Body:
    {
        "employee_id": "12",
        "location_id": "3",
        "by_staff_id": "1"
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        location_id = body.get("location_id")
        if not employee_id or not location_id:
            return JsonResponse({"error": "employee_id and location_id are required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id", allow_staff_override=True)
        location = Location.objects.filter(
            id=location_id,
            entity=employee.entity,
            is_active=True
        ).first()
        if not location:
            return JsonResponse({"error": "Location not found"}, status=404)

        by_staff_id = body.get("by_staff_id")
        allow_sign_in = body.get("allow_sign_in")
        allow_sign_confirm = body.get("allow_sign_confirm")
        allow_sign_out = body.get("allow_sign_out")
        gps_radius_meters = body.get("gps_radius_meters")
        period_to_take_action = body.get("period_to_take_action")

        start_date = timezone.localdate()
        end_date = timezone.localdate() + timedelta(days=36500)

        assignment, _ = EmployeeLocationAssignment.objects.get_or_create(
            entity=employee.entity,
            employee=employee,
            location=location,
            defaults={
                "is_active": True,
                "allow_sign_in": True,
                "allow_sign_confirm": True,
                "allow_sign_out": True,
                "gps_radius_meters": 100,
                "period_to_take_action": 1,
                "start_date": start_date,
                "end_date": end_date,
            },
        )

        assignment.is_active = True
        assignment.allow_sign_in = bool(allow_sign_in) if allow_sign_in is not None else assignment.allow_sign_in
        assignment.allow_sign_confirm = bool(allow_sign_confirm) if allow_sign_confirm is not None else assignment.allow_sign_confirm
        assignment.allow_sign_out = bool(allow_sign_out) if allow_sign_out is not None else assignment.allow_sign_out
        assignment.gps_radius_meters = int(gps_radius_meters) if gps_radius_meters is not None else assignment.gps_radius_meters
        assignment.period_to_take_action = int(period_to_take_action) if period_to_take_action is not None else assignment.period_to_take_action
        if not assignment.start_date:
            assignment.start_date = start_date
        if not assignment.end_date:
            assignment.end_date = end_date
        assignment.save()

        _safe_audit_log(
            request,
            employee.entity,
            page="api_assign_employee_location",
            action="API_ASSIGN_EMPLOYEE_LOCATION",
            model_name="employeelocationassignment",
            object_id=str(assignment.id),
            details=f"employee_id={employee.id}; location_id={location.id}; by_staff_id={by_staff_id or ''}",
        )

        return JsonResponse({
            "message": "Location assigned successfully",
            "assignment_id": assignment.id,
            "employee_id": employee.id,
            "location_id": location.id,
            "is_active": assignment.is_active,
        }, status=200)
    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except ValueError:
        return JsonResponse({"error": "Invalid numeric values"}, status=400)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as exc:
        return JsonResponse({"error": str(exc) or "Failed to assign location."}, status=400)


@csrf_exempt
@require_POST
def remove_employee_location(request):
    """
    POST /api/employee/remove_employee_location/
    Body:
    {
        "employee_id": "12",
        "location_id": "3",
        "by_staff_id": "1"
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        location_id = body.get("location_id")
        by_staff_id = body.get("by_staff_id")
        if not employee_id or not location_id:
            return JsonResponse({"error": "employee_id and location_id are required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id", allow_staff_override=True)
        assignment = EmployeeLocationAssignment.objects.filter(
            entity=employee.entity,
            employee=employee,
            location_id=location_id,
            is_active=True
        ).first()
        if not assignment:
            return JsonResponse({"error": "Active assignment not found"}, status=404)

        assignment.is_active = False
        assignment.save(update_fields=["is_active"])

        _safe_audit_log(
            request,
            employee.entity,
            page="api_remove_employee_location",
            action="API_REMOVE_EMPLOYEE_LOCATION",
            model_name="employeelocationassignment",
            object_id=str(assignment.id),
            details=f"employee_id={employee.id}; location_id={location_id}; by_staff_id={by_staff_id or ''}",
        )

        return JsonResponse({
            "message": "Location removed successfully",
            "employee_id": employee.id,
            "location_id": int(location_id),
        }, status=200)
    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except ValueError:
        return JsonResponse({"error": "Invalid numeric values"}, status=400)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as exc:
        return JsonResponse({"error": str(exc) or "Failed to remove location assignment."}, status=400)


@csrf_exempt
@require_POST
def manager_generate_attendance_qr(request):
    """
    POST /api/employee/manager_generate_attendance_qr/
    Body:
    {
        "manager_id": "5",
        "location_id": "3",
        "action": "SIGN_IN",      # SIGN_IN / SIGN_CONFIRM / SIGN_OUT
        "ttl_seconds": 120,         # optional, range: 30..300
        "notify_team_qr": true,     # optional
        "notify_team_message": "Optional custom message"
    }
    """
    try:
        body = json.loads(request.body)
        manager_id = body.get("manager_id")
        location_id = body.get("location_id")
        action = (body.get("action") or "").strip().upper()
        if not manager_id or not location_id or not action:
            return JsonResponse(
                {"error": "manager_id, location_id and action are required"},
                status=400,
            )
        if action not in {"SIGN_IN", "SIGN_OUT", "SIGN_CONFIRM"}:
            return JsonResponse({"error": "Invalid action"}, status=400)

        manager = _get_authorized_employee_for_mobile_request(request, body, "manager_id")
        if not manager.is_manager:
            return JsonResponse({"error": "Only managers can generate QR tokens"}, status=403)

        location = Location.objects.filter(
            id=location_id,
            entity=manager.entity,
            is_active=True,
        ).first()
        if not location:
            return JsonResponse({"error": "Location not found"}, status=404)

        ttl_seconds_raw = body.get("ttl_seconds", 120)
        live_rotation_enabled, live_rotation_interval_sec, live_rotation_grace_steps = _normalize_live_rotation_options(
            body.get("live_rotation_enabled", True),
            body.get("live_rotation_interval_sec", 10),
            body.get("live_rotation_grace_steps", 1),
        )
        notify_team_qr = str(body.get("notify_team_qr", "")).strip().lower() in {"1", "true", "on", "yes"}
        notify_team_message = (body.get("notify_team_message") or "").strip()
        entity_policy = _manager_manual_signing_policy(manager.entity)
        require_biometric = _parse_bool_input(body.get("require_biometric"), entity_policy["require_biometric"])
        require_face_liveness = _parse_bool_input(body.get("require_face_liveness"), entity_policy["require_face_liveness"])
        require_photo_base64 = _parse_bool_input(body.get("require_photo_base64"), entity_policy["require_photo_base64"])
        single_use_token = _parse_bool_input(body.get("single_use_token"), entity_policy["single_use_token"])
        require_geofence = _parse_bool_input(body.get("require_geofence"), entity_policy["require_geofence"])
        try:
            ttl_seconds = int(ttl_seconds_raw)
        except (TypeError, ValueError):
            return JsonResponse({"error": "ttl_seconds must be numeric"}, status=400)
        ttl_seconds = min(300, max(30, ttl_seconds))

        expires_at = timezone.now() + timedelta(seconds=ttl_seconds)
        token = secrets.token_urlsafe(32)
        qr = ManagerQRCodeToken.objects.create(
            entity=manager.entity,
            manager=manager,
            location=location,
            action=action,
            require_biometric=require_biometric,
            require_face_liveness=require_face_liveness,
            require_photo_base64=require_photo_base64,
            single_use_token=single_use_token,
            require_geofence=require_geofence,
            live_rotation_enabled=live_rotation_enabled,
            live_rotation_interval_sec=live_rotation_interval_sec,
            live_rotation_grace_steps=live_rotation_grace_steps,
            live_secret=secrets.token_hex(32) if live_rotation_enabled else "",
            token=token,
            expires_at=expires_at,
        )
        notified_count = 0
        if notify_team_qr:
            notified_count = _notify_manager_team_about_qr(
                manager=manager,
                location=location,
                action=action,
                expires_at=expires_at,
                custom_message=notify_team_message,
            )

        qr_payload_token = _build_manager_qr_live_token(qr) if live_rotation_enabled else qr.token
        return JsonResponse(
            {
                "message": "Manager QR token generated",
                "token_type": "manager_attendance_qr",
                "server_now": timezone.now().isoformat(),
                "expires_at": expires_at.isoformat(),
                "notified_team_count": notified_count,
                "manual_signing_policy": {
                    "require_biometric": require_biometric,
                    "require_face_liveness": require_face_liveness,
                    "require_photo_base64": require_photo_base64,
                    "single_use_token": single_use_token,
                    "require_geofence": require_geofence,
                },
                "qr_payload": {
                    "token": qr_payload_token,
                    "action": qr.action,
                    "location_id": qr.location_id,
                    "manager_id": qr.manager.user_id,
                    "expires_at": expires_at.isoformat(),
                },
                "live_rotation": {
                    "enabled": live_rotation_enabled,
                    "interval_sec": live_rotation_interval_sec,
                    "grace_steps": live_rotation_grace_steps,
                },
            },
            status=201,
        )
    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Manager not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


def _parse_bool_input(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_int_input(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float_input(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _haversine_distance_m(lat1, lon1, lat2, lon2):
    # Great-circle distance in meters.
    radius_m = 6371000.0
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    d_phi = math.radians(float(lat2) - float(lat1))
    d_lambda = math.radians(float(lon2) - float(lon1))
    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * (math.sin(d_lambda / 2.0) ** 2)
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return radius_m * c


def _normalize_live_rotation_options(enabled, interval_sec, grace_steps):
    is_enabled = _parse_bool_input(enabled, True)
    interval = _parse_int_input(interval_sec, 10)
    grace = _parse_int_input(grace_steps, 1)
    interval = min(60, max(5, interval))
    grace = min(3, max(0, grace))
    return is_enabled, interval, grace


def _ensure_manager_qr_live_secret(qr):
    secret = str(getattr(qr, "live_secret", "") or "").strip()
    if secret:
        return secret
    secret = secrets.token_hex(32)
    qr.live_secret = secret
    qr.save(update_fields=["live_secret"])
    return secret


def _manager_qr_live_now_step(interval_sec):
    safe_interval = max(1, int(interval_sec or 10))
    return int(timezone.now().timestamp()) // safe_interval


def _manager_qr_live_signature(qr, step):
    secret = _ensure_manager_qr_live_secret(qr)
    payload = f"{qr.pk}:{qr.token}:{int(step)}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()[:20]


def _build_manager_qr_live_token(qr, step=None):
    if step is None:
        step = _manager_qr_live_now_step(getattr(qr, "live_rotation_interval_sec", 10))
    sig = _manager_qr_live_signature(qr, step)
    return f"{MANAGER_QR_LIVE_PREFIX}:{qr.token}:{int(step)}:{sig}"


def _resolve_manager_qr_from_token_string(raw_token):
    token_text = str(raw_token or "").strip()
    if token_text.startswith("{") and token_text.endswith("}"):
        try:
            parsed = json.loads(token_text)
            token_text = str(parsed.get("token") or "").strip() or token_text
        except json.JSONDecodeError:
            pass
    if "token=" in token_text and ":" in token_text:
        try:
            parsed_url = urlparse(token_text)
            query_token = parse_qs(parsed_url.query).get("token", [""])[0]
            if query_token:
                token_text = unquote(query_token).strip() or token_text
        except Exception:
            pass
    if not token_text:
        return None, "missing"

    if token_text.startswith(f"{MANAGER_QR_LIVE_PREFIX}:"):
        parts = token_text.split(":")
        if len(parts) != 4:
            return None, "invalid_live_payload"
        _, session_token, step_raw, signature = parts
        if not session_token or not step_raw or not signature:
            return None, "invalid_live_payload"
        try:
            scanned_step = int(step_raw)
        except (TypeError, ValueError):
            return None, "invalid_live_payload"
        qr = (
            ManagerQRCodeToken.objects.select_related("entity", "manager", "location")
            .filter(token=session_token)
            .first()
        )
        if not qr:
            return None, "invalid_token"
        if not bool(getattr(qr, "live_rotation_enabled", False)):
            return None, "invalid_token"

        interval = max(1, int(getattr(qr, "live_rotation_interval_sec", 10) or 10))
        grace_steps = max(0, int(getattr(qr, "live_rotation_grace_steps", 1) or 0))
        current_step = _manager_qr_live_now_step(interval)
        if abs(current_step - scanned_step) > grace_steps:
            return None, "token_expired"

        expected_sig = _manager_qr_live_signature(qr, scanned_step)
        if not hmac.compare_digest(expected_sig, signature):
            return None, "invalid_live_payload"

        return qr, {
            "is_live": True,
            "step": scanned_step,
            "raw": token_text,
        }

    qr = (
        ManagerQRCodeToken.objects.select_related("entity", "manager", "location")
        .filter(token=token_text)
        .first()
    )
    if not qr:
        return None, "invalid_token"
    return qr, {"is_live": False, "step": None, "raw": token_text}


def _manager_manual_signing_policy(entity, qr_token=None):
    settings_obj = getattr(entity, "settings", None)
    if settings_obj is None:
        settings_obj = EntitySettings.objects.filter(entity=entity).only(
            "manager_manual_require_biometric",
            "manager_manual_require_face_liveness",
            "manager_manual_use_liveness_check",
            "manager_manual_use_face_compare",
            "manager_manual_require_photo_base64",
            "manager_manual_single_use_token",
            "manager_manual_require_geofence",
        ).first()
    policy = {
        "require_biometric": bool(
            getattr(settings_obj, "manager_manual_require_biometric", True)
            if settings_obj is not None else True
        ),
        "require_face_liveness": bool(
            getattr(settings_obj, "manager_manual_require_face_liveness", True)
            if settings_obj is not None else True
        ),
        "use_liveness_check": bool(
            getattr(settings_obj, "manager_manual_use_liveness_check", True)
            if settings_obj is not None else True
        ),
        "use_face_compare": bool(
            getattr(settings_obj, "manager_manual_use_face_compare", True)
            if settings_obj is not None else True
        ),
        "require_photo_base64": bool(
            getattr(settings_obj, "manager_manual_require_photo_base64", False)
            if settings_obj is not None else False
        ),
        "single_use_token": bool(
            getattr(settings_obj, "manager_manual_single_use_token", True)
            if settings_obj is not None else True
        ),
        "require_geofence": bool(
            getattr(settings_obj, "manager_manual_require_geofence", True)
            if settings_obj is not None else True
        ),
    }
    if qr_token is not None:
        policy["require_biometric"] = bool(getattr(qr_token, "require_biometric", policy["require_biometric"]))
        policy["require_face_liveness"] = bool(getattr(qr_token, "require_face_liveness", policy["require_face_liveness"]))
        policy["require_photo_base64"] = bool(getattr(qr_token, "require_photo_base64", policy["require_photo_base64"]))
        policy["single_use_token"] = bool(getattr(qr_token, "single_use_token", policy["single_use_token"]))
        policy["require_geofence"] = bool(getattr(qr_token, "require_geofence", policy["require_geofence"]))
    return policy


def _normal_signing_policy(entity):
    settings_obj = getattr(entity, "settings", None)
    if settings_obj is None:
        settings_obj = EntitySettings.objects.filter(entity=entity).only(
            "normal_sign_require_biometric",
            "normal_sign_require_face_liveness",
            "normal_sign_use_liveness_check",
            "normal_sign_use_face_compare",
        ).first()
    return {
        "require_biometric": bool(
            getattr(settings_obj, "normal_sign_require_biometric", True)
            if settings_obj is not None else True
        ),
        "require_face_liveness": bool(
            getattr(settings_obj, "normal_sign_require_face_liveness", True)
            if settings_obj is not None else True
        ),
        "use_liveness_check": bool(
            getattr(settings_obj, "normal_sign_use_liveness_check", True)
            if settings_obj is not None else True
        ),
        "use_face_compare": bool(
            getattr(settings_obj, "normal_sign_use_face_compare", True)
            if settings_obj is not None else True
        ),
    }


def _notify_manager_team_about_qr(manager, location, action, expires_at=None, custom_message=""):
    recipient_ids = list(
        Employee.objects.filter(
            entity=manager.entity,
            employee_parent=manager,
            is_active=True,
        ).values_list("id", flat=True)
    )
    if not recipient_ids:
        return 0

    expires_text = (
        timezone.localtime(expires_at).strftime("%Y-%m-%d %H:%M:%S")
        if expires_at else "-"
    )
    action_label = str(action or "").strip().upper() or "SIGN_IN"
    location_name = str(getattr(location, "name", "") or "-")
    subject = "Manual Signing QR Available"
    auto_message = (
        f"A manager QR for manual signing is available now.\n"
        f"Action: {action_label}\n"
        f"Location: {location_name}\n"
        f"Expires at: {expires_text}"
    )
    body = auto_message
    if custom_message:
        body = f"{custom_message.strip()}\n\n{auto_message}"

    InboxMessage.objects.bulk_create(
        [
            InboxMessage(
                entity=manager.entity,
                employee_id=employee_id,
                subject=subject,
                body=body,
                is_read=False,
                is_deleted=False,
            )
            for employee_id in recipient_ids
        ],
        batch_size=500,
    )
    return len(recipient_ids)


@csrf_exempt
@require_POST
def check_manager_qr_token(request):
    """
    POST /api/employee/check_manager_qr_token/
    Body:
    {
        "employee_id": "12",
        "token": "...."
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        token_input = body.get("token")
        if not employee_id or not str(token_input or "").strip():
            return JsonResponse({"error": "employee_id and token are required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")
        qr, resolved = _resolve_manager_qr_from_token_string(token_input)
        if not qr:
            if resolved == "token_expired":
                return JsonResponse({"error": "Token expired"}, status=410)
            if resolved == "invalid_live_payload":
                return JsonResponse({"error": "Invalid manager QR payload"}, status=400)
            if resolved == "missing":
                return JsonResponse({"error": "employee_id and token are required"}, status=400)
            return JsonResponse({"error": "Invalid token"}, status=404)

        _sync_entity_active_by_license(qr.entity)
        if _is_entity_license_expired(qr.entity):
            return JsonResponse({"error": _get_entity_license_error_message(qr.entity)}, status=403)
        if qr.entity_id != employee.entity_id:
            return JsonResponse({"error": "Token entity mismatch"}, status=403)
        if employee.employee_parent_id != qr.manager_id:
            return JsonResponse({"error": "Manager-employee relation is not valid"}, status=403)
        policy = _manager_manual_signing_policy(qr.entity, qr)
        if policy["single_use_token"] and qr.used_at is not None:
            return JsonResponse({"error": "Token already used"}, status=409)
        if qr.expires_at <= timezone.now():
            return JsonResponse({"error": "Token expired"}, status=410)

        assignment = _get_active_assignment_for_employee_location(employee, qr.location)
        if not assignment:
            return JsonResponse({"error": "Employee is not assigned to token location"}, status=403)
        if not _is_assignment_action_allowed(assignment, qr.action):
            return JsonResponse({"error": f"{qr.action} is not allowed for this assignment"}, status=403)

        nonce_preview = qr.token
        if isinstance(resolved, dict) and resolved.get("is_live"):
            nonce_preview = f"{MANAGER_QR_LIVE_PREFIX}:{qr.pk}:{employee.user_id}:{int(resolved.get('step') or 0)}"
            if AttendanceTransaction.objects.filter(
                entity=employee.entity,
                employee=employee,
                request_nonce=nonce_preview,
            ).exists():
                return JsonResponse({"error": "Token already used for current rotation"}, status=409)

        return JsonResponse(
            {
                "ok": True,
                "message": "Token is valid",
                "action": qr.action,
                "location_id": qr.location_id,
                "location_name": qr.location.name if qr.location_id else "",
                "expires_at": qr.expires_at.isoformat() if qr.expires_at else None,
                "manager_id": qr.manager.user_id if qr.manager_id else None,
                "manual_signing_policy": policy,
                "live_rotation": {
                    "enabled": bool(getattr(qr, "live_rotation_enabled", False)),
                    "interval_sec": int(getattr(qr, "live_rotation_interval_sec", 10) or 10),
                    "grace_steps": int(getattr(qr, "live_rotation_grace_steps", 1) or 1),
                    "step": int(resolved.get("step")) if isinstance(resolved, dict) and resolved.get("is_live") else None,
                },
            },
            status=200,
        )
    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


@csrf_exempt
@require_POST
def post_attendance_transaction_by_manager_qr(request):
    """
    POST /api/employee/post_attendance_transaction_by_manager_qr/
    Body:
    {
        "employee_id": "12",
        "token": "....",
        "photo_base64": "data:image/jpeg;base64,...", # optional
        "device_id": "device-xyz",                    # optional
        "latitude": 24.7136,                          # optional
        "longitude": 46.6753,                         # optional
        "gps_accuracy_m": 10,                         # optional
        "transaction_comment": "optional"
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        token_input = str(body.get("token") or "").strip()
        biometric_verified_raw = body.get("biometric_verified_client", False)
        if isinstance(biometric_verified_raw, bool):
            biometric_verified_client = biometric_verified_raw
        elif isinstance(biometric_verified_raw, (int, float)):
            biometric_verified_client = int(biometric_verified_raw) != 0
        else:
            biometric_verified_client = str(biometric_verified_raw).strip().lower() in {"1", "true", "yes", "y"}
        if not employee_id or not token_input:
            return JsonResponse({"error": "employee_id and token are required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")
        pre_qr, pre_resolved = _resolve_manager_qr_from_token_string(token_input)
        if not pre_qr:
            if pre_resolved == "token_expired":
                return JsonResponse({"error": "Token expired"}, status=410)
            if pre_resolved == "invalid_live_payload":
                return JsonResponse({"error": "Invalid manager QR payload"}, status=400)
            return JsonResponse({"error": "Invalid token"}, status=404)
        scanned_step = int(pre_resolved.get("step")) if isinstance(pre_resolved, dict) and pre_resolved.get("is_live") else None

        with transaction.atomic():
            qr = (
                ManagerQRCodeToken.objects.select_for_update()
                .select_related("entity", "manager", "location")
                .filter(pk=pre_qr.pk)
                .first()
            )
            if not qr:
                return JsonResponse({"error": "Invalid token"}, status=404)

            if scanned_step is not None:
                live_token_now = _build_manager_qr_live_token(qr, scanned_step)
                if not hmac.compare_digest(token_input, live_token_now):
                    return JsonResponse({"error": "Invalid manager QR payload"}, status=400)
                interval = max(1, int(getattr(qr, "live_rotation_interval_sec", 10) or 10))
                grace_steps = max(0, int(getattr(qr, "live_rotation_grace_steps", 1) or 0))
                current_step = _manager_qr_live_now_step(interval)
                if abs(current_step - scanned_step) > grace_steps:
                    return JsonResponse({"error": "Token expired"}, status=410)

            _sync_entity_active_by_license(qr.entity)
            if _is_entity_license_expired(qr.entity):
                return JsonResponse({"error": _get_entity_license_error_message(qr.entity)}, status=403)

            if qr.entity_id != employee.entity_id:
                return JsonResponse({"error": "Token entity mismatch"}, status=403)
            if employee.employee_parent_id != qr.manager_id:
                return JsonResponse({"error": "Manager-employee relation is not valid"}, status=403)
            policy = _manager_manual_signing_policy(qr.entity, qr)

            now_dt = timezone.now()
            if policy["single_use_token"] and qr.used_at is not None:
                return JsonResponse({"error": "Token already used"}, status=409)
            if qr.expires_at <= now_dt:
                return JsonResponse({"error": "Token expired"}, status=410)

            assignment = _get_active_assignment_for_employee_location(employee, qr.location)
            if not assignment:
                return JsonResponse({"error": "Employee is not assigned to token location"}, status=403)
            if not _is_assignment_action_allowed(assignment, qr.action):
                return JsonResponse({"error": f"{qr.action} is not allowed for this assignment"}, status=403)
            if policy["require_geofence"]:
                req_lat = _parse_float_input(body.get("latitude"))
                req_lon = _parse_float_input(body.get("longitude"))
                if req_lat is None or req_lon is None:
                    return JsonResponse(
                        {"error": "location_required", "message": "latitude and longitude are required"},
                        status=400,
                    )
                loc_lat = _parse_float_input(getattr(qr.location, "latitude", None))
                loc_lon = _parse_float_input(getattr(qr.location, "longitude", None))
                if loc_lat is None or loc_lon is None:
                    return JsonResponse(
                        {
                            "error": "token_location_missing_coordinates",
                            "message": "Token location has no GPS coordinates configured",
                        },
                        status=400,
                    )
                allowed_radius_m = int(getattr(assignment, "gps_radius_meters", 0) or 0)
                if allowed_radius_m <= 0:
                    return JsonResponse(
                        {
                            "error": "assignment_radius_invalid",
                            "message": "Assigned GPS radius is not configured",
                        },
                        status=400,
                    )
                distance_m = _haversine_distance_m(req_lat, req_lon, loc_lat, loc_lon)
                if distance_m > float(allowed_radius_m):
                    return JsonResponse(
                        {
                            "error": "outside_allowed_radius",
                            "distance_m": round(distance_m, 2),
                            "allowed_radius_m": allowed_radius_m,
                        },
                        status=403,
                    )

            photo_base64 = (body.get("photo_base64") or "").strip()
            if policy["require_photo_base64"] and not photo_base64:
                return JsonResponse({"error": "photo_base64 is required"}, status=400)
            biometric_client_error = ""
            if policy["require_biometric"] and not biometric_verified_client:
                biometric_client_error = "Biometric verification failed on device"

            if policy["require_face_liveness"] and (
                policy.get("use_liveness_check", True) or policy.get("use_face_compare", True)
            ):
                if not photo_base64:
                    return JsonResponse({"error": "Face capture is required"}, status=400)
                employee_photo_base64 = employee.photo_base64 or ""
                biometric_verify, biometric_method, biometric_error = compare_faces_with_api(
                    photo_base64,
                    employee_photo_base64,
                    use_liveness=policy.get("use_liveness_check", True),
                    use_compare=policy.get("use_face_compare", True),
                )
                if biometric_verify != "PASSED":
                    biometric_verify = "PENDING"
                    biometric_error = biometric_error or "Face liveness/compare verification failed"
            else:
                biometric_verify = "PASSED" if biometric_verified_client else "PENDING"
                biometric_method = "MANAGER_QR_MANUAL"
                biometric_error = ""
            if biometric_client_error:
                biometric_verify = "PENDING"
                biometric_error = "; ".join(part for part in [biometric_error, biometric_client_error] if part)

            transaction_comment = (body.get("transaction_comment") or "").strip()
            manager_audit_comment = f"manager_qr by manager_user_id={qr.manager.user_id}"
            if transaction_comment:
                transaction_comment = f"{manager_audit_comment}; {transaction_comment}"
            else:
                transaction_comment = manager_audit_comment

            request_nonce = qr.token
            if scanned_step is not None:
                request_nonce = f"{MANAGER_QR_LIVE_PREFIX}:{qr.pk}:{employee.user_id}:{scanned_step}"
                already_used_step = AttendanceTransaction.objects.filter(
                    entity=employee.entity,
                    employee=employee,
                    request_nonce=request_nonce,
                ).exists()
                if already_used_step:
                    return JsonResponse({"error": "Token already used for current rotation"}, status=409)

            tx = AttendanceTransaction.objects.create(
                entity=employee.entity,
                employee=employee,
                action=qr.action,
                location=qr.location,
                device_id=body.get("device_id"),
                photo_base64=photo_base64,
                biometric_verify=biometric_verify,
                biometric_method=biometric_method,
                biometric_error=biometric_error,
                beacon_uuid=body.get("beacon_uuid", ""),
                beacon_major=body.get("beacon_major"),
                beacon_minor=body.get("beacon_minor"),
                beacon_rssi=body.get("beacon_rssi"),
                gps_latitude=body.get("latitude"),
                gps_longitude=body.get("longitude"),
                gps_accuracy_m=body.get("gps_accuracy_m"),
                request_nonce=request_nonce,
                transaction_comment=transaction_comment,
            )

            if policy["single_use_token"]:
                qr.used_at = now_dt
                qr.save(update_fields=["used_at"])

        return JsonResponse(
            {
                "message": "Attendance transaction recorded by manager QR",
                "transaction_id": tx.id,
                "action": tx.action,
                "occurred_at": tx.occurred_at.isoformat() if tx.occurred_at else None,
                "location_id": tx.location_id,
                "manager_id": qr.manager.user_id,
                "biometric_verify": tx.biometric_verify,
                "biometric_error": tx.biometric_error,
                "manual_signing_policy": policy,
            },
            status=201,
        )
    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


@csrf_exempt
@require_POST
def post_employee_attendance_transactions(request):

    """
    POST /api/employee/post_attendance_transaction/
    Body:
    {
        "employee_id": "1",
        "action": "SIGN_IN"  # or "SIGN_OUT" or "SIGN_CONFIRM",
        "location_id": "2" (optional),
        "photo_base64": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD..." (optional),
        "beacon_device_id": "3" (optional),
        "device_id": "device-xyz" (optional),
        "gps_latitude": 24.7136 (optional),
        "gps_longitude": 46.6753 (optional),
        "gps_accuracy_m": 10 (optional)

    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        action = body.get("action")
        if not employee_id or not action:
            return JsonResponse({"error": "employee_id and action are required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")

        if action not in ["SIGN_IN", "SIGN_OUT", "SIGN_CONFIRM"]:
            return JsonResponse({"error": "Invalid action"}, status=400)

        biometric_verified_raw = body.get("biometric_verified_client", False)
        if isinstance(biometric_verified_raw, bool):
            biometric_verified_client = biometric_verified_raw
        elif isinstance(biometric_verified_raw, (int, float)):
            biometric_verified_client = int(biometric_verified_raw) != 0
        else:
            biometric_verified_client = str(biometric_verified_raw).strip().lower() in {"1", "true", "yes", "y"}

        normal_policy = _normal_signing_policy(employee.entity)
        photo_base64 = (body.get("photo_base64", "") or "").strip()
        biometric_client_error = ""
        if normal_policy["require_biometric"] and not biometric_verified_client:
            biometric_client_error = "Biometric verification failed on device"

        if normal_policy["require_face_liveness"] and (
            normal_policy.get("use_liveness_check", True) or normal_policy.get("use_face_compare", True)
        ):
            if not photo_base64:
                return JsonResponse({"error": "Face capture is required"}, status=400)
            employee_photo_base64 = employee.photo_base64 or ""
            biometric_verify, biometric_method, biometric_error = compare_faces_with_api(
                photo_base64,
                employee_photo_base64,
                use_liveness=normal_policy.get("use_liveness_check", True),
                use_compare=normal_policy.get("use_face_compare", True),
            )
            if biometric_verify != "PASSED":
                biometric_verify = "PENDING"
                biometric_error = biometric_error or "Face liveness/compare verification failed"
        else:
            biometric_verify = "PASSED" if (normal_policy["require_biometric"] and biometric_verified_client) else "PENDING"
            biometric_method = "MOBILE_BIOMETRIC" if normal_policy["require_biometric"] else "NONE"
            biometric_error = ""
        if biometric_client_error:
            biometric_verify = "PENDING"
            biometric_error = "; ".join(part for part in [biometric_error, biometric_client_error] if part)

       
        transaction = AttendanceTransaction.objects.create(
            entity=employee.entity,
            employee=employee,
            action=action,
            location_id=body.get("location_id"),
            device_id=body.get("device_id"),
            occurred_at=timezone.now(),
            photo_base64=photo_base64,
            biometric_verify=biometric_verify,
            biometric_method=biometric_method,
            biometric_error=biometric_error,
            beacon_uuid=body.get("beacon_uuid", ""),
            beacon_major=body.get("beacon_major"),
            beacon_minor=body.get("beacon_minor"),
            beacon_rssi=body.get("beacon_rssi"),
            gps_latitude=body.get("latitude"),
            gps_longitude=body.get("longitude"),
            gps_accuracy_m=body.get("gps_accuracy_m"),
            transaction_comment=body.get("transaction_comment", ""),

        )

        return JsonResponse({
            "message": "Attendance transaction recorded",
            "transaction_id": transaction.id,
            "action": transaction.action,
            "occurred_at": transaction.occurred_at.isoformat() if transaction.occurred_at else None,
            "biometric_verify": transaction.biometric_verify,
            "biometric_error": transaction.biometric_error,
        }, status=201)

    except Employee.DoesNotExist:
        print("Employee not found:", employee_id)
        return JsonResponse({"error": "Employee not found"}, status=404)
    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except json.JSONDecodeError:
        print("Invalid JSON:", request.body)
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    

@csrf_exempt
@require_POST
def confirm_attendance_transaction_recorded(request):

    """
    POST /api/employee/confirm_attendance_transaction_recorded/
    Body:
    {
        "transaction_id": "1"
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        transaction_id = body.get("transaction_id")
        if not employee_id or not transaction_id:
            return JsonResponse({"error": "employee_id and transaction_id are required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")

        transaction = AttendanceTransaction.objects.get(
            id=transaction_id,
            employee=employee,
        )
        _sync_entity_active_by_license(transaction.entity)
        if _is_entity_license_expired(transaction.entity):
            return JsonResponse({"error": _get_entity_license_error_message(transaction.entity)}, status=403)

            
        return JsonResponse({
            "message": "Attendance transaction exists",
            "transaction_id": transaction.id,
            "action": transaction.action,
            "occurred_at": transaction.occurred_at.isoformat(),
        }, status=200)

    except AttendanceTransaction.DoesNotExist:
        return JsonResponse({"error": "Attendance transaction not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    




@csrf_exempt
@require_POST
def load_recents_transactions(request):

    """
    POST /api/employee/load_recents_transactions/
    Body:
    {
        "employee_id": "1"
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        if not employee_id:
            return JsonResponse({"error": "employee_id is required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")

        transactions = AttendanceTransaction.objects.filter(
            employee=employee,
        ).order_by('-occurred_at')[:10]

        data = []
        confirm_minutes = int(employee.confirm_sign_period_minutes or 0)
        try:
            working_hours = float(employee.number_working_hours_per_day)
        except (TypeError, ValueError):
            working_hours = 0.0
        for transaction in transactions:
            next_confirm_time = None
            next_sign_out_time = None
            sign_confirm_time = None
            sign_out_time = None
            if transaction.action == "SIGN_IN" and transaction.occurred_at:
                next_confirm_time = (
                    transaction.occurred_at + timedelta(minutes=confirm_minutes)
                ).isoformat()
                if working_hours > 0:
                    next_sign_out_time = (
                        transaction.occurred_at + timedelta(hours=working_hours)
                    ).isoformat()

                sign_confirm_tx = (
                    AttendanceTransaction.objects.filter(
                        employee=employee,
                        action="SIGN_CONFIRM",
                        occurred_at__date=transaction.occurred_at.date(),
                        occurred_at__gte=transaction.occurred_at,
                    )
                    .order_by("occurred_at")
                    .first()
                )
                if sign_confirm_tx and sign_confirm_tx.occurred_at:
                    sign_confirm_time = sign_confirm_tx.occurred_at.isoformat()

                sign_out_tx = (
                    AttendanceTransaction.objects.filter(
                        employee=employee,
                        action="SIGN_OUT",
                        occurred_at__date=transaction.occurred_at.date(),
                        occurred_at__gte=transaction.occurred_at,
                    )
                    .order_by("occurred_at")
                    .first()
                )
                if sign_out_tx and sign_out_tx.occurred_at:
                    sign_out_time = sign_out_tx.occurred_at.isoformat()
            data.append({
                "transaction_id": transaction.id,
                "action": transaction.action,
                "occurred_at": transaction.occurred_at.isoformat(),
                "next_confirm_time": next_confirm_time,
                "next_sign_out_time": next_sign_out_time,
                "sign_confirm_time": sign_confirm_time,
                "sign_out_time": sign_out_time,
                "location": transaction.location.name if transaction.location else None,
                "biometric_verify": transaction.biometric_verify,
                "status":"Success", # means the transaction record is created, but the actual sign-in/out confirmation depends on the biometric_verify result and other business rules you may have
                "biometric_error": transaction.biometric_error,
                "transaction_comment": transaction.transaction_comment,
            })

        return JsonResponse({"transactions": data}, status=200)

    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    


@csrf_exempt
@require_POST
def load_transactions_by_date(request):

    """
    POST /api/employee/load_transactions_by_date/
    Body:
    {
        "employee_id": "1",
        "date_from": "2024-06-15",
        "date_to": "2024-06-16"
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        date_from_str = body.get("date_from")
        date_to_str = body.get("date_to")
        if not employee_id or not date_from_str or not date_to_str:
            return JsonResponse({"error": "employee_id, date_from and date_to are required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")

        date_from = timezone.datetime.strptime(date_from_str, "%Y-%m-%d").date()
        date_to = timezone.datetime.strptime(date_to_str, "%Y-%m-%d").date()
        start_datetime = timezone.make_aware(timezone.datetime.combine(date_from, timezone.datetime.min.time()))
        end_datetime = timezone.make_aware(timezone.datetime.combine(date_to, timezone.datetime.max.time()))
        transactions = AttendanceTransaction.objects.filter(
            employee=employee,
            occurred_at__range=(start_datetime, end_datetime),
        ).order_by('occurred_at')

        data = []
        for transaction in transactions:
            data.append({
                "transaction_id": transaction.id,
                "action": transaction.action,
                "occurred_at": transaction.occurred_at.isoformat(),
                "location": transaction.location.name if transaction.location else None,
            })

        return JsonResponse({"transactions": data}, status=200)

    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    







@csrf_exempt
@require_POST
def load_today_timeline(request):
    """
    POST /api/employee/load_today_timeline/
    Body:
    {
        "employee_id": "1"
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        if not employee_id:
            return JsonResponse({"error": "employee_id is required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")
        now_dt = timezone.now()
        today = timezone.localdate(now_dt)

        sign_in_tx = (
            AttendanceTransaction.objects.filter(
                employee=employee,
                action="SIGN_IN",
                occurred_at__date=today,
            )
            .order_by("occurred_at")
            .first()
        )

        sign_in_time = None
        expected_confirm_time = None
        sign_confirm_time = None
        expected_sign_out_time = None
        sign_out_time = None

        if sign_in_tx and sign_in_tx.occurred_at:
            sign_in_time = sign_in_tx.occurred_at.isoformat()
            expected_confirm_time = (
                sign_in_tx.occurred_at
                + timedelta(minutes=int(employee.confirm_sign_period_minutes or 0))
            ).isoformat()
            try:
                working_hours = float(employee.number_working_hours_per_day)
            except (TypeError, ValueError):
                working_hours = 0.0
            if working_hours > 0:
                expected_sign_out_time = (
                    sign_in_tx.occurred_at + timedelta(hours=working_hours)
                ).isoformat()

            sign_confirm_tx = (
                AttendanceTransaction.objects.filter(
                    employee=employee,
                    action="SIGN_CONFIRM",
                    occurred_at__date=today,
                    occurred_at__gte=sign_in_tx.occurred_at,
                )
                .order_by("occurred_at")
                .first()
            )
            if sign_confirm_tx and sign_confirm_tx.occurred_at:
                sign_confirm_time = sign_confirm_tx.occurred_at.isoformat()

            sign_out_tx = (
                AttendanceTransaction.objects.filter(
                    employee=employee,
                    action="SIGN_OUT",
                    occurred_at__date=today,
                    occurred_at__gte=sign_in_tx.occurred_at,
                )
                .order_by("occurred_at")
                .first()
            )
            if sign_out_tx and sign_out_tx.occurred_at:
                sign_out_time = sign_out_tx.occurred_at.isoformat()

        return JsonResponse(
            {
                "server_now": now_dt.isoformat(),
                "timeline": {
                    "SIGN_IN": sign_in_time,
                    "SIGN_CONFIRM": sign_confirm_time,
                    "SIGN_OUT": sign_out_time,
                    "EXPECTED_CONFIRM": expected_confirm_time,
                    "EXPECTED_SIGN_OUT": expected_sign_out_time,
                    "sign_in_time": sign_in_time,
                    "expected_confirm_time": expected_confirm_time,
                    "sign_confirm_time": sign_confirm_time,
                    "expected_sign_out_time": expected_sign_out_time,
                    "sign_out_time": sign_out_time,
                },
            },
            status=200,
        )
    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


@csrf_exempt
@require_POST
def load_employees_entity(request):

    """
    POST /api/employee/load_employees_entity/
    Body:
    {
        "entity_id": "1"
    }
    """
    try:
        body = json.loads(request.body)
        entity_id_raw = body.get("entity_id")
        if not entity_id_raw:
            return JsonResponse({"error": "entity_id is required"}, status=400)

        if request.user.is_authenticated and request.user.is_staff:
            entity_obj = _get_staff_entity_or_403(request)
            entity_id = entity_obj.pk
        else:
            entity_obj = Entity.objects.filter(pk=entity_id_raw).first()
            if entity_obj is None:
                return JsonResponse({"error": "Entity not found"}, status=404)
            entity_id = entity_obj.pk
            _sync_entity_active_by_license(entity_obj)
            if _is_entity_license_expired(entity_obj):
                return JsonResponse({"error": _get_entity_license_error_message(entity_obj)}, status=403)

        employees = Employee.objects.select_related("entity", "user").filter(
            entity_id=entity_id,
            is_active=True,
        )
        data = []
        for employee in employees:
            data.append({
                "employee_id": employee.user_id,
                "employee_no": employee.employee_no,
                "first_name": employee.user.first_name,
                "last_name": employee.user.last_name,
                "entity_id": employee.entity.id,
                "entity_name": employee.entity.name,
                "employee_civil_id": employee.civil_id,
            })
        return JsonResponse({"employees": data}, status=200)


    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    

@csrf_exempt
@require_POST
def search_employees(request):
    """
    POST /api/employee/search_employees/
    Body:
    {
        "entity_id": "1",
        "employee_name": "ali",   # optional
        "employee_id": "12",      # optional (employee.id or user_id)
        "civil_id": "123",        # optional
        "employee_no": "E10",     # optional
        "limit": 100              # optional
    }
    """
    try:
        body = json.loads(request.body)
        entity_id_raw = body.get("entity_id")
        if not entity_id_raw:
            return JsonResponse({"error": "entity_id is required"}, status=400)

        employee_name = (body.get("employee_name") or "").strip()
        employee_id_raw = str(body.get("employee_id") or "").strip()
        civil_id = (body.get("civil_id") or "").strip()
        employee_no = (body.get("employee_no") or "").strip()
        by_staff_id_raw = str(body.get("by_staff_id") or "").strip()
        include_inactive_raw = str(body.get("include_inactive") or "").strip().lower()
        include_inactive = include_inactive_raw in {"1", "true", "yes", "y", "on"}
        if not any([employee_name, employee_id_raw, civil_id, employee_no]):
            return JsonResponse(
                {"error": "Provide at least one filter: employee_name, employee_id, civil_id, or employee_no"},
                status=400,
            )

        limit_raw = body.get("limit", 100)
        try:
            limit = max(1, min(int(limit_raw), 500))
        except (TypeError, ValueError):
            return JsonResponse({"error": "limit must be a valid number"}, status=400)

        if request.user.is_authenticated and request.user.is_staff:
            entity_obj = _get_staff_entity_or_403(request)
            entity_id = entity_obj.pk
        else:
            entity_obj = Entity.objects.filter(pk=entity_id_raw).first()
            if entity_obj is None:
                return JsonResponse({"error": "Entity not found"}, status=404)
            entity_id = entity_obj.pk
            _sync_entity_active_by_license(entity_obj)
            if _is_entity_license_expired(entity_obj):
                return JsonResponse({"error": _get_entity_license_error_message(entity_obj)}, status=403)

        allow_inactive = False
        if request.user.is_authenticated and request.user.is_staff:
            allow_inactive = True
        elif include_inactive and by_staff_id_raw.isdigit():
            actor = (
                Employee.objects.select_related("user")
                .filter(user_id=int(by_staff_id_raw), entity_id=entity_id, is_active=True)
                .first()
            )
            allow_inactive = bool(actor and actor.user and actor.user.is_staff)

        employees = Employee.objects.select_related("user").filter(entity_id=entity_id)
        if not allow_inactive:
            employees = employees.filter(is_active=True)
        if employee_name:
            employees = employees.filter(
                Q(full_name__icontains=employee_name)
                | Q(user__first_name__icontains=employee_name)
                | Q(user__last_name__icontains=employee_name)
            )
        if employee_id_raw:
            if employee_id_raw.isdigit():
                employee_id_int = int(employee_id_raw)
                employees = employees.filter(Q(id=employee_id_int) | Q(user_id=employee_id_int))
            else:
                return JsonResponse({"error": "employee_id must be numeric"}, status=400)
        if civil_id:
            employees = employees.filter(civil_id__icontains=civil_id)
        if employee_no:
            employees = employees.filter(employee_no__icontains=employee_no)

        data = []
        for employee in employees.order_by("full_name")[:limit]:
            data.append(
                {
                    "employee_id": employee.id,
                    "user_id": employee.user_id,
                    "name": employee.full_name,
                    "civil_id": employee.civil_id,
                    "employee_no": employee.employee_no,
                }
            )

        return JsonResponse({"employees": data}, status=200)

    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


@csrf_exempt
@require_POST
def list_mobile_activation_requests(request):
    """
    POST /api/employee/list_activation_requests/
    Body:
    {
        "by_staff_id": "3",
        "status": "PENDING",  # optional
        "limit": 100          # optional
    }
    """
    try:
        body = json.loads(request.body)
        by_staff_id_raw = str(body.get("by_staff_id") or "").strip()
        if not by_staff_id_raw or not by_staff_id_raw.isdigit():
            return JsonResponse({"error": "by_staff_id is required and must be numeric"}, status=400)

        actor = (
            Employee.objects.select_related("user", "entity")
            .filter(is_active=True)
            .filter(Q(id=int(by_staff_id_raw)) | Q(user_id=int(by_staff_id_raw)))
            .first()
        )
        if not actor or not actor.user or not actor.user.is_staff:
            return JsonResponse({"error": "Only staff can view activation requests"}, status=403)

        _sync_entity_active_by_license(actor.entity)
        if _is_entity_license_expired(actor.entity):
            return JsonResponse({"error": _get_entity_license_error_message(actor.entity)}, status=403)

        status_filter = str(body.get("status") or "").strip().upper()
        limit_raw = body.get("limit", 100)
        try:
            limit = max(1, min(int(limit_raw), 500))
        except (TypeError, ValueError):
            return JsonResponse({"error": "limit must be a valid number"}, status=400)

        qs = (
            MobileActivationRequest.objects.select_related("employee", "employee__user", "decided_by")
            .filter(entity=actor.entity)
            .order_by(
                Case(
                    When(status=MobileActivationRequest.STATUS_PENDING, then=Value(0)),
                    default=Value(1),
                ),
                "-requested_at",
            )
        )
        if status_filter in {
            MobileActivationRequest.STATUS_PENDING,
            MobileActivationRequest.STATUS_APPROVED,
            MobileActivationRequest.STATUS_REJECTED,
        }:
            qs = qs.filter(status=status_filter)

        data = []
        for req_obj in qs[:limit]:
            data.append(
                {
                    "request_id": req_obj.id,
                    "status": req_obj.status,
                    "employee_id": req_obj.employee_id,
                    "employee_user_id": req_obj.employee.user_id if req_obj.employee_id else None,
                    "employee_name": req_obj.employee.full_name if req_obj.employee_id else "",
                    "employee_no": req_obj.employee.employee_no if req_obj.employee_id else "",
                    "requested_identifier": req_obj.requested_identifier,
                    "lookup_field": req_obj.lookup_field,
                    "activation_mode": req_obj.activation_mode,
                    "requested_device_uuid": req_obj.requested_device_uuid,
                    "requested_at": req_obj.requested_at.isoformat() if req_obj.requested_at else "",
                    "decided_at": req_obj.decided_at.isoformat() if req_obj.decided_at else "",
                    "decided_by": req_obj.decided_by.username if req_obj.decided_by else "",
                    "decision_note": req_obj.decision_note or "",
                }
            )

        pending_count = _pending_activation_requests_count(actor.entity)
        return JsonResponse(
            {
                "requests": data,
                "pending_count": pending_count,
            },
            status=200,
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


@csrf_exempt
@require_POST
def decide_mobile_activation_request(request):
    """
    POST /api/employee/decide_activation_request/
    Body:
    {
        "by_staff_id": "3",
        "request_id": "12",
        "decision": "activate|reject"
    }
    """
    try:
        body = json.loads(request.body)
        by_staff_id_raw = str(body.get("by_staff_id") or "").strip()
        request_id_raw = str(body.get("request_id") or "").strip()
        decision = str(body.get("decision") or "").strip().lower()
        if not by_staff_id_raw or not request_id_raw:
            return JsonResponse({"error": "by_staff_id and request_id are required"}, status=400)
        if not by_staff_id_raw.isdigit() or not request_id_raw.isdigit():
            return JsonResponse({"error": "by_staff_id and request_id must be numeric"}, status=400)
        if decision not in {"activate", "reject"}:
            return JsonResponse({"error": "decision must be activate or reject"}, status=400)

        actor = (
            Employee.objects.select_related("user", "entity")
            .filter(is_active=True)
            .filter(Q(id=int(by_staff_id_raw)) | Q(user_id=int(by_staff_id_raw)))
            .first()
        )
        if not actor or not actor.user or not actor.user.is_staff:
            return JsonResponse({"error": "Only staff can decide activation requests"}, status=403)

        req_obj = (
            MobileActivationRequest.objects.select_related("employee", "entity")
            .filter(pk=int(request_id_raw), entity=actor.entity)
            .first()
        )
        if not req_obj:
            return JsonResponse({"error": "Activation request not found"}, status=404)
        if req_obj.status != MobileActivationRequest.STATUS_PENDING:
            return JsonResponse({"error": "Activation request already decided"}, status=409)

        now_dt = timezone.now()
        if decision == "activate":
            req_obj.employee.is_active = True
            req_obj.employee.save(update_fields=["is_active"])
            req_obj.status = MobileActivationRequest.STATUS_APPROVED
            req_obj.decision_note = "Approved via mobile API"
            audit_action = "APPROVE_ACTIVATION_REQUEST_MOBILE"
        else:
            req_obj.status = MobileActivationRequest.STATUS_REJECTED
            req_obj.decision_note = "Rejected via mobile API"
            audit_action = "REJECT_ACTIVATION_REQUEST_MOBILE"

        req_obj.decided_at = now_dt
        req_obj.decided_by = actor.user
        req_obj.save(update_fields=["status", "decided_at", "decided_by", "decision_note", "updated_at"])

        _safe_audit_log(
            request,
            actor.entity,
            page="mobile_activation_requests",
            action=audit_action,
            model_name="mobileactivationrequest",
            object_id=req_obj.id,
            details=f"employee_id={req_obj.employee_id}; by_staff_user_id={actor.user_id}",
        )
        return JsonResponse(
            {
                "message": "Activation request updated successfully",
                "request_id": req_obj.id,
                "status": req_obj.status,
                "employee_id": req_obj.employee_id,
            },
            status=200,
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


@csrf_exempt
@require_POST
def inbox_messages(request):

    """
    POST /api/employee/inbox_messages/
    Body:
    {
        "employee_id": "1"
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        limit = body.get("limit")
        if not limit:
            limit = 50
        if not employee_id:
            return JsonResponse({"error": "employee_id is required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")

        # For demonstration, returning static messages
        messages = InboxMessage.objects.filter(
            employee=employee,
            is_deleted=False,
        ).order_by('-id')[:limit]

        data = []
        for msg in messages:
            data.append({
                "message_id": msg.id,
                "subject": msg.subject,
                "body": msg.body,
                "sent_at": msg.created_at.isoformat(),
                "is_read": msg.is_read,
            })

        return JsonResponse({"messages": data}, status=200)

    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    

@csrf_exempt
@require_POST
def search_inbox_messages(request):
    """
    POST /api/employee/search_inbox_messages/
    Body:
    {
        "employee_id": "1",
        "date_from": "2026-02-01",  # optional (YYYY-MM-DD)
        "date_to": "2026-02-29",    # optional (YYYY-MM-DD)
        "content_contains": "policy",  # optional
        "limit": 50                 # optional
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        if not employee_id:
            return JsonResponse({"error": "employee_id is required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")

        limit = body.get("limit")
        if not limit:
            limit = 50
        try:
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            return JsonResponse({"error": "limit must be a valid number"}, status=400)

        date_from_raw = (body.get("date_from") or "").strip()
        date_to_raw = (body.get("date_to") or "").strip()
        content_contains = (body.get("content_contains") or "").strip()

        date_from = parse_date(date_from_raw) if date_from_raw else None
        date_to = parse_date(date_to_raw) if date_to_raw else None
        if date_from_raw and date_from is None:
            return JsonResponse({"error": "date_from must be YYYY-MM-DD"}, status=400)
        if date_to_raw and date_to is None:
            return JsonResponse({"error": "date_to must be YYYY-MM-DD"}, status=400)

        messages = InboxMessage.objects.filter(
            employee=employee,
            is_deleted=False,
        )
        if date_from:
            messages = messages.filter(created_at__date__gte=date_from)
        if date_to:
            messages = messages.filter(created_at__date__lte=date_to)
        if content_contains:
            messages = messages.filter(Q(subject__icontains=content_contains) | Q(body__icontains=content_contains))

        messages = messages.order_by("-id")[:limit]

        data = []
        for msg in messages:
            data.append({
                "message_id": msg.id,
                "subject": msg.subject,
                "body": msg.body,
                "sent_at": msg.created_at.isoformat(),
                "is_read": msg.is_read,
            })

        return JsonResponse({"messages": data}, status=200)

    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


@csrf_exempt
@require_POST
def set_message_read(request):

    """
    POST /api/employee/set_message_read/
    Body:
    {
        "employee_id": "1",
        "message_id": "10"
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        message_id = body.get("message_id")
        if not employee_id or not message_id:
            return JsonResponse({"error": "employee_id and message_id are required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")

        message = InboxMessage.objects.get(
            id=message_id,
            employee=employee,
        )

        message.is_read = True
        message.save(update_fields=["is_read"])

        return JsonResponse({"message": "Message marked as read"}, status=200)

    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except InboxMessage.DoesNotExist:
        return JsonResponse({"error": "Message not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    

@csrf_exempt
@require_POST
def update_user_photo(request):

    """
    POST /api/employee/update_user_photo/
    Body:
    {
        "employee_id": "1",
        "photo_base64": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD..." 
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        photo_base64 = body.get("photo_base64")
        if not employee_id or not photo_base64:
            return JsonResponse({"error": "employee_id and photo_base64 are required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")

        employee.photo_base64 = photo_base64
        employee.save(update_fields=["photo_base64"])

        return JsonResponse({"message": "User photo updated"}, status=200)

    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


@csrf_exempt
@require_POST
def user_entity(request):

    """
    POST /api/employee/user_entity/
    Body:
    {
        "employee_id": "1"
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        if not employee_id:
            return JsonResponse({"error": "employee_id is required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")

        entity_settings = EntitySettings.objects.filter(entity=employee.entity).first()
       
        data = {
            "entity_id": employee.entity.id,
            "entity_display_name": entity_settings.display_name if entity_settings else "",
            "entity_theme_color": entity_settings.theme_color if entity_settings else "",
            "entity_logo_url": entity_settings.logo64 if entity_settings else "",
            "number_employees": entity_settings.number_employees if entity_settings else None,
            "is_active": entity_settings.is_active if entity_settings else False,
            "activation_mode": (
                str(getattr(entity_settings, "activation_mode", "option_1") or "option_1")
                .strip()
                .lower()
            ) if entity_settings else "option_1",

           
        }

        return JsonResponse(data, status=200)

    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


@csrf_exempt
@require_POST
def load_employee_for_parent(request):

    """
    POST /api/employee/load_employee_for_parent/
    Body:
    {
        "employee_id": "1"  # parent employee id
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        if not employee_id:
            return JsonResponse({"error": "employee_id is required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")

        data = {
            "user_id": employee.user_id,
            "username": employee.user.username,
            "employee_no": employee.employee_no,
            "first_name": employee.user.first_name,
            "last_name": employee.user.last_name,
            "email": employee.user.email,
            "employee_uuid": str(employee.employee_uuid),
            "device_uuid": employee.device_uuid,
            "is_active": employee.is_active,
            "entity_id": employee.entity.id,
            "entity_name": employee.entity.name,
        }

        return JsonResponse(data, status=200)

    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)


@csrf_exempt
@require_POST
def post_location(request):
    """
    POST /api/employee/post_location/
    Body:
    {
        "employee_id": "1",
        "name": "HQ",
        "description": "Main office",
        "latitude": 29.3759,
        "longitude": 47.9774,
        "is_GPS_based": true,
        "is_beacon_based": false,
        "major_value": 1,
        "minor_value": 2,
        "rssi_threshold": -70,
        "beacon_uuid": "....",
        "is_active": true
    }
    """
    try:
        body = json.loads(request.body)
        employee_id = body.get("employee_id")
        name = (body.get("name") or "").strip()
        if not employee_id or not name:
            return JsonResponse({"error": "employee_id and name are required"}, status=400)

        employee = _get_authorized_employee_for_mobile_request(request, body, "employee_id")
        if not employee.user.is_staff:
            return JsonResponse({"error": "Only staff can create locations"}, status=403)

        def _to_bool(value, default=False):
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "y", "on"}
            return default

        location = Location.objects.create(
            entity=employee.entity,
            name=name,
            description=(body.get("description") or "").strip(),
            latitude=body.get("latitude"),
            longitude=body.get("longitude"),
            is_GPS_based=_to_bool(body.get("is_GPS_based"), True),
            is_beacon_based=_to_bool(body.get("is_beacon_based"), False),
            major_value=body.get("major_value"),
            minor_value=body.get("minor_value"),
            rssi_threshold=body.get("rssi_threshold"),
            beacon_uuid=(body.get("beacon_uuid") or "").strip(),
            is_active=_to_bool(body.get("is_active"), True),
        )

        return JsonResponse(
            {
                "message": "Location created",
                "location_id": location.id,
                "entity_id": location.entity_id,
                "name": location.name,
                "is_active": location.is_active,
            },
            status=201,
        )

    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc) or "Entity license is not valid."}, status=403)
    except Employee.DoesNotExist:
        return JsonResponse({"error": "Employee not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
