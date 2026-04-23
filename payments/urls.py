from django.urls import path
from . import views

urlpatterns = [
    # Customer: upload UPI payment screenshot + UTR reference
    path("upload-proof/", views.UploadPaymentProofView.as_view(), name="payment-upload-proof"),

    # Admin: approve payment after verifying screenshot
    path("approve/<int:pk>/", views.ApprovePaymentView.as_view(), name="payment-approve"),

    # Admin: reject payment (fake screenshot, etc.)
    path("reject/<int:pk>/", views.RejectPaymentView.as_view(), name="payment-reject"),
    
    # Secure server-side QR generation
    path("qr-code/", views.GenerateUPIQRView.as_view(), name="payment-qr-code"),
]
