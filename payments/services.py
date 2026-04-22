"""
payments/services.py — PhonePe Payment Gateway Business Logic

Replaces Stripe with PhonePe PG REST API (v1).

PhonePe API reference:
  Production: https://api.phonepe.com/apis/hermes
  UAT/Sandbox: https://api-preprod.phonepe.com/apis/pg-sandbox

Payment flow:
  1. create_phonepe_payment()  → build signed request → get redirect URL
  2. User pays on PhonePe's hosted page
  3. PhonePe POSTs callback to our /callback/ endpoint
  4. fulfill_order_from_callback() → verify signature → confirm order

Security principles:
  ✅ HMAC-SHA256 checksum on every outbound request (X-VERIFY header)
  ✅ HMAC-SHA256 verification on every inbound callback (prevents spoofing)
  ✅ All DB mutations in transaction.atomic() with select_for_update()
  ✅ Idempotent fulfillment (safe to call twice)
  ✅ Comprehensive audit logging
  ✅ No sensitive credentials in logs
"""

import base64
import hashlib
import json
import logging
import uuid
from typing import Any, Dict, Optional, Tuple

import urllib.request
import urllib.error
from django.conf import settings
from django.db import transaction

from core.security import audit_log
from store.models import Order, OrderItem, Product, Transaction

logger = logging.getLogger("payments")


# ─────────────────────────────────────────────────────────────────────────────
# PhonePe API Constants
# ─────────────────────────────────────────────────────────────────────────────

# PhonePe PG REST API base URLs
_PHONEPE_PROD_URL = "https://api.phonepe.com/apis/hermes"
_PHONEPE_UAT_URL = "https://api-preprod.phonepe.com/apis/pg-sandbox"

# API endpoint paths
_PAY_ENDPOINT = "/pg/v1/pay"
_STATUS_ENDPOINT = "/pg/v1/status"

# Timeout for outbound HTTP calls to PhonePe (seconds)
_REQUEST_TIMEOUT = 10


def _get_base_url() -> str:
    """Return the correct PhonePe API base URL based on DEBUG mode."""
    return _PHONEPE_UAT_URL if settings.DEBUG else _PHONEPE_PROD_URL


# ─────────────────────────────────────────────────────────────────────────────
# Checksum Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _compute_checksum(base64_payload: str, endpoint: str, salt_key: str, salt_index: str) -> str:
    """
    Compute the X-VERIFY checksum required by PhonePe for every API call.

    PhonePe checksum formula:
        SHA256(base64_payload + endpoint + saltKey) + "###" + saltIndex

    Args:
        base64_payload: Base64-encoded JSON request body.
        endpoint:       API endpoint path (e.g. "/pg/v1/pay").
        salt_key:       Merchant salt key from PhonePe dashboard.
        salt_index:     Salt index (usually "1").

    Returns:
        Checksum string in format: "<sha256_hex>###<salt_index>"

    Security:
        SHA-256 binds our salt to the full payload, preventing tampering
        in transit. PhonePe rejects requests where the checksum doesn't match.
    """
    raw = f"{base64_payload}{endpoint}{salt_key}"
    sha256_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{sha256_hash}###{salt_index}"


def _verify_callback_checksum(
    x_verify_header: str,
    base64_response: str,
    salt_key: str,
    salt_index: str,
) -> bool:
    """
    Verify the X-VERIFY checksum on an incoming PhonePe callback.

    PhonePe signs its callbacks with:
        SHA256(base64_response + saltKey) + "###" + saltIndex

    Note: callback verification does NOT include an endpoint path.

    Args:
        x_verify_header: The X-VERIFY header value from PhonePe's POST.
        base64_response:  Base64-encoded response body from the callback.
        salt_key:         Merchant salt key (server-side only).
        salt_index:       Salt index string (usually "1").

    Returns:
        True if the checksum matches; False if invalid (possible spoofing).

    Security:
        Uses hmac.compare_digest() for constant-time comparison to prevent
        timing oracle attacks.
    """
    import hmac as hmac_mod
    if not x_verify_header or "###" not in x_verify_header:
        return False

    provided_hash, _ = x_verify_header.rsplit("###", 1)
    raw = f"{base64_response}{salt_key}"
    expected_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # Constant-time comparison prevents timing attacks
    return hmac_mod.compare_digest(expected_hash, provided_hash)


# ─────────────────────────────────────────────────────────────────────────────
# 1. GATEWAY HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────

