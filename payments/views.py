"""
Manual UPI payment flow with atomic stock settlement.
"""

import io
import logging
import qrcode

from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsCustomerUser
from core.security import audit_log, get_client_ip
from core.throttling import AdminMutationThrottle, PaymentThrottle
from store.models import Order, OrderItem, Product, StockReservation, Transaction
from store.views import IsAdminRole

logger = logging.getLogger("payments")


def _validate_upi_reference(value: str) -> str:
    reference = str(value or "").strip()
    if not reference:
        raise ValueError("UPI Transaction ID is required.")
    if len(reference) < 8 or len(reference) > 64:
        raise ValueError("UPI Transaction ID must be between 8 and 64 characters.")
    if not all(char.isalnum() or char in {"-", "_"} for char in reference):
        raise ValueError("UPI Transaction ID may only contain letters, numbers, '-' or '_'.")
    return reference


def _validate_screenshot(screenshot) -> None:
    if screenshot is None:
        return
    if screenshot.size == 0:
        raise ValueError("The uploaded screenshot is empty (0 bytes).")
    if screenshot.content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        raise ValueError("Invalid file type. Only JPEG, PNG, WebP, and GIF are allowed.")
    if screenshot.size > 5 * 1024 * 1024:
        raise ValueError("File too large. Maximum 5 MB allowed.")


def _load_locked_order(order_id: int, user=None) -> Order:
    queryset = Order.objects.select_for_update()
    if user is not None:
        queryset = queryset.filter(user=user)
    return queryset.get(id=order_id)


def _ensure_order_reservations(order: Order, acting_user) -> list[OrderItem]:
    """
    Ensure the order still has an active reservation for every line item.

    Upload-proof requests extend reservations instead of deducting stock. Admin
    approval is the only place where on-hand stock is decremented.
    """

    order_items = list(OrderItem.objects.filter(order=order).select_related("product"))
    product_ids = [item.product_id for item in order_items if item.product_id]
    products = Product.objects.select_for_update().filter(id__in=product_ids)
    product_map = {product.id: product for product in products}
    reservation_expiry = timezone.now() + timedelta(hours=24)

    for item in order_items:
        if not item.product_id:
            continue

        product = product_map.get(item.product_id)
        if not product:
            raise ValueError(
                f"Product for line item {item.product_name} is no longer available."
            )

        reservation = (
            StockReservation.objects.select_for_update()
            .filter(order=order, product=product)
            .first()
        )
        held_quantity = (
            reservation.quantity
            if reservation and reservation.expires_at > timezone.now()
            else 0
        )
        reservable_quantity = product.get_available_stock() + held_quantity
        if item.quantity > reservable_quantity:
            raise ValueError(
                f"Insufficient reserved stock for {product.name}. "
                "The payment window expired and stock is no longer available."
            )

        if reservation:
            reservation.quantity = item.quantity
            reservation.expires_at = reservation_expiry
            reservation.save(update_fields=["quantity", "expires_at"])
        else:
            StockReservation.objects.create(
                user=acting_user,
                product=product,
                order=order,
                quantity=item.quantity,
                expires_at=reservation_expiry,
            )

    return order_items


