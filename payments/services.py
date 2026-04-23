"""
payments/services.py — UPI QR Code Payment (No external gateway)

This file is intentionally minimal. The static UPI QR code approach
doesn't need any external payment gateway service layer.

All payment logic is handled directly in payments/views.py:
  - Customer uploads screenshot → UploadPaymentProofView
  - Admin approves → ApprovePaymentView (stock deduction here)
  - Admin rejects → RejectPaymentView

The QR code is a static image served from /static/img/upi_qr.png
and cannot be modified even if the site is compromised.
"""
