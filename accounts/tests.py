from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import OTP, User


@override_settings(
    SECURE_SSL_REDIRECT=False,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    CELERY_BROKER_URL="memory://",
    CELERY_RESULT_BACKEND="cache+memory://",
    USE_LOCAL_CACHE=True,
)
class OTPSessionSecurityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.verify_url = reverse("verify_otp")
        self.register_url = reverse("register")
        self.email = "new-user@example.com"
        self.otp_code = "123456"
        OTP.objects.create(email=self.email, otp_code=self.otp_code)

    def test_verify_otp_returns_session_token(self):
        response = self.client.post(
            self.verify_url,
            {"email": self.email, "otp_code": self.otp_code},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("otp_session_token", response.data)

    def test_register_requires_verified_otp_session_token(self):
        otp = OTP.objects.get(email=self.email)
        otp.is_verified = True
        otp.save(update_fields=["is_verified"])

        response = self.client.post(
            self.register_url,
            {
                "email": self.email,
                "username": "new-user",
                "full_name": "New User",
                "phone": "+919876543210",
                "password": "StrongPass123!",
                "password2": "StrongPass123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email=self.email).exists())
