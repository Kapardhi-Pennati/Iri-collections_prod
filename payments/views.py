"""
UPI QR code generation for the checkout payment step.
"""

import io
import logging
import qrcode

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from core.throttling import CheckoutThrottle

logger = logging.getLogger("payments")


class GenerateUPIQRView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [CheckoutThrottle]

    def get(self, request) -> HttpResponse:
        amount = request.query_params.get("amount", "0")
        try:
            amount_val = float(amount)
            if amount_val < 0 or amount_val > 999999:
                amount_val = 0
            amount = f"{amount_val:.2f}"
        except (ValueError, OverflowError):
            amount = "0.00"

        upi_id = str(getattr(settings, "UPI_ID", "your-upi-id@paytm")).strip()
        upi_name = str(getattr(settings, "UPI_DISPLAY_NAME", "Iri Collections")).strip()
        note = str(request.query_params.get("note", "Payment")).strip()[:80] or "Payment"
        ref = str(request.query_params.get("ref", "")).strip()[:50]

        # Build UPI URI — NO percent-encoding because this URI is
        # embedded in a QR code and scanned directly by GPay/PhonePe/Paytm.
        # Encoding turns spaces into %20 which apps display literally.
        upi_uri = (
            f"upi://pay"
            f"?pa={upi_id}"
            f"&pn={upi_name}"
            f"&am={amount}"
            f"&cu=INR"
            f"&tn={note}"
        )
        if ref:
            upi_uri += f"&tr={ref}"

        cache_key = f"payments:qr:{upi_uri}"
        cached_png = cache.get(cache_key)
        if cached_png:
            return HttpResponse(cached_png, content_type="image/png")

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
        png_bytes = buf.getvalue()
        cache.set(cache_key, png_bytes, 600)

        return HttpResponse(png_bytes, content_type="image/png")
