# BasmaAPI (AttendanceSaaS)

Django REST API for attendance, employee mobile flows, locations, manager QR signing, and admin tooling.

## Base URL

API routes are mounted at:

```text
http(s)://<host>/api/
```

(Project: `path("api/", include("basmaapp.urls"))` in `att/urls.py`.)

## Request format

- Prefer **`Content-Type: application/json`** on POST bodies unless otherwise noted.
- Function views use `@csrf_exempt`; mobile clients typically call these without CSRF cookies.

---

## Authentication and identity (mobile)

Many endpoints resolve the acting employee via `_get_authorized_employee_for_mobile_request`:

1. **`employee_id`** — Required in the JSON body for most routes (see each endpoint). It may be either **`Employee.id`** or **`Employee.user_id`** (numeric string).
2. **`employee_uuid`** — For callers who are **not** logged in as Django **`is_staff`**, send the employee UUID in the body **`employee_uuid`** or header **`X-Employee-UUID`**. Missing UUID → `403` with **`identity_credentials_required`**.
3. UUID must match the employee → otherwise **`identity_mismatch`**.
4. **`device_uuid`** — If the employee record has a non-empty **`device_uuid`**, the client must send **`device_uuid`** or header **`X-Device-UUID`**, matching the stored value; missing → **`device_uuid_required`**, wrong → **`device_mismatch`**.
5. **`by_staff_id`** — Some endpoints set **`allow_staff_override=True`**: a manager or staff member in the **same entity** can supply **`by_staff_id`** (their employee id or user id) to act on behalf of another employee without the mobile UUID flow.

Staff users authenticated with a normal Django session skip the UUID/device checks when calling protected endpoints.

---

## Endpoints summary

Paths below are relative to **`/api/`**.

