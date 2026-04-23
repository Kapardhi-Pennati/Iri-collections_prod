"""
CELERY TASKS — Asynchronous Email Dispatch
===========================================
Each task wraps a method from core.services.email_service.

Architecture:
  • Tasks accept PRIMITIVE TYPES only (int, str) — not Django model instances.
    This is because Celery serializes task arguments to JSON for the message
    broker (Redis). Model instances aren't JSON-serializable and would fail.

  • The actual model fetch happens INSIDE the task, on the Celery worker.
    This ensures the worker always reads the latest DB state (not stale data
    from when the task was enqueued).

  • retry settings handle transient SMTP failures (connection refused, timeout).
    After 3 retries with exponential backoff, the task is marked as failed.
"""

import logging

from celery import shared_task

logger = logging.getLogger("accounts")


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # 60 seconds between retries
    autoretry_for=(Exception,),
    retry_backoff=True,  # Exponential backoff: 60s, 120s, 240s
    retry_jitter=True,   # Add randomness to prevent thundering herd
)
def task_send_welcome_email(self, user_id: int) -> bool:
    """
    Send welcome email after successful registration.

    Triggered from: accounts/views.py → RegisterView.create()
    """
    from core.services.email_service import send_welcome_email
    # ✅ Deferred import: avoids circular imports and ensures the
    # email service module is only loaded inside the worker process.
    return send_welcome_email(user_id)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def task_send_verification_email(self, user_id: int) -> bool:
    """
    Send email verification link with secure token.

    Triggered from: accounts/views.py → RegisterView.create()
    (sent alongside the welcome email)
    """
    from core.services.email_service import send_verification_email
    return send_verification_email(user_id)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def task_send_password_reset_email(self, user_id: int) -> bool:
    """
    Send password reset link with single-use token.

    Triggered from: accounts/views.py → RequestOTPView.post()
    (when action == 'reset')
    """
    from core.services.email_service import send_password_reset_email
    return send_password_reset_email(user_id)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def task_send_order_status_email(
    self, order_id: int, old_status: str, new_status: str
) -> bool:
    """
    Send order status change notification.

    Triggered from: store/signals.py → order_status_changed (post_save signal)
    """
    from core.services.email_service import send_order_status_email
    return send_order_status_email(order_id, old_status, new_status)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def task_send_otp_email(self, email: str, otp_code: str) -> bool:
    """
    Send OTP verification code email (for signup and password reset).

    Triggered from: accounts/views.py → RequestOTPView.post()

    This is separate from the token-based verification email because
    the OTP flow is the existing signup mechanism in this codebase.
    """
    from core.services.email_service import send_otp_email

    return send_otp_email(email=email, otp_code=otp_code)
