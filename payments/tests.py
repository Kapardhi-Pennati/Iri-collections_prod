import base64
import hashlib
import json
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from store.models import Category, Order, OrderItem, Product, Transaction


@override_settings(
	PHONEPE_SALT_KEY="unit-test-salt",
	PHONEPE_SALT_INDEX="1",
	SECURE_SSL_REDIRECT=False,
	CELERY_TASK_ALWAYS_EAGER=True,
	CELERY_TASK_EAGER_PROPAGATES=True,
	CELERY_BROKER_URL="memory://",
	CELERY_RESULT_BACKEND="cache+memory://",
)
class PhonePeCallbackFlowTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.user = User.objects.create_user(
			username="buyer",
			email="buyer@example.com",
			password="test-pass-123",
		)
		self.category = Category.objects.create(name="Rings")
		self.product = Product.objects.create(
			name="Gold Ring",
			description="22k ring",
			price=Decimal("100.00"),
			stock=10,
			category=self.category,
		)
		self.callback_url = reverse("phonepe-callback")

	def _create_order_with_transaction(
		self,
		*,
		order_status: str,
		txn_status: str,
		merchant_txn_id: str,
		quantity: int = 1,
	):
		order = Order.objects.create(
			user=self.user,
			total_amount=Decimal("150.00"),
			shipping_fee=Decimal("50.00"),
			status=order_status,
			shipping_address="221B, Baker Street",
			phone="+919876543210",
		)
		OrderItem.objects.create(
			order=order,
			product=self.product,
			product_name=self.product.name,
			quantity=quantity,
			price_at_purchase=self.product.price,
		)
		txn = Transaction.objects.create(
			order=order,
			merchant_transaction_id=merchant_txn_id,
			amount=order.total_amount,
			status=txn_status,
		)
		return order, txn

	@staticmethod
	def _encode_payload(payload: dict) -> str:
		return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")

	@staticmethod
	def _sign(base64_response: str) -> str:
		digest = hashlib.sha256(f"{base64_response}unit-test-salt".encode("utf-8")).hexdigest()
		return f"{digest}###1"

	def test_callback_rejects_invalid_signature(self):
		order, txn = self._create_order_with_transaction(
			order_status="pending",
			txn_status="created",
			merchant_txn_id="MTXN-CB-BAD-SIG",
		)
		payload = {
			"code": "PAYMENT_SUCCESS",
			"data": {
				"merchantTransactionId": txn.merchant_transaction_id,
				"transactionId": "PHONEPE-123",
				"state": "COMPLETED",
			},
		}
		encoded = self._encode_payload(payload)

		response = self.client.post(
			self.callback_url,
			{"response": encoded},
			format="json",
			HTTP_X_VERIFY="invalid###1",
		)

		self.assertEqual(response.status_code, 400)
		txn.refresh_from_db()
		order.refresh_from_db()
		self.assertEqual(txn.status, "created")
		self.assertEqual(order.status, "pending")

	def test_failed_callback_does_not_downgrade_paid_order(self):
		order, txn = self._create_order_with_transaction(
			order_status="confirmed",
			txn_status="paid",
			merchant_txn_id="MTXN-CB-PAID",
		)
		payload = {
			"code": "PAYMENT_DECLINED",
			"data": {
				"merchantTransactionId": txn.merchant_transaction_id,
				"transactionId": "PHONEPE-FAILED-1",
				"state": "FAILED",
			},
		}
		encoded = self._encode_payload(payload)
		signature = self._sign(encoded)

		response = self.client.post(
			self.callback_url,
			{"response": encoded},
			format="json",
			HTTP_X_VERIFY=signature,
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data.get("status"), "IGNORED_ALREADY_PAID")
		txn.refresh_from_db()
		order.refresh_from_db()
		self.assertEqual(txn.status, "paid")
		self.assertEqual(order.status, "confirmed")

	def test_success_callback_confirms_order_and_deducts_stock(self):
		order, txn = self._create_order_with_transaction(
			order_status="pending",
			txn_status="created",
			merchant_txn_id="MTXN-CB-SUCCESS",
			quantity=2,
		)
		payload = {
			"code": "PAYMENT_SUCCESS",
			"data": {
				"merchantTransactionId": txn.merchant_transaction_id,
				"transactionId": "PHONEPE-SUCCESS-1",
				"state": "COMPLETED",
			},
		}
		encoded = self._encode_payload(payload)
		signature = self._sign(encoded)

		response = self.client.post(
			self.callback_url,
			{"response": encoded},
			format="json",
			HTTP_X_VERIFY=signature,
		)

		self.assertEqual(response.status_code, 200)
		txn.refresh_from_db()
		order.refresh_from_db()
		self.product.refresh_from_db()
		self.assertEqual(txn.status, "paid")
		self.assertEqual(order.status, "confirmed")
		self.assertEqual(self.product.stock, 8)
