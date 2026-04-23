"""
CENTRALIZED EMAIL SERVICE
==========================
Production-ready email utility using EmailMultiAlternatives for
high-deliverability transactional emails.

Architecture decisions:
  • EmailMultiAlternatives sends both HTML + plain-text (multipart/alternative).
    Email clients that support HTML render the rich version; others fall back
    to plain text. This is critical for deliverability — emails without a
    text/plain part are flagged by spam filters (SpamAssassin, Gmail, etc.).

  • Django's `default_token_generator` (PasswordResetTokenGenerator) creates
    HMAC-SHA256 tokens that are:
      - Cryptographically secure (signed with SECRET_KEY)
      - Time-sensitive (embed timestamp in the hash)
      - Single-use (invalidated when the user's password/last_login changes)
      - Stateless (no DB storage needed — verified by re-computing the hash)

  • All emails are rendered from Django templates, making them easy to
    customize without code changes.

  • This module is NEVER called directly from views. It's invoked by
    Celery tasks (core/tasks.py) to ensure SMTP latency doesn't block
    API response times.
"""

import logging
from typing import Optional, List

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes
from django.utils.html import strip_tags
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger("accounts")

User = get_user_model()


# ═══════════════════════════════════════════════════════════════════════════
# BASE EMAIL SENDER
# ═══════════════════════════════════════════════════════════════════════════

def send_html_email(
    subject: str,
    template_name: str,
    context: dict,
    recipient_list: List[str],
    from_email: Optional[str] = None,
) -> bool:
    """
    Core email sending function using EmailMultiAlternatives.

    Renders an HTML template, auto-generates a plain-text fallback by
    stripping HTML tags, and sends both versions in a single email.

    Args:
        subject: Email subject line
        template_name: Path to the HTML template (e.g., 'emails/welcome.html')
        context: Template context dictionary
        recipient_list: List of recipient email addresses
        from_email: Sender address (defaults to settings.DEFAULT_FROM_EMAIL)

    Returns:
        True if sent successfully, False otherwise

    Security:
        ✅ Uses Django template engine (auto-escaping prevents XSS in emails)
        ✅ from_email defaults to verified sender (prevents spoofing)
        ✅ Never logs email content (may contain tokens/OTPs)
    """
    if not from_email:
        from_email = settings.DEFAULT_FROM_EMAIL

    # ── Add global context ────────────────────────────────────────────
    context.setdefault("site_name", "Iri Collections")
    context.setdefault("site_url", getattr(settings, "FRONTEND_URL", "http://localhost:3000"))
    context.setdefault("support_email", settings.DEFAULT_FROM_EMAIL)
    context.setdefault("current_year", __import__("datetime").datetime.now().year)

    try:
        # ── Render HTML from template ─────────────────────────────────
        html_content = render_to_string(template_name, context)

        # ── Auto-generate plain-text fallback ─────────────────────────
        # strip_tags() removes all HTML, producing a readable text version.
        # This is critical for:
        #   1. Email clients that don't support HTML (rare but exist)
        #   2. Accessibility (screen readers prefer plain text)
        #   3. Spam filter compliance (multipart/alternative scores better)
        text_content = strip_tags(html_content)

        # ── Build multipart email ─────────────────────────────────────
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_content,       # text/plain part
            from_email=from_email,
            to=recipient_list,
        )
        # Attach the HTML version as an alternative content type
        email.attach_alternative(html_content, "text/html")

        # ── Send ──────────────────────────────────────────────────────
        email.send(fail_silently=False)

        logger.info(f"Email sent: subject='{subject}', to={recipient_list}")
        return True

    except Exception as e:
        # ✅ Log the error but never the email content (may contain tokens)
        logger.error(
            f"Email send failed: subject='{subject}', "
            f"to={recipient_list}, error={str(e)}"
        )
        return False


# ═══════════════════════════════════════════════════════════════════════════
# OTP EMAIL
# ═══════════════════════════════════════════════════════════════════════════

