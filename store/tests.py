from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from store.models import Category, Order, Product, Transaction


@override_settings(
	SECURE_SSL_REDIRECT=False,
	CELERY_TASK_ALWAYS_EAGER=True,
	CELERY_TASK_EAGER_PROPAGATES=True,
	CELERY_BROKER_URL="memory://",
	CELERY_RESULT_BACKEND="cache+memory://",
)
class OrderStatusSecurityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_user(
            username="admin-user",
            email="admin@example.com",
            password="test-pass-123",
            role="admin",
        )
        self.customer = User.objects.create_user(
            username="customer-user",
            email="customer@example.com",
            password="test-pass-123",
        )
        self.category = Category.objects.create(name="Necklaces")
        self.product = Product.objects.create(
            name="Pearl Necklace",
            description="Classic pearls",
            price=Decimal("499.00"),
            stock=5,
            category=self.category,
        )
        self.order = Order.objects.create(
            user=self.customer,
            total_amount=Decimal("579.00"),
            shipping_fee=Decimal("80.00"),
            status="pending",
            shipping_address="MG Road, Chennai",
            phone="+919876543210",
        )
        self.txn = Transaction.objects.create(
            order=self.order,
            amount=self.order.total_amount,
            status="pending_verification",
        )
        self.status_url = reverse("admin-order-status", kwargs={"pk": self.order.id})
        self.client.force_authenticate(user=self.admin_user)

    def test_admin_cannot_confirm_unpaid_order(self):
        response = self.client.patch(
            self.status_url,
            {"status": "confirmed"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")

    def test_admin_can_confirm_only_after_paid_transaction(self):
        self.txn.status = "paid"
        self.txn.save(update_fields=["status"])

        response = self.client.patch(
            self.status_url,
            {"status": "confirmed"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "confirmed")

    def test_rejected_transaction_does_not_implicitly_cancel_without_workflow(self):
        self.txn.status = "rejected"
        self.txn.save(update_fields=["status"])

        self.order.refresh_from_db()
        self.txn.refresh_from_db()
        self.assertEqual(self.order.status, "pending")
        self.assertEqual(self.txn.status, "rejected")


class SeedDataRandomGenerationTests(TestCase):
    def test_seed_data_generates_random_items_for_every_category(self):
        call_command(
            "seed_data",
            random_items_per_category=1,
            random_seed=12345,
            verbosity=0,
        )

        expected_categories = {"Necklaces", "Earrings", "Bracelets", "Rings", "Anklets"}
        category_names = set(Category.objects.values_list("name", flat=True))
        self.assertTrue(expected_categories.issubset(category_names))

        for category_name in expected_categories:
            self.assertGreater(
                Product.objects.filter(category__name=category_name).count(),
                0,
                msg=f"Expected seeded products for category {category_name}",
            )
