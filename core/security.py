"""
core/security.py — Production-grade security primitives.

Changes from original:
  - is_rate_limited(): Fixed TOCTOU race condition. Old get()+set() pattern
    allowed two simultaneous requests to both read count=0 and both proceed.
    New pattern uses atomic cache.add()+cache.incr() operations.
  - get_client_ip(): Now validates the extracted IP via ipaddress module to
    prevent log injection and rate-limit key manipulation from crafted headers.
  - audit_log(): All string values in `details` are sanitized to prevent
    log-injection attacks (newline stripping, length capping).
  - sanitize_for_log(): New helper used throughout to neutralize log forging.
  - generate_otp_session_token() / verify_otp_session_token(): New signed
    token pair. Replaces relying solely on the DB is_verified boolean flag,
    which an attacker could abuse by replaying an email address without
    possessing the actual OTP token.
  - verify_hmac_signature(): Wrapped in try/except so exceptions never leak
    implementation details to callers.
  - increment_failed_login_attempts(): Also made atomic via add()+incr().
"""

import hashlib
import hmac
import ipaddress
import logging
import re
import secrets
from functools import wraps
from typing import Optional

from django.core.cache import cache
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.http import JsonResponse
from django.utils.timezone import now as django_now

logger = logging.getLogger("core.security")

# ── Module-level constants ─────────────────────────────────────────────────
MAX_LOGIN_ATTEMPTS: int = 5
LOGIN_LOCKOUT_SECONDS: int = 3600   # 1 hour
OTP_TTL_MINUTES: int = 15
_OTP_SIGNER_SALT: str = "iri.otp.session.v1"


# ── Log Injection Prevention ───────────────────────────────────────────────

def sanitize_for_log(value: str, max_length: int = 200) -> str:
    """
    Sanitise a string before embedding it in a log message.

    Strips newlines and carriage returns — the primary vectors for log
    injection/forging attacks — and truncates to prevent log flooding.

    Args:
        value:      Raw string from user input or an external source.
        max_length: Maximum retained length (default 200 chars).

    Returns:
        Sanitised, truncated string safe for structured logging.
    """
    if not isinstance(value, str):
        value = str(value)
    value = value.replace("\n", "\\n").replace("\r", "\\r")
    return value[:max_length]


# ── Cryptographic OTP Generation ───────────────────────────────────────────

def generate_secure_otp(length: int = 6) -> str:
    """
    Generate a cryptographically secure numeric OTP.

    Uses secrets.randbelow() which provides uniform distribution without
    the modulo bias present in naive random.randint() implementations.

    Args:
        length: Digit count (default 6 → 1,000,000 combinations).

    Returns:
        Zero-padded digit string of the requested length.
    """
    return str(secrets.randbelow(10 ** length)).zfill(length)


# ── Signed OTP Session Tokens ──────────────────────────────────────────────

def generate_otp_session_token(email: str) -> str:
    """
    Issue a short-lived, cryptographically signed token after OTP verification.

    This token must be submitted by the client on the next step
    (RegisterView / ResetPasswordView). It prevents an attacker from
    abusing the DB `is_verified=True` flag by replaying just the email
    address — they must also possess this token.

    Args:
        email: The verified email address to bind to the token.

    Returns:
        A signed, timestamped, URL-safe token string.

    Security:
        Signed with Django SECRET_KEY via TimestampSigner.
        Salt isolates tokens from other Django signing contexts.
        Expires in OTP_TTL_MINUTES (enforced in verify_otp_session_token).
    """
    signer = TimestampSigner(salt=_OTP_SIGNER_SALT)
    return signer.sign(email)