def send_otp_email(email: str, otp_code: str) -> bool:
    """
    Send OTP verification code email (signup/password-reset flow).

    Args:
        email: Recipient email address.
        otp_code: 6-digit one-time password.
    """
    context = {
        "otp_code": otp_code,
        "email": email,
        "expiry_minutes": 15,
    }

    return send_html_email(
        subject="Your OTP Verification Code — Iri Collections",
        template_name="emails/otp_code.html",
        context=context,
        recipient_list=[email],
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. WELCOME EMAIL
# ═══════════════════════════════════════════════════════════════════════════

def send_welcome_email(user_id: int) -> bool:
    """
    Send a welcome email after successful registration.

    Args:
        user_id: The primary key of the newly registered user

    Security:
        ✅ Fetches user from DB (never trusts serialized user data)
        ✅ Gracefully handles deleted users (race condition protection)
    """
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.warning(f"Welcome email skipped: user {user_id} not found")
        return False

    context = {
        "user_name": user.full_name or user.username,
        "user_email": user.email,
    }

    return send_html_email(
        subject="Welcome to Iri Collections! ✨",
        template_name="emails/welcome.html",
        context=context,
        recipient_list=[user.email],
    )


# ═══════════════════════════════════════════════════════════════════════════
# 2. ACCOUNT VERIFICATION EMAIL
# ═══════════════════════════════════════════════════════════════════════════

def send_verification_email(user_id: int) -> bool:
    """
    Send an email verification link using Django's token generator.

    The verification URL contains:
      - uidb64: Base64-encoded user PK (not the raw ID — prevents enumeration)
      - token: HMAC-SHA256 signed token from default_token_generator

    Token properties (from PasswordResetTokenGenerator):
      ✅ Cryptographic: HMAC(SECRET_KEY, user_pk + timestamp + password_hash + last_login)
      ✅ Time-sensitive: Expires after PASSWORD_RESET_TIMEOUT (default 3 days in Django)
      ✅ Single-use: Invalidated when password or last_login changes
      ✅ Stateless: No DB storage needed

    Args:
        user_id: PK of the user to verify
    """
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.warning(f"Verification email skipped: user {user_id} not found")
        return False

    # ── Generate secure token ─────────────────────────────────────────
    # urlsafe_base64_encode encodes the user PK so it's safe for URLs
    # and doesn't reveal the actual integer ID.
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)

    # ── Build verification URL pointing to the FRONTEND app ──────────
    # The frontend receives the uidb64 + token and calls a backend
    # endpoint to actually verify the email. This keeps the UX smooth.
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    verification_url = f"{frontend_url}/verify-email/{uid}/{token}/"

    context = {
        "user_name": user.full_name or user.username,
        "verification_url": verification_url,
        # Token validity period for display in the email
        "expiry_hours": getattr(settings, "PASSWORD_RESET_TIMEOUT", 259200) // 3600,
    }

    return send_html_email(
        subject="Verify Your Email — Iri Collections",
        template_name="emails/verify_email.html",
        context=context,
        recipient_list=[user.email],
    )


# ═══════════════════════════════════════════════════════════════════════════
# 3. PASSWORD RESET EMAIL
# ═══════════════════════════════════════════════════════════════════════════

def send_password_reset_email(user_id: int) -> bool:
    """
    Send a password reset email with a secure, single-use, expiring link.

    Uses the same token generator as email verification, but the
    frontend URL points to the password reset page instead.

    Token security properties:
      ✅ Single-use: Token is invalidated after the password is changed
         (because the password hash is part of the HMAC input)
      ✅ Expiring: PASSWORD_RESET_TIMEOUT controls validity (default 3 days)
      ✅ Tamper-proof: HMAC-SHA256 signed with SECRET_KEY
      ✅ User-specific: Contains user PK + last_login in the hash

    Args:
        user_id: PK of the user requesting password reset
    """
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        # ✅ Don't reveal whether user exists (security best practice)
        logger.info(f"Password reset email skipped: user {user_id} not found")
        return False

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)

    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    reset_url = f"{frontend_url}/reset-password/{uid}/{token}/"

    context = {
        "user_name": user.full_name or user.username,
        "reset_url": reset_url,
        "expiry_hours": getattr(settings, "PASSWORD_RESET_TIMEOUT", 259200) // 3600,
    }

    return send_html_email(
        subject="Reset Your Password — Iri Collections",
        template_name="emails/password_reset.html",
        context=context,
        recipient_list=[user.email],
    )


# ═══════════════════════════════════════════════════════════════════════════
# 4. ORDER STATUS UPDATE EMAIL
# ═══════════════════════════════════════════════════════════════════════════

def send_order_status_email(
    order_id: int, old_status: str, new_status: str
) -> bool:
    """
    Notify the customer when their order status changes.

    Called by the Django signal (store/signals.py) via a Celery task,
    NOT from Order.save() (which would block the request).

    Args:
        order_id: PK of the order
        old_status: Previous status value
        new_status: New status value
    """
    from store.models import Order  # ✅ Deferred import to avoid circular dependency

    try:
        order = Order.objects.select_related("user").get(id=order_id)
    except Order.DoesNotExist:
        logger.warning(f"Order status email skipped: order {order_id} not found")
        return False

    user = order.user

    # ── Map status to human-friendly labels and emojis ────────────────
    status_display = {
        "pending": ("Pending", "⏳"),
        "confirmed": ("Confirmed", "✅"),
        "shipped": ("Shipped", "🚚"),
        "delivered": ("Delivered", "📦"),
        "cancelled": ("Cancelled", "❌"),
    }

    new_label, new_emoji = status_display.get(new_status, (new_status.title(), "📋"))
    old_label, _ = status_display.get(old_status, (old_status.title(), ""))

    # ── Build subject line ────────────────────────────────────────────
    if new_status == "cancelled":
        subject = f"Order {order.order_number} — Cancelled"
    elif new_status == "shipped":
        subject = f"Order {order.order_number} — Your order has been shipped! 🚚"
    elif new_status == "delivered":
        subject = f"Order {order.order_number} — Delivered! 📦"
    else:
        subject = f"Order {order.order_number} — Status Update: {new_label}"

    context = {
        "user_name": user.full_name or user.username,
        "order_number": order.order_number,
        "old_status": old_label,
        "new_status": new_label,
        "new_status_emoji": new_emoji,
        "total_amount": order.total_amount,
        "is_cancelled": new_status == "cancelled",
        "is_shipped": new_status == "shipped",
        "is_delivered": new_status == "delivered",
    }

    return send_html_email(
        subject=subject,
        template_name="emails/order_status.html",
        context=context,
        recipient_list=[user.email],
    )
