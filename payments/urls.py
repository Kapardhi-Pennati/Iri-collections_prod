from django.urls import path
from . import views

urlpatterns = [
    # Gateway health check — safe for monitoring systems
    path("health-check/", views.payment_health_check, name="payment-health-check"),

    # Initiate a PhonePe payment session for a pending order
    path("initiate/", views.InitiatePaymentView.as_view(), name="payment-initiate"),

    # PhonePe server-to-server callback (replaces Stripe webhook)
    # Must be CSRF-exempt — PhonePe cannot send CSRF tokens
    path("callback/", views.PhonePeCallbackView.as_view(), name="phonepe-callback"),

    # Verify payment result after PhonePe redirects the user back
    path(
        "status/<str:merchant_transaction_id>/",
        views.PaymentStatusView.as_view(),
        name="payment-status",
    ),
]
