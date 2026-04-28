from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient


@override_settings(
    SECURE_SSL_REDIRECT=False,
)
class GenerateUPIQRViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("payment-qr-code")

    def test_qr_code_returns_png(self):
        response = self.client.get(self.url, {"amount": "150.00", "note": "Test"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_qr_code_with_order_id(self):
        response = self.client.get(
            self.url,
            {"amount": "250.00", "note": "Iri-Order-42", "ref": "42"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_qr_code_handles_invalid_amount(self):
        response = self.client.get(self.url, {"amount": "abc"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