def verify_otp_session_token(
    token: str, max_age_seconds: int = OTP_TTL_MINUTES * 60
) -> Optional[str]:
    """
    Verify and decode a signed OTP session token.

    Args:
        token:           Signed token from generate_otp_session_token().
        max_age_seconds: Maximum token age (default = OTP_TTL_MINUTES * 60).

    Returns:
        The verified email string, or None if the token is invalid or expired.

    Security:
        Returns None (never raises) to prevent timing oracles.
        TimestampSigner uses constant-time comparison internally.
    """
    try:
        signer = TimestampSigner(salt=_OTP_SIGNER_SALT)
        return signer.unsign(token, max_age=max_age_seconds)
    except (SignatureExpired, BadSignature, Exception):
        return None


# ── Atomic Rate Limiting ───────────────────────────────────────────────────

def is_rate_limited(key: str, max_attempts: int, window_seconds: int) -> bool:
    """
    Atomically check-and-increment a rate-limit counter using Django cache.

    Race-condition-safe: cache.add() is atomic (only sets if absent) and
    cache.incr() is atomic. The previous get()+set() pattern had a TOCTOU
    vulnerability where two simultaneous requests could both read count=0
    and both be allowed through.

    Args:
        key:            Scoped identifier, e.g. f"otp_verify:{email}".
        max_attempts:   Maximum allowed requests within the window.
        window_seconds: Rolling window duration in seconds.

    Returns:
        True if the request should be blocked; False if allowed.
    """
    cache_key = f"ratelimit:{key}"

    # Atomically initialise to 1 if the key is absent (first request in window)
    if cache.add(cache_key, 1, window_seconds):
        return False  # First attempt — allow

    # Key already exists; increment atomically
    try:
        current = cache.incr(cache_key)
    except ValueError:
        # Key expired between add() check and incr() — treat as first attempt
        cache.set(cache_key, 1, window_seconds)
        return False

    # Some cache backends may return None when errors are ignored.
    if not isinstance(current, int):
        logger.warning("Rate limit cache unavailable for key: %s", sanitize_for_log(key))
        return False

    if current > max_attempts:
        logger.warning("Rate limit exceeded for key: %s", sanitize_for_log(key))
        return True
    return False


def get_rate_limit_remaining(key: str, max_attempts: int) -> int:
    """Return attempts remaining before rate limiting triggers."""
    attempts = cache.get(f"ratelimit:{key}", 0)
    if not isinstance(attempts, int):
        attempts = 0
    return max(0, max_attempts - attempts)