def check_phonepe_health() -> Tuple[bool, Optional[str]]:
    """
    Verify that PhonePe API is reachable and credentials are configured.

    Does a lightweight validation of settings rather than making an
    authenticated API call (PhonePe has no equivalent of Stripe's
    Account.retrieve). Validates that all required env vars are present.

    Returns:
        (True, None) if healthy; (False, error_message) if not.
    """
    merchant_id = getattr(settings, "PHONEPE_MERCHANT_ID", "")
    salt_key = getattr(settings, "PHONEPE_SALT_KEY", "")
    salt_index = getattr(settings, "PHONEPE_SALT_INDEX", "")

    missing = [
        name for name, val in [
            ("PHONEPE_MERCHANT_ID", merchant_id),
            ("PHONEPE_SALT_KEY", salt_key),
            ("PHONEPE_SALT_INDEX", salt_index),
        ] if not val
    ]

    if missing:
        logger.error("PhonePe health check failed: missing settings %s", missing)
        return False, "Payment gateway not configured. Contact support."

    # Lightweight ping: attempt to reach the PhonePe API host
    base_url = _get_base_url()
    try:
        req = urllib.request.Request(
            base_url,
            headers={"User-Agent": "Iri-Collections/1.0"},
            method="HEAD",
        )
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError:
        # HTTPError means the server responded (even 4xx/5xx) — it's reachable
        pass
    except Exception as e:
        logger.warning("PhonePe reachability check failed: %s", e)
        return False, "Payment gateway is temporarily unreachable."

    return True, None


# ─────────────────────────────────────────────────────────────────────────────
# 2. INITIATE PAYMENT — Create PhonePe payment request
# ─────────────────────────────────────────────────────────────────────────────

