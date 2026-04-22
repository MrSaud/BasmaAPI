from django.utils import timezone
from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return response

    request = context.get("request")
    view = context.get("view")
    payload = {
        "ok": False,
        "status_code": response.status_code,
        "error": response.data,
        "path": request.path if request else "",
        "view": view.__class__.__name__ if view else "",
        "timestamp": timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    response.data = payload
    return response