class UploadPaymentProofView(APIView):
    permission_classes = [IsCustomerUser]
    throttle_classes = [PaymentThrottle]
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

        try:
            upi_reference = _validate_upi_reference(request.data.get("upi_reference_id"))
            screenshot = request.FILES.get("payment_screenshot")
            _validate_screenshot(screenshot)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            order = _load_locked_order(order_id, user=request.user)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if order.status != "pending":
            return Response(
                {
                    "error": f"Order is already {order.status}. Cannot upload payment proof."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            _ensure_order_reservations(order, request.user)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)

        txn = Transaction.objects.select_for_update().filter(order=order).first()
        if txn and txn.status in {"paid", "rejected"}:
            return Response(
                {"error": f"Payment is already {txn.status}. Cannot upload proof again."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if txn:
            txn.payment_screenshot = screenshot
            txn.upi_reference_id = upi_reference
            txn.status = "pending_verification"
            txn.save(update_fields=["payment_screenshot", "upi_reference_id", "status"])
        else:
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
                "has_screenshot": "true" if screenshot else "false",
                "file_size": str(screenshot.size) if screenshot else "0",
            },
            severity="INFO",
            ip_address=get_client_ip(request),
        )

        return Response(
            {
                "message": "Payment proof uploaded successfully. Awaiting admin verification.",
                "order_number": order.order_number,
                "status": "pending_verification",
            }
        )


class ApprovePaymentView(APIView):
    permission_classes = [IsAdminRole]
    throttle_classes = [AdminMutationThrottle]

    @transaction.atomic
    def post(self, request, pk) -> Response:
        try:
            txn = Transaction.objects.select_for_update().get(order_id=pk)
        except Transaction.DoesNotExist:
            return Response(
                {"error": "No payment proof found for this order."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if txn.status == "paid":
            return Response({"message": "Payment already approved.", "status": "paid"})

        if txn.status == "rejected":
            return Response(
                {"error": "Payment was already rejected. Cannot approve."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order = _load_locked_order(pk)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        admin_notes = str(request.data.get("admin_notes", "")).strip()
        order_items = list(OrderItem.objects.filter(order=order).select_related("product"))
        product_ids = [item.product_id for item in order_items if item.product_id]
        products = Product.objects.select_for_update().filter(id__in=product_ids)
        product_map = {product.id: product for product in products}

        for item in order_items:
            if not item.product_id:
                continue

            product = product_map.get(item.product_id)
            if not product:
                return Response(
                    {"error": f"Product for line item {item.product_name} is no longer available."},
                    status=status.HTTP_409_CONFLICT,
                )

            reservation = (
                StockReservation.objects.select_for_update()
                .filter(order=order, product=product)
                .first()
            )
            held_quantity = (
                reservation.quantity
                if reservation and reservation.expires_at > timezone.now()
                else 0
            )
            reservable_quantity = product.get_available_stock() + held_quantity
            if item.quantity > reservable_quantity:
                return Response(
                    {
                        "error": (
                            f"Inventory for {product.name} is no longer available. "
                            "Reject and recreate the order."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        for item in order_items:
            if not item.product_id:
                continue
            product = product_map[item.product_id]
            product.stock -= item.quantity
            product.save(update_fields=["stock"])

        StockReservation.objects.filter(order=order).delete()

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

        return Response(
            {
                "message": "Payment approved. Order confirmed.",
                "order_number": order.order_number,
                "status": "confirmed",
            }
        )


class RejectPaymentView(APIView):
    permission_classes = [IsAdminRole]
    throttle_classes = [AdminMutationThrottle]

    @transaction.atomic
    def post(self, request, pk) -> Response:
        try:
            txn = Transaction.objects.select_related("order").select_for_update().get(
                order_id=pk
            )
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
            return Response(
                {"message": "Payment already rejected.", "status": "rejected"}
            )

        try:
            order = _load_locked_order(pk)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        admin_notes = str(request.data.get("admin_notes", "")).strip()

        StockReservation.objects.filter(order=order).delete()

        txn.status = "rejected"
        txn.admin_notes = admin_notes
        txn.save(update_fields=["status", "admin_notes"])

        if order.status == "pending":
            order.status = "cancelled"
            order.save(update_fields=["status"])

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

        return Response(
            {
                "message": "Payment rejected. Order has been cancelled.",
                "order_number": txn.order.order_number,
                "status": "rejected",
            }
        )


class GenerateUPIQRView(APIView):
    permission_classes = [IsCustomerUser]
    throttle_classes = [PaymentThrottle]

    def get(self, request) -> HttpResponse:
        amount = request.query_params.get("amount", "0")
        try:
            amount_val = float(amount)
            if amount_val < 0:
                amount_val = 0
            amount = f"{amount_val:.2f}"
        except ValueError:
            amount = "0.00"

        upi_id = getattr(settings, "UPI_ID", "")
        upi_name = getattr(settings, "UPI_DISPLAY_NAME", "")
        note = request.query_params.get("note", "Payment")

        upi_uri = f"upi://pay?pa={upi_id}&pn={upi_name}&am={amount}&cu=INR&tn={note}"

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(upi_uri)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        return HttpResponse(buf.getvalue(), content_type="image/png")