| Method | Path | Required body fields | Optional / notes |
|--------|------|---------------------|------------------|
| GET | `basma` | — | Health check |
| GET | `list_users` | — | **Staff session** required; lists users for the selected admin entity |
| POST | `employee/verify-uuid/` | `employee_uuid` | DRF (`request.data`). Response `user_id` is **`Employee.id`** (not Django `User.id`). When **`store_review_mode`** is on, UUID matching is skipped — see [Store review mode](#store-review-mode) |
| GET | `superadmin/app-global-settings/` | — | **Django superuser** only. Returns deployment-wide app flags |
| PATCH | `superadmin/app-global-settings/` | at least one of `store_review_mode`, `store_review_user_id` | **Django superuser** only. Updates `AppGlobalSettings` singleton |
| POST | `employee/check_license/` | `employee_uuid` | License / entity info |
| POST | `employee/update-uuid/` | `employee_no`, `employee_uuid` | `device_uuid`, `by_staff_id` optional (DRF serializer) |
| POST | `employee/start_activation/` | `identifier` | e.g. `ENTITYCODE-IDENTIFIER`; `device_uuid` used for `option_2` flows |
| POST | `employee/activate_employee_by_staff/` | `employee_id`, `by_staff_id` | `request_id`; actor must be staff |
| POST | `employee/load-data/` | `employee_id` | `by_staff_id` for manager/staff override (`allow_staff_override`) |
| POST | `employee/load_entity_locations/` | `employee_id` | All active entity locations |
| POST | `employee/employee_locations_beacons/` | `employee_id` | Employee’s assigned locations (with GPS/beacon and permissions) |
| POST | `employee/assign_employee_location/` | `employee_id`, `location_id` | `by_staff_id`, `allow_sign_in`, `allow_sign_confirm`, `allow_sign_out`, `gps_radius_meters`, `period_to_take_action` |
| POST | `employee/remove_employee_location/` | `employee_id`, `location_id` | `by_staff_id` (audit) |
| POST | `employee/manager_generate_attendance_qr/` | `manager_id`, `location_id`, `action` | `action`: `SIGN_IN` \| `SIGN_OUT` \| `SIGN_CONFIRM`. Optional: `ttl_seconds` (30–300), `notify_team_qr`, `notify_team_message`, `live_rotation_enabled`, `live_rotation_interval_sec`, `live_rotation_grace_steps`, `require_biometric`, `require_face_liveness`, `require_photo_base64`, `single_use_token`, `require_geofence`. Caller must be **`is_manager`** |
| POST | `employee/check_manager_qr_token/` | `employee_id`, `token` | Validates QR token for the employee |
| POST | `employee/post_attendance_transaction_by_manager_qr/` | `employee_id`, `token` | Optional: `photo_base64`, `biometric_verified_client`, `device_id`, `latitude`, `longitude`, `gps_accuracy_m`, `beacon_*`, `transaction_comment`. Geofence policy may require **`latitude`** + **`longitude`** |
| POST | `employee/post_attendance_transaction/` | `employee_id`, `action` | `action`: `SIGN_IN` \| `SIGN_OUT` \| `SIGN_CONFIRM`. Optional: `location_id`, `photo_base64`, `biometric_verified_client`, `device_id`, **`latitude`**, **`longitude`**, `gps_accuracy_m`, `beacon_uuid`, `beacon_major`, `beacon_minor`, `beacon_rssi`, `transaction_comment`. Entity **normal signing** policy may require face capture (`photo_base64`) for liveness/compare |
| POST | `employee/confirm_attendance_transaction_recorded/` | `employee_id`, `transaction_id` | |
| POST | `employee/load_recents_transactions/` | `employee_id` | Last **10** transactions (no `limit` parameter) |
| POST | `employee/load_transactions_by_date/` | `employee_id`, `date_from`, `date_to` | Dates: `YYYY-MM-DD` |
| POST | `employee/load_today_timeline/` | `employee_id` | Today’s sign-in / confirm / sign-out timeline hints |
| POST | `employee/load_employees_entity/` | `entity_id` | **Staff** session: uses session entity (ignores mismatched body). Unauthenticated: validates `entity_id` + license |
| POST | `employee/search_employees/` | `entity_id` + at least one filter | Filters: `employee_name`, `employee_id`, `civil_id`, `employee_no`. Optional: `limit` (default 100, max 500), `include_inactive` + `by_staff_id` (staff-only path for inactive). Staff session forces session entity |
| POST | `employee/list_activation_requests/` | `by_staff_id` | Staff only. Optional: `status`, `limit` |
| POST | `employee/decide_activation_request/` | `by_staff_id`, `request_id`, `decision` | `decision`: `activate` \| `reject` — staff only |
| POST | `employee/inbox_messages/` | `employee_id` | Optional: `limit` (default 50) |
| POST | `employee/search_inbox_messages/` | `employee_id` | Optional: `date_from`, `date_to`, `content_contains`, `limit` |
| POST | `employee/set_message_read/` | `employee_id`, `message_id` | |
| POST | `employee/update_user_photo/` | `employee_id`, `photo_base64` | |
| POST | `employee/user_entity/` | `employee_id` | Entity branding / settings summary |
| POST | `employee/load_employee_for_parent/` | `employee_id` | Lighter profile payload |
| POST | `employee/post_location/` | `employee_id`, `name` | **Staff user** (`employee.user.is_staff`) only. Optional: `description`, `latitude`, `longitude`, `is_GPS_based`, `is_beacon_based`, `major_value`, `minor_value`, `rssi_threshold`, `beacon_uuid`, `is_active` |

---

## App global settings

Deployment-wide toggles live in a singleton **`AppGlobalSettings`** row (managed in Django admin or via the superadmin API).

| Field | Type | Description |
|-------|------|-------------|
| `store_review_mode` | boolean | When `true`, `verify-uuid` skips UUID matching (for App Store review builds) |
| `store_review_user_id` | positive int | Django **`User.pk`** whose **`Employee`** profile is returned when store review mode is on (default `1`) |
| `updated_at` | ISO datetime | Last change timestamp (read-only in API responses) |

### Superadmin API

**`GET /api/superadmin/app-global-settings/`** — returns current values.

**`PATCH /api/superadmin/app-global-settings/`** — update one or both fields:

```json
{
  "store_review_mode": true,
  "store_review_user_id": 1
}
```

- Requires **`is_superuser=True`** (staff-only accounts get **403**).
- `store_review_user_id` must reference an existing Django user.
- At least one of `store_review_mode` or `store_review_user_id` must be present in the body.

### Store review mode

When **`store_review_mode`** is enabled:

1. **`POST /api/employee/verify-uuid/`** ignores the submitted `employee_uuid`.
2. The server looks up the active **`Employee`** where **`user_id`** equals **`store_review_user_id`**.
3. On success, the response includes `store_review_mode: true`, `store_review_user_id`, and the review employee’s `employee_uuid`.
4. If no active employee exists for that user → **503** with an explanatory `error` message.

---

## Face liveness and face compare

There is **no standalone** “liveness only” or “compare only” public route. Server-side checks run inside:

- `POST /api/employee/post_attendance_transaction/`
- `POST /api/employee/post_attendance_transaction_by_manager_qr/`

when entity / QR policy requires them, using `photo_base64` and the employee’s stored reference photo.

---

## Common error responses

Typical JSON shape: `{"error": "<message>"}`.

- **400** — Missing/invalid fields, invalid JSON, validation errors
- **403** — License expired, identity/device checks, geofence radius, cross-entity actions, permission (e.g. not manager, not superuser)
- **404** — Employee, entity, token, or message not found
- **503** — Store review mode enabled but no active employee for the configured `store_review_user_id`
- **409** — Duplicate / already decided / token already used (context-specific)
- **410** — QR token expired

---

## Frontend gotchas

1. **`verify-uuid`** returns `"user_id"` set to **`Employee.id`** in the current backend — do not assume it is Django **`User.id`**. In **store review mode**, the request UUID is ignored; the employee comes from **`store_review_user_id`**.
2. **`load_employees_entity`** returns `"employee_id"` as **`Employee.user_id`** per record; other endpoints may expect **`Employee.id`** or **`user_id`** in different shapes — always check the response field definitions above.
3. **`search_employees`** returns both **`employee_id`** (**`Employee.id`**) and **`user_id`** per row for disambiguation.

---

## Running locally

Activate the project virtualenv and use Django’s development server (see your local `Notes.txt` or team docs for DB credentials).

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py runserver
```

---

## See also

- `API_BRIEF.txt` — concise endpoint reference (partial coverage; both docs are aligned for app global settings and store review mode).
