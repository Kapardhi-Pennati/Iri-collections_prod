from django.http import JsonResponse, HttpResponse
from django.utils.html import escape
from core.security import audit_log, get_client_ip

def csrf_failure(request, reason=""):
    """
    Custom CSRF failure view for security-hardened environment.
    Logs the violation and returns a generic error.
    """
    audit_log(
        action="CSRF_VIOLATION",
        user_id=request.user.id if request.user.is_authenticated else None,
        details={
            "reason": escape(reason or "CSRF token missing or incorrect"),
            "path": escape(request.path),
            "ip": get_client_ip(request)
        },
        severity="WARNING"
    )
    
    if request.path.startswith('/api/'):
        return JsonResponse(
            {"error": "Security validation failed (CSRF)."},
            status=403
        )
    
    return HttpResponse(
        "<h1>403 Forbidden</h1><p>Security validation failed. Please refresh and try again.</p>",
        status=403
    )