def rate_limit_decorator(max_attempts: int, window_seconds: int):
    """
    View decorator for declarative, per-user/IP rate limiting.

    Scopes by user ID for authenticated requests; by IP for anonymous ones.

    Args:
        max_attempts:   Max requests in the window before blocking.
        window_seconds: Window size in seconds.

    Usage:
        @rate_limit_decorator(max_attempts=5, window_seconds=3600)
        def my_view(request): ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.is_authenticated:
                key = f"endpoint:{view_func.__name__}:user:{request.user.id}"
            else:
                key = f"endpoint:{view_func.__name__}:ip:{get_client_ip(request)}"
            if is_rate_limited(key, max_attempts, window_seconds):
                return JsonResponse(
                    {"error": "Too many requests. Please try again later."},
                    status=429,
                )
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# ── Request Utilities ──────────────────────────────────────────────────────

def get_client_ip(request) -> str:
    """
    Extract and validate the real client IP from the request.

    Checks X-Forwarded-For first (set by reverse proxies), then falls back
    to REMOTE_ADDR. Validates the result using the ipaddress module to
    prevent spoofed header values from polluting rate-limit keys or logs.

    Args:
        request: Django HTTP request object.

    Returns:
        A valid IP address string. Falls back to "0.0.0.0" on failure.

    Security:
        Takes only the FIRST (leftmost) entry from X-Forwarded-For —
        the original client IP — which cannot be spoofed when your
        reverse proxy is configured to strip and re-add the header.
    """
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    candidate = (
        x_forwarded_for.split(",")[0].strip()
        if x_forwarded_for
        else request.META.get("REMOTE_ADDR", "0.0.0.0")
    )
    try:
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        logger.warning("Invalid IP extracted: %s", sanitize_for_log(candidate))
        return "0.0.0.0"


# ── Audit Logging ──────────────────────────────────────────────────────────

def audit_log(
    action: str,
    user_id: Optional[int] = None,
    details: Optional[dict] = None,
    severity: str = "INFO",
    ip_address: Optional[str] = None,
) -> None:
    """
    Record a security-relevant event to the structured audit trail.

    All string values in `details` are sanitised against log injection.
    Never pass passwords, tokens, or raw credentials to this function.

    Args:
        action:     Event identifier (e.g., "LOGIN_SUCCESS", "OTP_VERIFIED").
        user_id:    Acting user PK; None for anonymous events.
        details:    Supplementary key/value context. String values sanitised.
        severity:   "INFO", "WARNING", or "CRITICAL".
        ip_address: Client IP for geographic/security correlation.
    """
    safe_details = {
        k: (sanitize_for_log(str(v)) if isinstance(v, str) else v)
        for k, v in (details or {}).items()
    }
    entry = {
        "timestamp": django_now().isoformat(),
        "action": action,
        "user_id": user_id,
        "details": safe_details,
        "ip_address": ip_address,
    }
    if severity == "CRITICAL":
        logger.critical(entry)
    elif severity == "WARNING":
        logger.warning(entry)
    else:
        logger.info(entry)


# ── Account Lockout Management ─────────────────────────────────────────────

def increment_failed_login_attempts(user_id: int) -> int:
    """
    Atomically increment the failed-login counter for a user.

    Uses the same add()+incr() atomic pattern as is_rate_limited() to
    prevent the race condition where two parallel failed logins could
    both read count=0 and record only one failure.

    Args:
        user_id: Primary key of the user account.

    Returns:
        The updated (post-increment) failure count.
    """
    cache_key = f"login_attempts:{user_id}"
    if cache.add(cache_key, 1, LOGIN_LOCKOUT_SECONDS):
        return 1
    try:
        current = cache.incr(cache_key)
        if isinstance(current, int):
            return current
        logger.warning(
            "Failed-login cache unavailable for user_id=%s; defaulting to 1.",
            user_id,
        )
        return 1
    except ValueError:
        cache.set(cache_key, 1, LOGIN_LOCKOUT_SECONDS)
        return 1


def is_account_locked(user_id: int, max_attempts: int = MAX_LOGIN_ATTEMPTS) -> bool:
    """Return True if the account is locked due to repeated failed logins."""
    return cache.get(f"login_attempts:{user_id}", 0) >= max_attempts


def unlock_account(user_id: int) -> None:
    """Clear the failed-login counter after a successful authentication."""
    cache.delete(f"login_attempts:{user_id}")


def get_lockout_remaining_seconds(user_id: int) -> int:
    """Return remaining lockout time in seconds (requires Django 4.1+ cache)."""
    remaining = cache.ttl(f"login_attempts:{user_id}")
    return max(0, remaining or 0)


# ── HMAC Signature Verification ───────────────────────────────────────────

def verify_hmac_signature(message: bytes, signature: str, secret: str) -> bool:
    """
    Verify an HMAC-SHA256 signature using constant-time comparison.

    Used for Stripe webhook verification and any other HMAC-signed payloads.

    Args:
        message:   Raw bytes to verify (e.g., request.body).
        signature: Hex-encoded HMAC provided by the sender.
        secret:    Shared secret key (server-side only).

    Returns:
        True if valid; False if invalid, expired, or on any error.

    Security:
        hmac.compare_digest() prevents timing oracle attacks.
        All exceptions return False — never raises, never leaks details.
        Empty inputs are rejected immediately.
    """
    if not signature or not secret or not message:
        return False
    try:
        expected = hmac.new(
            key=secret.encode("utf-8"),
            msg=message,
            digestmod=hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        logger.error("HMAC verification encountered an unexpected error.")
        return False
