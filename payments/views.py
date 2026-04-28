"""
UPI QR code generation for the checkout payment step.
"""

import io
import logging
import qrcode
import urllib.parse

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

logger = logging.getLogger("payments")


class GenerateUPIQRView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []

    def get(self, request) -> HttpResponse:
        amount = request.query_params.get("amount", "0")
        try:
            amount_val = float(amount)
            if amount_val < 0:
                amount_val = 0
            amount = f"{amount_val:.2f}"
        except ValueError:
            amount = "0.00"

        upi_id = str(getattr(settings, "UPI_ID", "")).strip()
        upi_name = str(getattr(settings, "UPI_DISPLAY_NAME", "")).strip()
        note = str(request.query_params.get("note", "Payment")).strip()[:80] or "Payment"
        ref = str(request.query_params.get("ref", "")).strip()[:50]

        # Build UPI URI manually — GPay/PhonePe need minimal encoding
        # and @ in VPA must NOT be encoded.
        q = urllib.parse.quote
        upi_uri = (
            f"upi://pay"
            f"?pa={q(upi_id, safe='@.')}"
            f"&pn={q(upi_name, safe='')}"
            f"&am={q(amount, safe='.')}"
            f"&cu=INR"
            f"&tn={q(note, safe='')}"
        )
        if ref:
            upi_uri += f"&tr={q(ref, safe='')}"

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
