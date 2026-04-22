from django.urls import path
from . import views

urlpatterns = [
    path("basma", views.basma, name="index"),
    path("list_users", views.list_users, name="list_users"),
    path("employee/verify-uuid/", views.VerifyEmployeeUUIDView.as_view()),
    path("employee/check_license/", views.CheckEmployeeLicenseView.as_view()),
    path("employee/update-uuid/", views.UpdateEmployeeUUIDView.as_view()),
    path("employee/start_activation/", views.start_employee_activation, name="start_employee_activation"),
    path("employee/activate_employee_by_staff/", views.activate_employee_by_staff, name="activate_employee_by_staff"),
    path("employee/load-data/", views.load_employee_data, name="load_employee_data"),
    path("employee/load_entity_locations/", views.load_entity_locations, name="load_entity_locations"),
    path("employee/employee_locations_beacons/", views.load_employee_locations_beacons, name="load_employee_locations_beacons"),
    path("employee/assign_employee_location/", views.assign_employee_location, name="assign_employee_location"),
    path("employee/remove_employee_location/", views.remove_employee_location, name="remove_employee_location"),
    path("employee/manager_generate_attendance_qr/", views.manager_generate_attendance_qr, name="manager_generate_attendance_qr"),
    path("employee/check_manager_qr_token/", views.check_manager_qr_token, name="check_manager_qr_token"),
    path("employee/post_attendance_transaction_by_manager_qr/", views.post_attendance_transaction_by_manager_qr, name="post_attendance_transaction_by_manager_qr"),
    path("employee/post_attendance_transaction/", views.post_employee_attendance_transactions, name="post_employee_attendance_transactions"),
    path("employee/confirm_attendance_transaction_recorded/", views.confirm_attendance_transaction_recorded, name="confirm_attendance_transaction_recorded"),
    path("employee/load_recents_transactions/", views.load_recents_transactions, name="load_recents_transactions"),
    path("employee/load_today_timeline/", views.load_today_timeline, name="load_today_timeline"),
    path("employee/load_transactions_by_date/", views.load_transactions_by_date, name="load_transactions_by_date"),
    path("employee/load_employees_entity/", views.load_employees_entity, name="load_employees_entity"),
    path("employee/search_employees/", views.search_employees, name="search_employees"),
    path("employee/list_activation_requests/", views.list_mobile_activation_requests, name="list_mobile_activation_requests"),
    path("employee/decide_activation_request/", views.decide_mobile_activation_request, name="decide_mobile_activation_request"),
    path("employee/inbox_messages/", views.inbox_messages, name="inbox_messages"),
    path("employee/search_inbox_messages/", views.search_inbox_messages, name="search_inbox_messages"),
    path("employee/set_message_read/", views.set_message_read, name="set_message_read"),
    path("employee/update_user_photo/", views.update_user_photo, name="update_user_photo"),
    path("employee/user_entity/", views.user_entity, name="user_entity"),
    path("employee/load_employee_for_parent/", views.load_employee_for_parent, name="load_employee_for_parent"),
    path("employee/post_location/", views.post_location, name="post_location"),














]
