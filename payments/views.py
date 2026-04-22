"""
payments/views.py — PhonePe Payment Gateway Views

Endpoints:
  POST /api/payments/initiate/          → Create PhonePe payment, return redirect URL
  POST /api/payments/callback/          → PhonePe server-to-server callback (webhook)
  GET  /api/payments/status/<txn_id>/  → Verify payment status after redirect
  GET  /api/payments/health-check/     → Gateway connectivity check

Security:
  ✅ Callback endpoint verifies PhonePe X-VERIFY checksum before trusting data
  ✅ Status endpoint polls PhonePe directly — never trusts client-side redirect params
  ✅ All fulfillment is atomic + idempotent
  ✅ Audit logging on every significant event
"""

import base64
import json
import logging

from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.security import audit_log, get_client_ip
from store.models import Order, Transaction

from .services import (
    check_phonepe_health,
    check_payment_status,
    create_phonepe_payment,
    fulfill_order_after_payment,
    rollback_order_inventory,
    _verify_callback_checksum,
)

logger = logging.getLogger("payments")


# ─────────────────────────────────────────────────────────────────────────────
# 1. GATEWAY HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([AllowAny])
def payment_health_check(request) -> Response:
    """
    Lightweight check that PhonePe credentials are configured and the
    API host is reachable. Safe to call from monitoring systems.
    """
    is_healthy, error = check_phonepe_health()
    if is_healthy:
        return Response({"status": "healthy", "healthy": True, "gateway": "PhonePe"})
    return Response(
        {"status": "unhealthy", "healthy": False, "error": error},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. INITIATE PAYMENT
# ─────────────────────────────────────────────────────────────────────────────

class InitiatePaymentView(APIView):
    """
    Initiate a PhonePe payment for an existing pending order.

    The client submits the order ID; we build and sign the PhonePe request
    server-side (amount from our DB, never from the client) and return the
    redirect URL.

    Request body:
        { "order_id": 123 }

    Response (success):
        {
            "payment_url": "https://mercury-t2.phonepe.com/...",
            "merchant_transaction_id": "IRI-XXXXXXXX-YYYYYYYY"
        }

    Security:
        ✅ Order must belong to the authenticated user (IDOR prevention)
        ✅ Only 'pending' orders can be paid (prevents double-payment)
        ✅ Amount is read from DB — client cannot manipulate price
        ✅ Existing unpaid transaction is reused (idempotent)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request) -> Response:
        order_id = request.data.get("order_id")
        if not order_id:
            return Response(
                {"error": "order_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order_id = int(order_id)
        except (TypeError, ValueError):
            return Response(
                {"error": "Invalid order_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Fetch order (scoped to this user — IDOR guard) ───────────
        try:
            order = Order.objects.prefetch_related("items").get(
                id=order_id,
                user=request.user,
            )
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if order.status != "pending":
            return Response(
                {"error": f"Order is already {order.status} and cannot be paid again."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if order.items.count() == 0:
            return Response(
                {"error": "Order has no items."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Build redirect/callback URLs ──────────────────────────────
        origin = request.META.get(
            "HTTP_ORIGIN",
            request.build_absolute_uri("/").rstrip("/"),
        )
        redirect_url = f"{origin}/checkout/?payment=result"
        callback_url = request.build_absolute_uri("/api/payments/callback/")

        # ── Initiate PhonePe payment ──────────────────────────────────
        payment_url, merchant_transaction_id, error = create_phonepe_payment(
            order=order,
            redirect_url=redirect_url,
            callback_url=callback_url,
        )

        if error:
            return Response(
                {"error": error},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # ── Create a Transaction record to track this payment ─────────
        Transaction.objects.update_or_create(
            order=order,
            defaults={
                "merchant_transaction_id": merchant_transaction_id,
                "amount": order.total_amount,
                "status": "created",
            },
        )

        audit_log(
            action="PAYMENT_INITIATED",
            user_id=request.user.id,
            details={
                "order_id": str(order.id),
                "order_number": order.order_number,
                "merchant_transaction_id": merchant_transaction_id,
            },
            severity="INFO",
            ip_address=get_client_ip(request),
        )

        return Response({
            "payment_url": payment_url,
            "merchant_transaction_id": merchant_transaction_id,
        })


# ─────────────────────────────────────────────────────────────────────────────
# 3. PHONEPE CALLBACK (Server-to-Server Webhook)
# ─────────────────────────────────────────────────────────────────────────────

@method_decorator(csrf_exempt, name="dispatch")
class PhonePeCallbackView(APIView):
    """
    Receive PhonePe's server-to-server payment result callback.

    PhonePe POSTs a Base64-encoded payload with an X-VERIFY header.
    We verify the signature before processing — any request with an
    invalid checksum is rejected immediately (prevents spoofed callbacks).

    PhonePe callback format:
        POST body: { "response": "<base64_encoded_json>" }
        Header:    X-VERIFY: <sha256>###<salt_index>

    Security:
        ✅ CSRF exempted (PhonePe cannot send CSRF tokens)
        ✅ X-VERIFY checksum verified before any DB writes
        ✅ Idempotent fulfillment (safe to call multiple times)
        ✅ Only PAYMENT_SUCCESS triggers order confirmation
        ✅ Failed payments update Transaction.status to 'failed'
    """
    permission_classes = [AllowAny]  # PhonePe cannot authenticate as a user

    def post(self, request) -> Response:
        from django.conf import settings as django_settings

        # ── Extract and validate the callback payload ─────────────────
        x_verify = request.META.get("HTTP_X_VERIFY", "")
        body_data = request.data

        base64_response = body_data.get("response", "")
        if not base64_response:
            logger.warning("PhonePe callback received with empty response body")
            return Response(
                {"error": "Invalid callback payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Verify checksum BEFORE decoding payload ───────────────────
        salt_key = django_settings.PHONEPE_SALT_KEY
        salt_index = str(django_settings.PHONEPE_SALT_INDEX)

        if not _verify_callback_checksum(x_verify, base64_response, salt_key, salt_index):
            logger.critical(
                "PhonePe callback checksum FAILED — possible spoofing attempt. "
                "IP: %s  X-VERIFY: %s",
                get_client_ip(request), x_verify[:50],
            )
            audit_log(
                action="PHONEPE_CALLBACK_CHECKSUM_FAILED",
                details={"ip": get_client_ip(request)},
                severity="CRITICAL",
            )
            return Response(
                {"error": "Invalid signature."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Decode and parse the verified payload ─────────────────────
        try:
            decoded = base64.b64decode(base64_response).decode("utf-8")
            payload = json.loads(decoded)
        except Exception as e:
            logger.error("PhonePe callback payload decode failed: %s", e)
            return Response(
                {"error": "Malformed callback payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Extract key fields from payload ───────────────────────────
        phonepe_code = payload.get("code", "")
        data = payload.get("data", {})
        merchant_transaction_id = data.get("merchantTransactionId", "")
        phonepe_transaction_id = data.get("transactionId", "")
        state = data.get("state", "FAILED")

        if not merchant_transaction_id:
            logger.error("PhonePe callback missing merchantTransactionId: %s", payload)
            return Response(
                {"error": "Missing transaction ID."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Process based on payment state ────────────────────────────
        if phonepe_code == "PAYMENT_SUCCESS" and state == "COMPLETED":
            # Update phonepe_transaction_id before fulfillment
            try:
                txn = Transaction.objects.get(
                    merchant_transaction_id=merchant_transaction_id
                )
                txn.phonepe_transaction_id = phonepe_transaction_id
                txn.save(update_fields=["phonepe_transaction_id"])
            except Transaction.DoesNotExist:
                logger.error(
                    "Callback: no Transaction for merchant_txn_id=%s",
                    merchant_transaction_id,
                )

            fulfilled = fulfill_order_after_payment(merchant_transaction_id)
            if fulfilled:
                logger.info(
                    "PhonePe callback: order fulfilled for txn %s",
                    merchant_transaction_id,
                )
                return Response({"status": "OK"})
            return Response(
                {"status": "ALREADY_PROCESSED"},
                status=status.HTTP_200_OK,
            )

        elif phonepe_code in ("PAYMENT_ERROR", "PAYMENT_DECLINED", "TIMED_OUT"):
            # Mark transaction and order as failed
            try:
                txn = Transaction.objects.get(
                    merchant_transaction_id=merchant_transaction_id
                )
                txn.status = "failed"
                txn.phonepe_transaction_id = phonepe_transaction_id
                txn.save(update_fields=["status", "phonepe_transaction_id"])
                # Transaction.save() auto-cancels the linked order
            except Transaction.DoesNotExist:
                logger.warning(
                    "Callback: failed payment for unknown txn %s",
                    merchant_transaction_id,
                )

            audit_log(
                action="PHONEPE_PAYMENT_FAILED_CALLBACK",
                details={
                    "merchant_transaction_id": merchant_transaction_id,
                    "phonepe_code": phonepe_code,
                },
                severity="WARNING",
            )
            return Response({"status": "OK"})

        else:
            # PAYMENT_PENDING or unknown state — do nothing (wait for next callback)
            logger.info(
                "PhonePe callback: unhandled state %s / code %s for txn %s",
                state, phonepe_code, merchant_transaction_id,
            )
            return Response({"status": "PENDING"})


# ─────────────────────────────────────────────────────────────────────────────
# 4. PAYMENT STATUS (Called from redirect URL after user returns)
# ─────────────────────────────────────────────────────────────────────────────

class PaymentStatusView(APIView):
    """
    Verify payment status after PhonePe redirects the user back.

    The client calls this with the merchant_transaction_id to get the
    authoritative payment result. We poll PhonePe's Status API directly —
    we never trust redirect URL parameters.

    GET /api/payments/status/<merchant_transaction_id>/

    Security:
        ✅ Authentication required — user can only check their own orders
        ✅ Status comes from PhonePe API, not from URL/client parameters
        ✅ On COMPLETED, triggers fulfillment if callback hasn't run yet
           (handles rare cases where callback fires before/after redirect)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, merchant_transaction_id: str) -> Response:
        # Verify this transaction belongs to the authenticated user
        try:
            txn = Transaction.objects.select_related("order__user").get(
                merchant_transaction_id=merchant_transaction_id,
                order__user=request.user,
            )
        except Transaction.DoesNotExist:
            return Response(
                {"error": "Transaction not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # If already paid (callback already ran), return immediately
        if txn.status == "paid":
            return Response({
                "payment_status": "COMPLETED",
                "order_status": txn.order.status,
                "order_number": txn.order.order_number,
                "total_amount": float(txn.order.total_amount),
                "merchant_transaction_id": merchant_transaction_id,
                "message": "Payment confirmed.",
            })

        # Poll PhonePe for authoritative status
        result = check_payment_status(merchant_transaction_id)

        if result["status"] == "COMPLETED" and result["success"]:
            # Update PhonePe transaction ID if we have it
            if result.get("phonepe_transaction_id"):
                txn.phonepe_transaction_id = result["phonepe_transaction_id"]
                txn.save(update_fields=["phonepe_transaction_id"])

            # Fulfill if callback hasn't done so already (idempotent)
            fulfill_order_after_payment(merchant_transaction_id)
            txn.refresh_from_db()

            return Response({
                "payment_status": "COMPLETED",
                "order_status": txn.order.status,
                "order_number": txn.order.order_number,
                "total_amount": float(txn.order.total_amount),
                "merchant_transaction_id": merchant_transaction_id,
                "message": "Payment successful! Your order is confirmed.",
            })

        elif result["status"] == "FAILED":
            txn.status = "failed"
            txn.save(update_fields=["status"])
            return Response({
                "payment_status": "FAILED",
                "order_status": txn.order.status,
                "order_number": txn.order.order_number,
                "total_amount": float(txn.order.total_amount),
                "merchant_transaction_id": merchant_transaction_id,
                "message": "Payment failed. Please try again.",
            })

        else:
            # PENDING or UNKNOWN
            return Response({
                "payment_status": result["status"],
                "order_status": txn.order.status,
                "order_number": txn.order.order_number,
                "total_amount": float(txn.order.total_amount),
                "merchant_transaction_id": merchant_transaction_id,
                "message": "Payment is being processed. Please wait.",
            })
