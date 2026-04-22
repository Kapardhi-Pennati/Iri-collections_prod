"""
Traffic Middleware — records each page view for analytics.
Skips API, static, media, and Django admin paths.
Uses threading to avoid blocking the response.
"""

import threading
import logging

logger = logging.getLogger(__name__)

# Paths to skip (don't record as page views)
SKIP_PREFIXES = (
    "/api/",
    "/static/",
    "/staticfiles/",
    "/media/",
    "/admin/",
    "/favicon.",
    "/robots.txt",
    "/sitemap.xml",
)


def _get_client_ip(request):
    """Extract real IP, respecting proxies."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _record_view(path, session_key, ip_address, user_agent, user_id):
    """Record a PageView in a background thread so it doesn't slow responses."""
    try:
        from .models import PageView  # lazy import to avoid circular imports

        PageView.objects.create(
            path=path,
            session_key=session_key or "",
            ip_address=ip_address,
            user_agent=user_agent[:500] if user_agent else "",
            user_id=user_id,
        )
    except Exception as exc:
        logger.debug("PageView record error: %s", exc)


class TrafficMiddleware:
    """
    Records GET page views asynchronously.
    Only active for HTML-serving pages, not API or static endpoints.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Only track GET requests that return HTML pages
        if request.method == "GET":
            path = request.path_info

            # Skip non-page paths
            if not any(path.startswith(prefix) for prefix in SKIP_PREFIXES):
                # Only record successful HTML responses (200-299)
                if 200 <= response.status_code < 300:
                    try:
                        session_key = request.session.session_key or ""
                        if not session_key:
                            request.session.create()
                            session_key = request.session.session_key or ""
                    except Exception:
                        session_key = ""

                    ip_address = _get_client_ip(request)
                    user_agent = request.META.get("HTTP_USER_AGENT", "")
                    user_id = request.user.id if request.user.is_authenticated else None

                    t = threading.Thread(
                        target=_record_view,
                        args=(path, session_key, ip_address, user_agent, user_id),
                        daemon=True,
                    )
                    t.start()

        return response
