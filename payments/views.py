"""
payments/views.py — Static UPI QR Code Payment Views

Endpoints:
  POST /api/payments/upload-proof/    → Customer uploads payment screenshot + UTR
  POST /api/payments/approve/<pk>/    → Admin approves payment (confirms order)
  POST /api/payments/reject/<pk>/     → Admin rejects payment (cancels order)

Flow:
  1. Customer places order → order status = 'pending'
  2. Customer scans static UPI QR, pays, uploads screenshot + UTR
  3. On proof upload → stock is reserved (deducted)
  4. Admin reviews screenshot in dashboard
  5. Admin approves → order confirmed
  5. Admin rejects → order cancelled

Security:
  ✅ QR code is a static image — cannot be changed even if site is compromised
  ✅ Admin manually verifies every payment before confirming
  ✅ Stock is reserved at proof upload and restored on rejection
  ✅ All actions are audit-logged
"""

import logging

from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import transaction

from core.security import audit_log, get_client_ip
from store.models import Order, OrderItem, Product, Transaction
from store.views import IsAdminRole

logger = logging.getLogger("payments")


# ─────────────────────────────────────────────────────────────────────────────
# 1. UPLOAD PAYMENT PROOF (Customer)
# ─────────────────────────────────────────────────────────────────────────────

