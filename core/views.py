from django.http import JsonResponse, HttpResponse
from django.shortcuts import redirect, render
from django.conf import settings
from django.utils.html import escape
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.authentication import CookieJWTAuthentication
from core.security import audit_log, get_client_ip


def admin_dashboard_view(request):
    user = getattr(request, "user", None) if getattr(request, "user", None) and request.user.is_authenticated else None

    if user is None:
        auth_result = CookieJWTAuthentication().authenticate(request)
        if auth_result:
            user, _ = auth_result

    if user is None:
        refresh_cookie_name = settings.SIMPLE_JWT["AUTH_COOKIE_REFRESH"]
        refresh_token = request.COOKIES.get(refresh_cookie_name)
        if refresh_token:
            try:
                refresh = RefreshToken(refresh_token)
                user_id = refresh.get("user_id")
                if user_id:
                    from accounts.models import User

                    user = User.objects.filter(id=user_id, is_active=True).first()
            except TokenError:
                user = None

    if user is None:
        return redirect(f"/login/?next={request.path}")

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
