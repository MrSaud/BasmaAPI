from __future__ import annotations

import re

from django.core.exceptions import ObjectDoesNotExist

from .models import Employee, Entity

_DEFAULT_THEME_COLOR = "#0284c7"
_DEFAULT_THEME_COLOR_DARK = "#0369a1"
_HEX_COLOR_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")
_SAFE_FONT_RE = re.compile(r"^[a-zA-Z0-9\s,\-_'\"().]{1,120}$")


def _normalize_hex_color(value: str) -> str:
    raw = (value or "").strip()
    if not raw or not _HEX_COLOR_RE.match(raw):
        return ""
    if not raw.startswith("#"):
        raw = f"#{raw}"
    return raw.lower()


def _darken_hex_color(color: str, factor: float = 0.2) -> str:
    normalized = _normalize_hex_color(color)
    if not normalized:
        return _DEFAULT_THEME_COLOR_DARK

    r = int(normalized[1:3], 16)
    g = int(normalized[3:5], 16)
    b = int(normalized[5:7], 16)

    factor = max(0.0, min(0.85, factor))
    r = int(r * (1.0 - factor))
    g = int(g * (1.0 - factor))
    b = int(b * (1.0 - factor))
    return f"#{r:02x}{g:02x}{b:02x}"


def _sanitize_font_name(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if not _SAFE_FONT_RE.match(raw):
        return ""
    return raw


def _get_current_admin_entity(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_staff:
        return None

    if user.is_superuser:
        selected_entity_id = request.session.get("admin_selected_entity_id")
        if selected_entity_id:
            entity = Entity.objects.select_related("settings").filter(pk=selected_entity_id).first()
            if entity:
                return entity

        employee_profile = (
            Employee.objects.filter(user=user, is_active=True)
            .select_related("entity__settings")
            .first()
        )
        if employee_profile and employee_profile.entity:
            return employee_profile.entity

        return Entity.objects.select_related("settings").order_by("name", "id").first()

    employee_profile = (
        Employee.objects.filter(user=user, is_active=True)
        .select_related("entity__settings")
        .first()
    )
    if employee_profile and employee_profile.entity:
        return employee_profile.entity
    return None


def _build_initials(value: str) -> str:
    words = [w for w in re.split(r"\s+", (value or "").strip()) if w]
    if not words:
        return "AT"
    if len(words) == 1:
        word = words[0]
        letters = "".join(ch for ch in word if ch.isalnum())
        return (letters[:2] or "AT").upper()
    first = "".join(ch for ch in words[0] if ch.isalnum())
    second = "".join(ch for ch in words[1] if ch.isalnum())
    token = (first[:1] + second[:1]).upper()
    return token or "AT"


def admin_theme(request):
    entity = _get_current_admin_entity(request)
    theme_color = ""
    secondary_theme_color = ""
    entity_display_name = ""
    entity_code = ""
    font_family = ""
    if entity:
        entity_display_name = (entity.name or "").strip()
        entity_code = (entity.code or "").strip()
        try:
            theme_color = _normalize_hex_color(entity.settings.theme_color)
            secondary_theme_color = _normalize_hex_color(getattr(entity.settings, "secondary_theme_color", ""))
            font_family = _sanitize_font_name(getattr(entity.settings, "font_family", ""))
            display_name = (entity.settings.display_name or "").strip()
            if display_name:
                entity_display_name = display_name
        except ObjectDoesNotExist:
            theme_color = ""
            secondary_theme_color = ""

    if not theme_color:
        theme_color = _DEFAULT_THEME_COLOR
    if not secondary_theme_color:
        secondary_theme_color = _darken_hex_color(theme_color, factor=0.1)

    return {
        "admin_theme_color": theme_color,
        "admin_theme_color_dark": _darken_hex_color(theme_color),
        "admin_theme_color_secondary": secondary_theme_color,
        "admin_entity_display_name": entity_display_name,
        "admin_entity_code": entity_code,
        "admin_entity_initials": _build_initials(entity_display_name),
        "admin_font_family": font_family,
    }