class UploadPaymentProofView(APIView):
    """
    Customer uploads UPI payment screenshot and UTR reference for a pending order.

    Request:
        POST /api/payments/upload-proof/
        Content-Type: multipart/form-data
        Body:
          - order_id: int (required)
          - payment_screenshot: file (required, image)
          - upi_reference_id: str (optional but recommended)

    Security:
        ✅ Only the order owner can upload proof
        ✅ Only pending orders accept proof uploads
        ✅ File upload validated (image only)
        ✅ Audit logged
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @transaction.atomic
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

        # ── Fetch order with lock (scoped to this user — IDOR guard) ──
        try:
            order = Order.objects.select_for_update().get(id=order_id, user=request.user)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if order.status != "pending":
            return Response(
                {"error": f"Order is already {order.status}. Cannot upload payment proof."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Validate screenshot file ─────────────────────────────────
        screenshot = request.FILES.get("payment_screenshot")
        if not screenshot:
            return Response(
                {"error": "payment_screenshot file is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate file type
        allowed_types = ["image/jpeg", "image/png", "image/webp", "image/gif"]
        if screenshot.content_type not in allowed_types:
            return Response(
                {"error": "Invalid file type. Only JPEG, PNG, WebP, and GIF are allowed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate file size (max 5 MB)
        if screenshot.size > 5 * 1024 * 1024:
            return Response(
                {"error": "File too large. Maximum 5 MB allowed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        upi_reference = str(request.data.get("upi_reference_id", "")).strip()

        # ── Create transaction and reserve stock on first upload ──────
        txn = Transaction.objects.filter(order=order).first()

        if txn and txn.status == "pending_verification":
            # Proof re-upload before admin action: update proof only.
            txn.payment_screenshot = screenshot
            txn.upi_reference_id = upi_reference
            txn.save(update_fields=["payment_screenshot", "upi_reference_id"])
        else:
            if txn and txn.status in {"paid", "rejected"}:
                return Response(
                    {"error": f"Payment is already {txn.status}. Cannot upload proof again."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            order_items = OrderItem.objects.filter(order=order).select_related("product")
            for item in order_items:
                if not item.product:
                    continue

                product = Product.objects.select_for_update().get(id=item.product_id)
                if product.stock < item.quantity:
                    return Response(
                        {
                            "error": (
                                f"Insufficient stock for {product.name}. "
                                "Please contact support."
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            for item in order_items:
                if not item.product:
                    continue
                product = Product.objects.select_for_update().get(id=item.product_id)
                product.stock -= item.quantity
                product.save(update_fields=["stock"])

            txn = Transaction.objects.create(
                order=order,
                payment_screenshot=screenshot,
                upi_reference_id=upi_reference,
                amount=order.total_amount,
                status="pending_verification",
            )

        audit_log(
            action="PAYMENT_PROOF_UPLOADED",
            user_id=request.user.id,
            details={
                "order_id": str(order.id),
                "order_number": order.order_number,
                "upi_reference_id": upi_reference,
                "file_size": str(screenshot.size),
            },
            severity="INFO",
            ip_address=get_client_ip(request),
        )

        return Response({
            "message": "Payment proof uploaded successfully. Awaiting admin verification.",
            "order_number": order.order_number,
            "status": "pending_verification",
        })


# ─────────────────────────────────────────────────────────────────────────────
# 2. APPROVE PAYMENT (Admin)
# ─────────────────────────────────────────────────────────────────────────────

class ApprovePaymentView(APIView):
    """
    Admin approves a payment after verifying the screenshot.

    This atomically:
      1. Sets transaction status to 'paid'
      2. Confirms the order

    Security:
        ✅ Admin-only access
        ✅ Atomic transaction with row-level locking
        ✅ Idempotent (safe to call twice)
        ✅ Stock already reserved at proof upload
    """
    permission_classes = [IsAdminRole]

    @transaction.atomic
    def post(self, request, pk) -> Response:
        try:
            txn = Transaction.objects.select_for_update().get(order_id=pk)
        except Transaction.DoesNotExist:
            return Response(
                {"error": "No payment proof found for this order."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Idempotency guard
        if txn.status == "paid":
            return Response({
                "message": "Payment already approved.",
                "status": "paid",
            })

        if txn.status == "rejected":
            return Response(
                {"error": "Payment was already rejected. Cannot approve."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        admin_notes = str(request.data.get("admin_notes", "")).strip()

        # ── Lock order ────────────────────────────────────────────────
        order = Order.objects.select_for_update().get(id=pk)

        # ── Confirm transaction and order ─────────────────────────────
        txn.status = "paid"
        txn.admin_notes = admin_notes
        txn.save(update_fields=["status", "admin_notes"])

        order.status = "confirmed"
        order.save(update_fields=["status"])

        audit_log(
            action="PAYMENT_APPROVED",
            user_id=request.user.id,
            details={
                "order_id": str(order.id),
                "order_number": order.order_number,
                "upi_reference_id": txn.upi_reference_id,
                "total": str(float(order.total_amount)),
                "admin_notes": admin_notes,
            },
            severity="INFO",
        )

        return Response({
            "message": "Payment approved. Order confirmed.",
            "order_number": order.order_number,
            "status": "confirmed",
        })


# ─────────────────────────────────────────────────────────────────────────────
# 3. REJECT PAYMENT (Admin)
# ─────────────────────────────────────────────────────────────────────────────

class RejectPaymentView(APIView):
    """
    Admin rejects a payment proof (screenshot doesn't match, fake, etc.).

    This:
      1. Sets transaction status to 'rejected'
      2. Cancels the order (via Transaction.save() hook)

    Security:
        ✅ Admin-only access
        ✅ Audit logged
    """
    permission_classes = [IsAdminRole]

    @transaction.atomic
    def post(self, request, pk) -> Response:
        try:
            txn = Transaction.objects.select_related("order").select_for_update().get(order_id=pk)
        except Transaction.DoesNotExist:
            return Response(
                {"error": "No payment proof found for this order."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if txn.status == "paid":
            return Response(
                {"error": "Payment already approved. Cannot reject."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if txn.status == "rejected":
            return Response({
                "message": "Payment already rejected.",
                "status": "rejected",
            })

        admin_notes = str(request.data.get("admin_notes", "")).strip()

        order = Order.objects.select_for_update().get(id=pk)
        order_items = OrderItem.objects.filter(order=order).select_related("product")
        for item in order_items:
            if not item.product:
                continue
            product = Product.objects.select_for_update().get(id=item.product_id)
            product.stock += item.quantity
            product.save(update_fields=["stock"])

        txn.status = "rejected"
        txn.admin_notes = admin_notes
        txn.save(update_fields=["status", "admin_notes"])
        # Transaction.save() hook auto-cancels the order

        audit_log(
            action="PAYMENT_REJECTED",
            user_id=request.user.id,
            details={
                "order_id": str(txn.order.id),
                "order_number": txn.order.order_number,
                "upi_reference_id": txn.upi_reference_id,
                "admin_notes": admin_notes,
            },
            severity="WARNING",
        )

        return Response({
            "message": "Payment rejected. Order has been cancelled.",
            "order_number": txn.order.order_number,
            "status": "rejected",
        })
