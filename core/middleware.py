"""
core/middleware.py — Additional security response headers.

Django's built-in SecurityMiddleware covers HSTS, SSL redirect, and a few
other headers. This middleware adds the remaining headers recommended by
OWASP and Mozilla Observatory that are not provided by Django out of the box:

  - Permissions-Policy: Restricts browser features (camera, geolocation, etc.)
  - Referrer-Policy:    Controls how much referrer info is sent cross-origin.
  - Cross-Origin-*:     CORP / COOP / COEP isolation headers.
  - X-Request-ID:       Unique request identifier for log correlation.
  - Cache-Control:      Prevents caching of API responses.

Add 'core.middleware.SecurityHeadersMiddleware' to MIDDLEWARE in settings.py
AFTER Django's SecurityMiddleware.
"""

import secrets
import logging

logger = logging.getLogger("core.security")


class SecurityHeadersMiddleware:
    """
    Inject additional HTTP security headers on every response.

    These headers address OWASP Top 10 concerns not covered by Django's
    built-in SecurityMiddleware (Spectre side-channel isolation, feature
    policy, referrer leakage, log correlation).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Generate a unique request ID for distributed tracing / log correlation.
        # Stored on the request object so views and loggers can reference it.
        request.request_id = secrets.token_hex(8)

        response = self.get_response(request)

        # ── Referrer Policy ─────────────────────────────────────────────
        # Sends full URL for same-origin requests but only the origin
        # (no path/query) for cross-origin — prevents leaking sensitive
        # URL parameters to third-party resources.
        response.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

        # ── Permissions / Feature Policy ─────────────────────────────────
        # Explicitly disable browser APIs that this application does not use.
        # Reduces attack surface if a dependency is compromised (supply chain).
        response.setdefault(
            "Permissions-Policy",
            (
                "geolocation=(), "
                "camera=(), "
                "microphone=(), "
                "payment=(), "
                "usb=(), "
                "magnetometer=(), "
                "accelerometer=(), "
                "gyroscope=()"
            ),
        )

        # ── Cross-Origin Isolation (Spectre mitigation) ──────────────────
        # COOP prevents a cross-origin page from holding a reference to
        # this window, protecting against Spectre-style side-channel leaks.
        response.setdefault(
            "Cross-Origin-Opener-Policy", "same-origin"
        )
        # CORP declares that this response should only be readable by
        # same-origin contexts (prevents cross-origin information leakage).
        response.setdefault(
            "Cross-Origin-Resource-Policy", "same-origin"
        )

        # ── Request ID ───────────────────────────────────────────────────
        # Echoed in the response so frontend/Sentry can correlate client-side
        # errors with backend log entries.
        response["X-Request-ID"] = request.request_id

        # ── Cache Control for API endpoints ─────────────────────────────
        # Prevent sensitive API responses from being cached by browsers or
        # intermediate proxies, even if the response headers don't say so.
        if request.path.startswith("/api/"):
            response.setdefault(
                "Cache-Control", "no-store, no-cache, must-revalidate, private"
            )
            response.setdefault("Pragma", "no-cache")

        # ── Content Security Policy ──────────────────────────────────────
        # Mitigates XSS and Quishing (QR Phishing) attacks.
        response.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "font-src 'self' data: https://unpkg.com https://fonts.gstatic.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "img-src 'self' data: blob: https://api.qrserver.com https://*.iricollections.in https://*.hostinger.com; "
            "connect-src 'self' https://api.postalpincode.in;"
        )

        return response
