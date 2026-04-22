"""att URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from basmaapp import views as basma_views

urlpatterns = [
    path("", basma_views.root_redirect_view, name="root_redirect"),
    path("set-language/", basma_views.set_ui_language, name="set_ui_language"),
    path("admin-login/", basma_views.admin_login_view, name="admin_login"),
    path("admin-logout/", basma_views.admin_logout_view, name="admin_logout"),
    path("admin-home/", basma_views.admin_home_view, name="admin_home"),
    path("admin-home/select-entity/", basma_views.set_admin_entity_view, name="set_admin_entity"),
    path("admin-home/dashboard/", basma_views.admin_dashboard_view, name="admin_dashboard"),
    path("admin-home/privileges-matrix/", basma_views.privilege_matrix_view, name="privilege_matrix"),
    path("admin-home/realtime-alerts/", basma_views.realtime_alerts_view, name="realtime_alerts"),
    path("admin-home/activation-requests/", basma_views.activation_requests_view, name="activation_requests"),
    path("admin-home/data-import/", basma_views.data_import_view, name="data_import"),
    path("admin-home/user-accounts/", basma_views.super_admin_user_accounts_view, name="super_admin_user_accounts"),
    path("admin-home/audit-insights/", basma_views.audit_insights_view, name="audit_insights"),
    path("admin-home/audit-insights/<int:audit_id>/diff/", basma_views.audit_diff_view, name="audit_diff"),
    path("admin-home/audit-insights/export.csv", basma_views.audit_export_csv_view, name="audit_export_csv"),
    path("admin-home/audit-insights/export.pdf", basma_views.audit_export_pdf_view, name="audit_export_pdf"),
    path("admin-home/attendance-exceptions/", basma_views.attendance_exceptions_view, name="attendance_exceptions"),
    path("admin-home/reports/", basma_views.reports_home_view, name="reports_home"),
    path(
        "admin-home/reports/employee-location-assignments/preview/",
        basma_views.report_employee_location_assignments_preview,
        name="report_employee_location_assignments_preview",
    ),
    path(
        "admin-home/reports/employee-location-assignments.pdf",
        basma_views.report_employee_location_assignments_pdf,
        name="report_employee_location_assignments_pdf",
    ),
    path(
        "admin-home/reports/locations/preview/",
        basma_views.report_locations_preview,
        name="report_locations_preview",
    ),
    path(
        "admin-home/reports/locations.pdf",
        basma_views.report_locations_pdf,
        name="report_locations_pdf",
    ),
    path(
        "admin-home/reports/<str:report_key>/preview/",
        basma_views.report_advanced_preview,
        name="report_advanced_preview",
    ),
    path(
        "admin-home/reports/<str:report_key>.pdf",
        basma_views.report_advanced_pdf,
        name="report_advanced_pdf",
    ),
    path(
        "admin-home/transactions/<int:pk>/image/",
        basma_views.attendance_transaction_image_view,
        name="attendance_transaction_image",
    ),
    path(
        "admin-home/manager-qr/<int:pk>/live-payload/",
        basma_views.manager_qr_live_payload_view,
        name="manager_qr_live_payload",
    ),
    path("admin-home/models/<str:model_name>/", basma_views.model_records_view, name="model_records"),
    path("admin-home/models/<str:model_name>/new/", basma_views.model_create_view, name="model_create"),
    path("admin-home/models/<str:model_name>/<int:pk>/edit/", basma_views.model_edit_view, name="model_edit"),
    path('admin/', admin.site.urls),
    path("api/", include("basmaapp.urls")),
]

handler400 = "basmaapp.views.custom_400"
handler403 = "basmaapp.views.custom_403"
handler404 = "basmaapp.views.custom_404"
handler500 = "basmaapp.views.custom_500"
