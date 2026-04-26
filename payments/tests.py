from decimal import Decimal
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from store.models import Category, Order, OrderItem, Product, StockReservation, Transaction


@override_settings(
    SECURE_SSL_REDIRECT=False,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    CELERY_BROKER_URL="memory://",
    CELERY_RESULT_BACKEND="cache+memory://",
)
class UpiProofPaymentFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="buyer",
            email="buyer@example.com",
            password="test-pass-123",
        )
        self.admin = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="test-pass-123",
            role="admin",
        )
        self.category = Category.objects.create(name="Rings")
        self.product = Product.objects.create(
            name="Gold Ring",
            description="22k ring",
            price=Decimal("100.00"),
            stock=10,
            category=self.category,
        )
        self.order = Order.objects.create(
            user=self.user,
            total_amount=Decimal("150.00"),
            shipping_fee=Decimal("50.00"),
            status="pending",
            shipping_address="221B, Baker Street",
            phone="+919876543210",
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            quantity=2,
            price_at_purchase=self.product.price,
        )
        self.upload_url = reverse("payment-upload-proof")
        self.approve_url = reverse("payment-approve", kwargs={"pk": self.order.id})
        self.reject_url = reverse("payment-reject", kwargs={"pk": self.order.id})

    @staticmethod
    def _proof_image(name: str = "proof.png"):
        return SimpleUploadedFile(
            name=name,
            content=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
            content_type="image/png",
        )

    def test_customer_can_upload_payment_proof(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            self.upload_url,
            {
                "order_id": self.order.id,
                "payment_screenshot": self._proof_image(),
                "upi_reference_id": "123456789012",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        txn = Transaction.objects.get(order=self.order)
        self.assertEqual(txn.status, "pending_verification")
        self.assertEqual(txn.upi_reference_id, "123456789012")
        self.assertTrue(bool(txn.payment_screenshot))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertTrue(
            StockReservation.objects.filter(order=self.order, product=self.product).exists()
        )

    def test_admin_approve_marks_paid_and_confirms_order(self):
        StockReservation.objects.create(
            user=self.user,
            product=self.product,
            order=self.order,
            quantity=2,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        Transaction.objects.create(
            order=self.order,
            amount=self.order.total_amount,
            status="pending_verification",
            upi_reference_id="555666777888",
        )
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(self.approve_url, {"admin_notes": "Verified UTR"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        txn = Transaction.objects.get(order=self.order)
        self.assertEqual(txn.status, "paid")
        self.assertEqual(self.order.status, "confirmed")
        self.assertEqual(self.product.stock, 8)
        self.assertFalse(StockReservation.objects.filter(order=self.order).exists())

    def test_admin_reject_releases_reservations_and_cancels_order(self):
        StockReservation.objects.create(
            user=self.user,
            product=self.product,
            order=self.order,
            quantity=2,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        Transaction.objects.create(
            order=self.order,
            amount=self.order.total_amount,
            status="pending_verification",
        )
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(self.reject_url, {"admin_notes": "Proof mismatch"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        txn = Transaction.objects.get(order=self.order)
        self.assertEqual(txn.status, "rejected")
        self.assertEqual(self.order.status, "cancelled")
        self.assertEqual(self.product.stock, 10)
        self.assertFalse(StockReservation.objects.filter(order=self.order).exists())
