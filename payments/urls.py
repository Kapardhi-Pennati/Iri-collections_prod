from django.urls import path
from . import views

urlpatterns = [
    # Secure server-side QR generation
    path("qr-code/", views.GenerateUPIQRView.as_view(), name="payment-qr-code"),
]
