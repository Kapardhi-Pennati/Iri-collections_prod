"""
ORDER STATUS CHANGE SIGNAL
============================
Listens for Order.save() and dispatches an async email notification
when the status field actually changes.

Why a signal instead of overriding save()?
  1. Separation of concerns: The Order model shouldn't know about emails.
  2. Testability: Signals can be disconnected in tests.
  3. Async: The signal dispatches a Celery task, never blocking the request.

Why post_save instead of pre_save?
  We need the order to be saved (committed) before the Celery worker
  tries to fetch it from the DB. post_save guarantees the row exists.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from store.models import Order

logger = logging.getLogger("payments")


@receiver(post_save, sender=Order)
def order_status_changed(sender, instance, created, **kwargs):
    """
    Dispatch async email when an order's status changes.

    Flow:
      1. Order.__init__() stores self._original_status (already implemented)
      2. When .save() is called and status differs, this signal fires
      3. Dispatches task_send_order_status_email.delay() to Celery
      4. Celery worker picks up the task and sends the email asynchronously

    Guard conditions:
      • Skip newly created orders (they start as 'pending', no notification needed)
      • Skip if status hasn't actually changed (e.g., updating shipping_address)
      • Only send if _original_status was set (prevents edge cases)
    """
    # ── Skip brand-new orders ─────────────────────────────────────────
    # The first save has no "previous status" to compare against.
    if created:
        return

    # ── Check if status actually changed ──────────────────────────────
    # _original_status is set in Order.__init__() and tracks the status
    # at the time the instance was loaded from the DB.
    old_status = getattr(instance, "_original_status", None)
    new_status = instance.status

    if old_status is None or old_status == new_status:
        return

    # ── Dispatch async email task ─────────────────────────────────────
    # Import here to avoid circular imports (tasks.py imports models)
    from core.tasks import task_send_order_status_email

    logger.info(
        f"Order {instance.order_number} status changed: "
        f"{old_status} → {new_status}. Dispatching email task."
    )

    # .delay() sends this to the Celery queue immediately.
    # The worker processes it asynchronously — the current request
    # returns to the user without waiting for the SMTP connection.
    # Wrapped in try/except: if the broker (Redis) is down, we must
    # NOT let a failed email dispatch crash the order status update.
    try:
        task_send_order_status_email.delay(
            order_id=instance.id,
            old_status=old_status,
            new_status=new_status,
        )
    except Exception:
        logger.exception(
            f"Failed to dispatch email task for order {instance.order_number}. "
            f"Broker may be unreachable. Order update will proceed."
        )

    # ── Update _original_status for future saves in the same request ──
    instance._original_status = new_status