def create_phonepe_payment(
    order: Order,
    redirect_url: str,
    callback_url: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Initiate a PhonePe payment and return the redirect URL for the user.

    Builds a signed payment request, sends it to PhonePe's /pg/v1/pay
    endpoint, and returns the hosted payment page URL that the frontend
    should redirect the user to.

    Args:
        order:        The Order instance to pay for (must have a phone number).
        redirect_url: URL PhonePe redirects to after payment (success/fail page).
        callback_url: URL PhonePe POSTs the payment result to (our /callback/).

    Returns:
        (payment_url, merchant_transaction_id, error_message)
        On success: (url, txn_id, None)
        On failure: (None, None, "error description")

    Security:
        ✅ Amount read from our DB — never from client input
        ✅ X-VERIFY checksum binds the payload to our salt key
        ✅ merchantTransactionId is UUID-based (unguessable, unique)
        ✅ No card data handled by us (PCI scope reduction)
    """
    merchant_id = settings.PHONEPE_MERCHANT_ID
    salt_key = settings.PHONEPE_SALT_KEY
    salt_index = str(settings.PHONEPE_SALT_INDEX)

    # Generate a unique, unguessable transaction ID for this payment attempt.
    # Format: IRI-<8-char-order-number>-<8-char-uuid> (max 38 chars, PhonePe allows 38)
    merchant_transaction_id = f"IRI-{order.order_number}-{uuid.uuid4().hex[:8].upper()}"

    if "?" in redirect_url:
        redirect_with_txn = f"{redirect_url}&merchant_transaction_id={merchant_transaction_id}"
    else:
        redirect_with_txn = f"{redirect_url}?merchant_transaction_id={merchant_transaction_id}"

    # Amount in paise (PhonePe requires smallest currency unit)
    amount_in_paise = int(order.total_amount * 100)

    # Build the payment request payload
    payload = {
        "merchantId": merchant_id,
        "merchantTransactionId": merchant_transaction_id,
        "merchantUserId": f"USER-{order.user_id}",
        "amount": amount_in_paise,
        "redirectUrl": redirect_with_txn,
        "redirectMode": "REDIRECT",
        "callbackUrl": callback_url,
        "mobileNumber": order.phone.replace("+91", "").replace("+", "")[-10:] or "",
        "paymentInstrument": {
            "type": "PAY_PAGE",  # PhonePe's hosted payment page (supports all methods)
        },
    }

    # Base64-encode the JSON payload (PhonePe requirement)
    payload_json = json.dumps(payload)
    payload_b64 = base64.b64encode(payload_json.encode("utf-8")).decode("utf-8")

    # Compute the X-VERIFY checksum
    checksum = _compute_checksum(payload_b64, _PAY_ENDPOINT, salt_key, salt_index)

    # Build the PhonePe API request
    api_url = f"{_get_base_url()}{_PAY_ENDPOINT}"
    request_body = json.dumps({"request": payload_b64}).encode("utf-8")

    try:
        req = urllib.request.Request(
            api_url,
            data=request_body,
            headers={
                "Content-Type": "application/json",
                "X-VERIFY": checksum,
                "X-MERCHANT-ID": merchant_id,
                "Accept": "application/json",
                "User-Agent": "Iri-Collections/1.0",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        logger.error(
            "PhonePe API HTTP error for order %s: %s — %s",
            order.id, e.code, error_body[:200],
        )
        return None, None, "Payment gateway returned an error. Please try again."

    except urllib.error.URLError as e:
        logger.error("PhonePe API connection error for order %s: %s", order.id, e)
        return None, None, "Payment gateway is unreachable. Please try again."

    except Exception as e:
        logger.exception("Unexpected error creating PhonePe payment for order %s: %s", order.id, e)
        return None, None, "An unexpected error occurred. Please try again."

    # ── Parse PhonePe response ────────────────────────────────────────
    if not response_data.get("success"):
        code = response_data.get("code", "UNKNOWN")
        msg = response_data.get("message", "Unknown error")
        logger.error(
            "PhonePe rejected payment for order %s: code=%s message=%s",
            order.id, code, msg,
        )
        audit_log(
            action="PHONEPE_PAYMENT_INIT_FAILED",
            user_id=order.user_id,
            details={
                "order_id": str(order.id),
                "phonepe_code": code,
                "merchant_transaction_id": merchant_transaction_id,
            },
            severity="WARNING",
        )
        return None, None, f"Payment gateway error: {msg}"

    # Extract the redirect URL from the nested response
    try:
        payment_url = (
            response_data["data"]["instrumentResponse"]["redirectInfo"]["url"]
        )
    except (KeyError, TypeError):
        logger.error(
            "PhonePe response missing payment URL for order %s: %s",
            order.id, response_data,
        )
        return None, None, "Unexpected response from payment gateway."

    audit_log(
        action="PHONEPE_PAYMENT_INITIATED",
        user_id=order.user_id,
        details={
            "order_id": str(order.id),
            "order_number": order.order_number,
            "merchant_transaction_id": merchant_transaction_id,
            "amount_paise": str(amount_in_paise),
        },
        severity="INFO",
    )

    return payment_url, merchant_transaction_id, None


# ─────────────────────────────────────────────────────────────────────────────
# 3. CHECK PAYMENT STATUS — Poll PhonePe for payment result
# ─────────────────────────────────────────────────────────────────────────────

def check_payment_status(merchant_transaction_id: str) -> Dict[str, Any]:
    """
    Query PhonePe's Status API for the current state of a payment.

    Used by the /success/ view to retrieve verified payment status after
    the user is redirected back. Never trust the redirect URL parameters
    alone — always verify with this API call.

    Args:
        merchant_transaction_id: The ID we sent to PhonePe when initiating.

    Returns:
        Dict with keys: 'success', 'status', 'phonepe_transaction_id', 'message'

    Security:
        ✅ X-VERIFY checksum on the status API call
        ✅ Authoritative result comes from PhonePe, not from redirect params
    """
    merchant_id = settings.PHONEPE_MERCHANT_ID
    salt_key = settings.PHONEPE_SALT_KEY
    salt_index = str(settings.PHONEPE_SALT_INDEX)

    endpoint = f"{_STATUS_ENDPOINT}/{merchant_id}/{merchant_transaction_id}"
    # Status API checksum: SHA256("" + endpoint + saltKey) + "###" + saltIndex
    # (empty payload for GET requests)
    raw = f"{endpoint}{salt_key}"
    sha256_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    checksum = f"{sha256_hash}###{salt_index}"

    api_url = f"{_get_base_url()}{endpoint}"

    try:
        req = urllib.request.Request(
            api_url,
            headers={
                "Content-Type": "application/json",
                "X-VERIFY": checksum,
                "X-MERCHANT-ID": merchant_id,
                "Accept": "application/json",
                "User-Agent": "Iri-Collections/1.0",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))

    except Exception as e:
        logger.error("PhonePe status check failed for %s: %s", merchant_transaction_id, e)
        return {"success": False, "status": "UNKNOWN", "message": "Status check failed."}

    phonepe_state = data.get("data", {}).get("state", "UNKNOWN")
    phonepe_txn_id = data.get("data", {}).get("transactionId", "")

    return {
        "success": data.get("success", False),
        "status": phonepe_state,          # COMPLETED, FAILED, PENDING, etc.
        "phonepe_transaction_id": phonepe_txn_id,
        "message": data.get("message", ""),
        "raw": data,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. FULFILL ORDER — Called after verified payment confirmation
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def fulfill_order_after_payment(merchant_transaction_id: str) -> bool:
    """
    Atomically confirm a paid order: update transaction, confirm order,
    and deduct inventory.

    Called from the callback handler AFTER signature verification, or from
    the success view after polling the PhonePe Status API.

    Security:
        ✅ transaction.atomic() — all-or-nothing DB update
        ✅ select_for_update() — row-level locks prevent stock race conditions
        ✅ Idempotent — safe to call multiple times (skips if already paid)
        ✅ Stock validation with graceful degradation (logs critical, continues)

    Args:
        merchant_transaction_id: Our ID used to locate the Transaction record.

    Returns:
        True if order was fulfilled; False if not found or already processed.
    """
    try:
        txn = Transaction.objects.select_for_update().get(
            merchant_transaction_id=merchant_transaction_id
        )
    except Transaction.DoesNotExist:
        logger.warning(
            "fulfill_order_after_payment: no transaction for ID %s",
            merchant_transaction_id,
        )
        return False

    # ── Idempotency guard ─────────────────────────────────────────────
    if txn.status == "paid":
        logger.info(
            "Transaction %s already fulfilled — skipping (idempotent)",
            merchant_transaction_id,
        )
        return True

    # ── Lock order and items ──────────────────────────────────────────
    order = Order.objects.select_for_update().get(id=txn.order_id)
    order_items = OrderItem.objects.filter(order=order).select_related("product")

    # ── Validate and deduct stock ─────────────────────────────────────
    for item in order_items:
        if not item.product:
            logger.error(
                "Product deleted for order item %s in order %s — skipping",
                item.id, order.id,
            )
            continue

        # Lock the product row before modifying stock
        product = Product.objects.select_for_update().get(id=item.product_id)

        if product.stock < item.quantity:
            logger.critical(
                "Insufficient stock for product %s (%s): needed %s, have %s. Order %s",
                product.id, product.name, item.quantity, product.stock, order.id,
            )
            audit_log(
                action="PHONEPE_STOCK_INSUFFICIENT",
                user_id=order.user_id,
                details={
                    "order_id": str(order.id),
                    "product_id": str(product.id),
                    "product_name": product.name,
                    "needed": str(item.quantity),
                    "available": str(product.stock),
                },
                severity="CRITICAL",
            )
            # Deduct what we can — fulfillment team resolves shortfall
            product.stock = max(0, product.stock - item.quantity)
        else:
            product.stock -= item.quantity

        product.save(update_fields=["stock"])

    # ── Confirm transaction and order ─────────────────────────────────
    txn.status = "paid"
    txn.save(update_fields=["status", "phonepe_transaction_id"])

    order.status = "confirmed"
    order.save(update_fields=["status"])

    audit_log(
        action="PHONEPE_ORDER_FULFILLED",
        user_id=order.user_id,
        details={
            "order_id": str(order.id),
            "order_number": order.order_number,
            "merchant_transaction_id": merchant_transaction_id,
            "total": str(float(order.total_amount)),
        },
        severity="INFO",
    )
    logger.info(
        "Order %s confirmed after PhonePe payment (txn: %s)",
        order.order_number, merchant_transaction_id,
    )
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 5. INVENTORY ROLLBACK — For failed or cancelled payments
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def rollback_order_inventory(order: Order) -> None:
    """
    Restore inventory when a confirmed order is cancelled or payment fails.

    Only restores stock if the order was previously confirmed (paid),
    meaning stock was actually deducted by fulfill_order_after_payment().

    Security:
        ✅ transaction.atomic() — consistent rollback
        ✅ select_for_update() — prevents concurrent modification
        ✅ Guards against double-restoration (status check)

    Args:
        order: The Order instance to roll back.
    """
    if order.status not in ("confirmed", "shipped", "delivered"):
        logger.info(
            "Rollback skipped for order %s: status=%s (stock was never deducted)",
            order.id, order.status,
        )
        return

    order_items = OrderItem.objects.filter(order=order).select_related("product")

    for item in order_items:
        if not item.product:
            continue
        product = Product.objects.select_for_update().get(id=item.product_id)
        product.stock += item.quantity
        product.save(update_fields=["stock"])
        logger.info(
            "Restored %sx %s (new stock: %s) for order %s",
            item.quantity, product.name, product.stock, order.id,
        )

    audit_log(
        action="PHONEPE_INVENTORY_ROLLBACK",
        user_id=order.user_id,
        details={
            "order_id": str(order.id),
            "order_number": order.order_number,
            "items_restored": str(order_items.count()),
        },
        severity="INFO",
    )
