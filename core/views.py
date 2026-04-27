from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.html import escape
from core.security import audit_log, get_client_ip


@login_required(login_url="/login/")
def admin_dashboard_view(request):
    user = request.user
    if not (user.is_superuser or getattr(user, "role", None) == "admin"):
        return redirect("home")
    return render(request, "admin_dashboard.html")

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
