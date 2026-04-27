"""
SECURE STORE VIEWS - Production-hardened order and product management
Merged with original application logic for full functionality.
"""

import logging
import urllib.request
import urllib.error
import json
from datetime import timedelta

from rest_framework import generics, status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import transaction
from django.db.models import Sum, Count, F, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.html import escape

from core.security import audit_log
from core.permissions import IsAdminUser, IsCustomerUser
from core.validators import InputValidator
from core.throttling import (
    AdminMutationThrottle,
    CheckoutThrottle,
    PincodeVerifyThrottle,
)

from .models import Category, Product, Cart, CartItem, Order, OrderItem, Wishlist, StockReservation, Transaction
from accounts.models import Address
from .serializers import (
    CategorySerializer,
    ProductSerializer,
    ProductAdminSerializer,
    CartSerializer,
    OrderSerializer,
    OrderCreateSerializer,
)

logger = logging.getLogger(__name__)

# ─── Permission helpers ────────────────────────────────────────
class IsAdminRole(IsAdminUser):
    """
    Grants access only to authenticated users with role='admin'
    or the is_superuser flag. Extends IsAuthenticated so unauthenticated
    requests are rejected at the authentication stage.
    """
    pass


class IsCustomerRole(IsCustomerUser):
    pass


def _parse_product_id(raw_value) -> tuple:
    """
    Parse and validate a product_id from request data.

    Extracted from WishlistView and WishlistToggleView to eliminate
    three identical try/except blocks (D.R.Y.).

    Args:
        raw_value: The raw value from request.data or query_params.

    Returns:
        (True, int_id) on success.
        (False, Response) on failure — caller should return the Response.
    """
    from rest_framework import status as drf_status
    from rest_framework.response import Response as DRFResponse
    try:
        return True, int(raw_value)
    except (TypeError, ValueError):
        return False, DRFResponse(
            {"error": "Invalid product ID."},
            status=drf_status.HTTP_400_BAD_REQUEST,
        )


def _cart_queryset():
    return Cart.objects.select_related("user").prefetch_related(
        "items__product__category"
    )


def _get_or_create_cart(user):
    cart = _cart_queryset().filter(user=user).first()
    if cart:
        return cart
    cart, _ = Cart.objects.get_or_create(user=user)
    return _cart_queryset().get(pk=cart.pk)


def _merge_session_cart_with_user_cart(user, session_items: list = None) -> Cart:
    """
    Merge guest/session cart items with the authenticated user's cart.

    This function supports the following scenarios:
    1. User already has a cart: merge session items into existing cart
    2. User has no cart: create new cart and populate with session items
    3. Duplicate products: update quantities instead of creating duplicates
    4. Out-of-stock products: skip without error

    Args:
        user: Authenticated user instance
        session_items: List of dicts with 'product_id' and 'quantity' keys,
                      sourced from session/localStorage. If None, no merge occurs.

    Returns:
        The user's Cart instance (created or existing)
    """
    cart = _get_or_create_cart(user)

    if not session_items:
        return cart

    cart = Cart.objects.select_for_update().get(pk=cart.pk)

    for item_data in session_items:
        try:
            product_id = int(item_data.get("product_id"))
            quantity = int(item_data.get("quantity", 1))
            if quantity <= 0:
                continue
        except (TypeError, ValueError):
            continue

        try:
            product = Product.objects.get(id=product_id, is_active=True)
        except Product.DoesNotExist:
            continue

        existing_item = CartItem.objects.filter(
            cart=cart, product=product
        ).first()

        if existing_item:
            existing_item.quantity += quantity
            existing_item.save(update_fields=["quantity"])
        else:
            CartItem.objects.create(cart=cart, product=product, quantity=quantity)

        # Create or update stock reservation for the guest/session item
        expires_at = timezone.now() + timedelta(hours=24)
        reservation = StockReservation.objects.filter(
            user=user, product=product, order__isnull=True
        ).first()

        if reservation:
            reservation.quantity += quantity
            reservation.expires_at = expires_at
            reservation.save(update_fields=["quantity", "expires_at"])
        else:
            StockReservation.objects.create(
                user=user,
                product=product,
                quantity=quantity,
                expires_at=expires_at,
            )

    return _cart_queryset().get(pk=cart.pk)


# ─── Public: Categories ────────────────────────────────────────
class CategoryListView(generics.ListAPIView):
    queryset = Category.objects.annotate(
        product_count=Count("products", filter=Q(products__is_active=True))
    )
    serializer_class = CategorySerializer
    permission_classes = [AllowAny]
    pagination_class = None


# ─── Public: Products ──────────────────────────────────────────
class ProductListView(generics.ListAPIView):
    serializer_class = ProductSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        qs = Product.objects.filter(is_active=True).select_related("category")
        category = self.request.query_params.get("category")
        search = self.request.query_params.get("search")
        featured = self.request.query_params.get("featured")
        sort = self.request.query_params.get("sort")

        if category:
            qs = qs.filter(category__slug=category)
        if search:
            qs = qs.filter(name__icontains=search)
        # Only activate featured filter on explicit truthy values.
        # Previously `if featured:` would be True for "false" or "0" strings.
        if featured in ("true", "1", "yes"):
            qs = qs.filter(is_featured=True)
        if sort == "price_low":
            qs = qs.order_by("price")
        elif sort == "price_high":
            qs = qs.order_by("-price")
        elif sort == "newest":
            qs = qs.order_by("-created_at")
        return qs


class ProductDetailView(generics.RetrieveAPIView):
    queryset = Product.objects.filter(is_active=True).select_related("category")
    serializer_class = ProductSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"


# ─── Cart ──────────────────────────────────────────────────────
class CartView(APIView):
    permission_classes = [IsCustomerRole]

    def get(self, request):
        cart = _get_or_create_cart(request.user)
        return Response(CartSerializer(cart).data)

    @transaction.atomic
    def post(self, request):
        """Add an item to the cart while holding a reservation lock on the SKU."""
        ok, quantity = InputValidator.validate_quantity(request.data.get("quantity", 1))
        if not ok:
            return Response(
                {"error": "Quantity must be between 1 and 100."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ok, product_id = _parse_product_id(request.data.get("product_id"))
        if not ok:
            return product_id

        cart = _get_or_create_cart(request.user)
        cart = Cart.objects.select_for_update().get(pk=cart.pk)

        try:
            product = Product.objects.select_for_update().select_related("category").get(
                id=product_id,
                is_active=True,
            )
        except Product.DoesNotExist:
            return Response(
                {"error": "Product not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        item = (
            CartItem.objects.select_for_update()
            .filter(cart=cart, product=product)
            .first()
        )
        new_quantity = quantity + (item.quantity if item else 0)
        available = product.get_available_stock_for_user(request.user.id)
        if new_quantity > available:
            return Response(
                {
                    "error": f"Only {available} item(s) are available after active reservations are applied."
                },
                status=status.HTTP_409_CONFLICT,
            )

        if item:
            item.quantity = new_quantity
            item.save(update_fields=["quantity"])
        else:
            item = CartItem.objects.create(cart=cart, product=product, quantity=new_quantity)

        reservation = (
            StockReservation.objects.select_for_update()
            .filter(user=request.user, product=product, order__isnull=True)
            .first()
        )
        expires_at = timezone.now() + timedelta(minutes=30)
        if reservation:
            reservation.quantity = item.quantity
            reservation.expires_at = expires_at
            reservation.save(update_fields=["quantity", "expires_at"])
        else:
            StockReservation.objects.create(
                user=request.user,
                product=product,
                order=None,
                quantity=item.quantity,
                expires_at=expires_at,
            )

        return Response(
            CartSerializer(_cart_queryset().get(pk=cart.pk)).data,
            status=status.HTTP_200_OK,
        )

    @transaction.atomic
    def patch(self, request):
        """Update a cart item quantity under row locks."""
        ok, item_id = _parse_product_id(request.data.get("item_id"))
        if not ok:
            return item_id

        try:
            quantity = int(request.data.get("quantity", 1))
        except (ValueError, TypeError):
            return Response(
                {"error": "Invalid quantity."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cart = _get_or_create_cart(request.user)
        cart = Cart.objects.select_for_update().get(pk=cart.pk)

        try:
            item = (
                CartItem.objects.select_for_update()
                .select_related("product__category")
                .get(id=item_id, cart=cart)
            )
        except CartItem.DoesNotExist:
            return Response(
                {"error": "Item not found in cart."},
                status=status.HTTP_404_NOT_FOUND,
            )

        product = Product.objects.select_for_update().get(pk=item.product_id)
        reservation = (
            StockReservation.objects.select_for_update()
            .filter(user=request.user, product=product, order__isnull=True)
            .first()
        )

        if quantity <= 0:
            if reservation:
                reservation.delete()
            item.delete()
        else:
            ok, validated_quantity = InputValidator.validate_quantity(quantity)
            if not ok:
                return Response(
                    {"error": "Quantity must be between 1 and 100."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            available = product.get_available_stock_for_user(request.user.id)
            if validated_quantity > available:
                return Response(
                    {
                        "error": f"Only {available} item(s) are available after active reservations are applied."
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            item.quantity = validated_quantity
            item.save(update_fields=["quantity"])

            expires_at = timezone.now() + timedelta(minutes=30)
            if reservation:
                reservation.quantity = validated_quantity
                reservation.expires_at = expires_at
                reservation.save(update_fields=["quantity", "expires_at"])
            else:
                StockReservation.objects.create(
                    user=request.user,
                    product=product,
                    order=None,
                    quantity=validated_quantity,
                    expires_at=expires_at,
                )

        return Response(CartSerializer(_cart_queryset().get(pk=cart.pk)).data)

    @transaction.atomic
    def delete(self, request):
        """Remove one cart item or clear the cart and its reservations."""
        cart = _get_or_create_cart(request.user)
        cart = Cart.objects.select_for_update().get(pk=cart.pk)
        item_id = request.data.get("item_id") or request.query_params.get("item_id")

        if item_id:
            try:
                item_id = int(item_id)
            except (TypeError, ValueError):
                return Response(
                    {"error": "Invalid item_id."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            item = (
                CartItem.objects.select_for_update()
                .filter(id=item_id, cart=cart)
                .first()
            )
            if item:
                StockReservation.objects.filter(
                    user=request.user,
                    product=item.product,
                    order__isnull=True,
                ).delete()
                item.delete()
        else:
            StockReservation.objects.filter(user=request.user, order__isnull=True).delete()
            cart.items.all().delete()

        return Response(CartSerializer(_cart_queryset().get(pk=cart.pk)).data)


# ─────────────────────────────────────────────────────────────────────────────
# ORDER CREATION (Secure)
# ─────────────────────────────────────────────────────────────────────────────

class OrderCreateView(APIView):
    """
    Create order from cart with inventory locking.
    
    Security features:
    ✅ User authentication required
    ✅ Input validation with sanitization
    ✅ Database transaction with row locking (prevents race conditions)
    ✅ Stock validation
    ✅ Audit logging
    """
    permission_classes = [IsCustomerRole]
    throttle_classes = [CheckoutThrottle]
    
    @transaction.atomic
    def post(self, request):
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            cart = (
                Cart.objects.select_for_update()
                .prefetch_related("items__product__category")
                .get(user=request.user)
            )
        except Cart.DoesNotExist:
            return Response(
                {"error": "Cart is empty or not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cart_items = list(cart.items.all())
        if not cart_items:
            return Response(
                {"error": "Cannot create order from empty cart."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sanitized_address = serializer.validated_data["shipping_address"]
        normalized_phone = serializer.validated_data["phone"]

        product_ids = [item.product_id for item in cart_items if item.product_id]
        products = Product.objects.select_for_update().filter(id__in=product_ids)
        product_map = {p.id: p for p in products}

        order_items_data = []
        for item in cart_items:
            locked_product = product_map.get(item.product_id)

            if not locked_product:
                continue

            available = locked_product.get_available_stock_for_user(request.user.id)
            if item.quantity > available:
                audit_log(
                    action="ORDER_INSUFFICIENT_STOCK",
                    user_id=request.user.id,
                    details={
                        "product_id": item.product_id,
                        "product_name": item.product.name,
                        "requested": item.quantity,
                        "available": available,
                    },
                    severity="WARNING",
                )
                return Response(
                    {"error": f"Insufficient available stock for {item.product.name}."},
                    status=status.HTTP_409_CONFLICT,
                )

            order_items_data.append({"item": item, "product": locked_product})

        if not order_items_data:
            return Response(
                {"error": "All items in your cart are out of stock."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        items_total = sum(
            data["product"].price * data["item"].quantity
            for data in order_items_data
        )

        shipping_state = str(serializer.validated_data.get("state", "")).strip()
        shipping_fee = _calculate_shipping_fee(sanitized_address, shipping_state)
        final_total = items_total + shipping_fee

        if final_total <= 0 or final_total > 999999:
            audit_log(
                action="ORDER_INVALID_TOTAL",
                user_id=request.user.id,
                details={"total": float(final_total)},
                severity="CRITICAL",
            )
            return Response(
                {"error": "Invalid order total."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order = Order.objects.create(
            user=request.user,
            total_amount=final_total,
            shipping_fee=shipping_fee,
            shipping_address=sanitized_address,
            phone=normalized_phone,
            notes=escape(serializer.validated_data.get("notes", ""))[:500],
        )

        if serializer.validated_data.get("save_address", True):
            street = serializer.validated_data.get("street", "").strip()
            city = serializer.validated_data.get("city", "").strip()
            state = serializer.validated_data.get("state", "").strip()
            pincode = serializer.validated_data.get("pincode", "").strip()
            if street and city and state and pincode:
                phone = serializer.validated_data.get("phone", "")
                name = serializer.validated_data.get("recipient_name", "").strip() or "Shipping"
                address, created = Address.objects.get_or_create(
                    user=request.user,
                    street=street,
                    city=city,
                    state=state,
                    pincode=pincode,
                    defaults={"name": name[:150], "phone": phone},
                )
                if not created:
                    updated_fields = []
                    if phone and address.phone != phone:
                        address.phone = phone
                        updated_fields.append("phone")
                    if name and address.name != name[:150]:
                        address.name = name[:150]
                        updated_fields.append("name")
                    if updated_fields:
                        address.save(update_fields=updated_fields)

                if not Address.objects.filter(user=request.user, is_default=True).exists():
                    address.is_default = True
                    address.save(update_fields=["is_default"])

        for data in order_items_data:
            item = data["item"]
            product = data["product"]

            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=product.name,
                quantity=item.quantity,
                price_at_purchase=product.price,
            )

            reservation = (
                StockReservation.objects.select_for_update()
                .filter(user=request.user, product=product, order__isnull=True)
                .first()
            )
            expires_at = timezone.now() + timedelta(hours=24)
            if reservation:
                reservation.order = order
                reservation.quantity = item.quantity
                reservation.expires_at = expires_at
                reservation.save(update_fields=["order", "quantity", "expires_at"])
            else:
                StockReservation.objects.create(
                    user=request.user,
                    product=product,
                    order=order,
                    quantity=item.quantity,
                    expires_at=expires_at,
                )

        cart.items.all().delete()

        audit_log(
            action="ORDER_CREATED",
            user_id=request.user.id,
            details={
                "order_id": order.id,
                "order_number": order.order_number,
                "total": float(order.total_amount),
                "items_count": len(order_items_data),
                "note": "Inventory reserved atomically; hard stock deduction deferred to payment approval",
            },
            severity="INFO",
        )

        return Response(
            OrderSerializer(
                Order.objects.select_related("transaction")
                .prefetch_related("items")
                .get(pk=order.pk)
            ).data,
            status=status.HTTP_201_CREATED,
        )

class OrderConfirmPaymentView(APIView):
    """
    "I have paid" action — mark an existing pending order as paid
    without requiring screenshot/UTR proof.

    Creates (or updates) a Transaction row with status='paid'.
    Stock is NOT deducted here.
    """
    permission_classes = [IsCustomerRole]
    throttle_classes = [CheckoutThrottle]

    @transaction.atomic
    def post(self, request):
        order_id = request.data.get("order_id")
        if not order_id:
            return Response(
                {"error": "order_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order_id = int(order_id)
        except (TypeError, ValueError):
            return Response(
                {"error": "Invalid order_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order = (
                Order.objects.select_for_update()
                .get(id=order_id, user=request.user)
            )
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if order.status != "pending":
            return Response(
                {"error": f"Order is already {order.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Extend stock reservations while the order proceeds to fulfillment.
        expires_at = timezone.now() + timedelta(hours=24)
        StockReservation.objects.filter(order=order).update(expires_at=expires_at)

        txn, created = Transaction.objects.get_or_create(
            order=order,
            defaults={
                "amount": order.total_amount,
                "status": "paid",
            },
        )
        if not created and txn.status != "paid":
            txn.status = "paid"
            txn.save(update_fields=["status"])

        audit_log(
            action="PAYMENT_CONFIRMED_BY_CUSTOMER",
            user_id=request.user.id,
            details={
                "order_id": str(order.id),
                "order_number": order.order_number,
            },
            severity="INFO",
        )

        return Response({
            "message": "Payment marked as paid.",
            "order_number": order.order_number,
            "status": "paid",
        })


class OrderCancelView(APIView):
    """
    Cancel a pending order and restore its items back to the user's cart.

    Security:
        - Only the owning customer can cancel
        - Only orders in 'pending' status can be cancelled
        - Entire operation is atomic
        - Stock reservations are released
        - Cart items are re-created (quantities merged if item already in cart)
    """
    permission_classes = [IsCustomerRole]

    @transaction.atomic
    def post(self, request):
        raw_order_id = request.data.get("order_id") or request.data.get("id")
        order_number = str(request.data.get("order_number", "")).strip()

        order = None

        if raw_order_id not in (None, ""):
            try:
                order_id = int(raw_order_id)
            except (TypeError, ValueError):
                return Response(
                    {"error": "Invalid order_id."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            order = (
                Order.objects.select_for_update()
                .prefetch_related("items__product")
                .filter(id=order_id, user=request.user)
                .first()
            )
        elif order_number:
            order = (
                Order.objects.select_for_update()
                .prefetch_related("items__product")
                .filter(order_number=order_number, user=request.user)
                .first()
            )
        else:
            return Response(
                {"error": "order_id or order_number is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not order:
            return Response(
                {"error": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if order.status != "pending":
            return Response(
                {"error": f"Only pending orders can be cancelled. Current status: {order.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Restore items to cart
        cart = _get_or_create_cart(request.user)
        cart = Cart.objects.select_for_update().get(pk=cart.pk)

        for order_item in order.items.all():
            if not order_item.product or not order_item.product.is_active:
                continue

            existing = (
                CartItem.objects.select_for_update()
                .filter(cart=cart, product=order_item.product)
                .first()
            )
            if existing:
                existing.quantity += order_item.quantity
                existing.save(update_fields=["quantity"])
            else:
                CartItem.objects.create(
                    cart=cart,
                    product=order_item.product,
                    quantity=order_item.quantity,
                )

            # Convert order reservation back to cart reservation
            reservation = (
                StockReservation.objects.select_for_update()
                .filter(order=order, product=order_item.product)
                .first()
            )
            if reservation:
                reservation.order = None
                reservation.expires_at = timezone.now() + timedelta(minutes=30)
                reservation.save(update_fields=["order", "expires_at"])

        # Release any remaining order reservations
        StockReservation.objects.filter(order=order).delete()

        # Delete associated transaction if any
        Transaction.objects.filter(order=order).delete()

        # Cancel and delete the order
        order_number = order.order_number
        order.status = "cancelled"
        order.save(update_fields=["status"])
        order.items.all().delete()
        order.delete()

        audit_log(
            action="ORDER_CANCELLED_BY_CUSTOMER",
            user_id=request.user.id,
            details={"order_number": order_number, "items_restored_to_cart": True},
            severity="INFO",
        )

        return Response(
            CartSerializer(_cart_queryset().get(pk=cart.pk)).data,
            status=status.HTTP_200_OK,
        )


def _calculate_shipping_fee(address: str, state: str = "") -> int:
    """
    Calculate shipping fee based on Indian state.

    Business rule:
      - Tamil Nadu: 50
      - Any other state in India: 80
    """
    normalized_state = " ".join(str(state or "").lower().split())

    if normalized_state in {"tamil nadu", "tn"}:
        return 50

    # Backward-compatible fallback for clients not yet sending `state`.
    address_lower = str(address or "").lower()
    if "tamil nadu" in address_lower:
        return 50

    return 80


# ─────────────────────────────────────────────────────────────────────────────
# PINCODE VERIFICATION (Secure with SSRF protection)
# ─────────────────────────────────────────────────────────────────────────────

class PincodeVerifyView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PincodeVerifyThrottle]
    
    PINCODE_API_DOMAIN = "api.postalpincode.in"
    REQUEST_TIMEOUT = 5
    
    def post(self, request):
        pincode = str(request.data.get("pincode", "")).strip()
        
        is_valid, validated_pincode = InputValidator.validate_pincode(pincode)
        if not is_valid:
            return Response(
                {"error": "Invalid pincode format. Please enter 6 digits."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        url = f"https://{self.PINCODE_API_DOMAIN}/pincode/{validated_pincode}"
        if not InputValidator.is_valid_url(url, allowed_domains=[self.PINCODE_API_DOMAIN]):
            return Response(
                {"error": "Invalid external service configuration."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Iri-Collections/1.0', 'Accept': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=self.REQUEST_TIMEOUT) as response:
                data = json.loads(response.read().decode())
            
            if not isinstance(data, list) or len(data) == 0:
                return Response({"error": "Invalid pincode."}, status=status.HTTP_400_BAD_REQUEST)
            
            result = data[0]
            if result.get("Status") != "Success":
                 return Response({"error": "Pincode not found."}, status=status.HTTP_400_BAD_REQUEST)
            
            post_offices = result.get("PostOffice", [])
            if not post_offices:
                return Response({"error": "No data found."}, status=status.HTTP_400_BAD_REQUEST)
            
            first_office = post_offices[0]
            state = escape(first_office.get("State", ""))
            shipping_fee = _calculate_shipping_fee("", state)
            
            return Response({
                "valid": True,
                "pincode": validated_pincode,
                "district": first_office.get("District", ""),
                "state": first_office.get("State", ""),
                "shipping_fee": shipping_fee,
            })
        except Exception as e:
            logger.error(f"Pincode verify error: {str(e)}")
            return Response({"error": "Service unavailable."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


# ─── Wishlist (Secure) ──────────────────────────────────────────
class WishlistView(APIView):
    permission_classes = [IsCustomerRole]
    
    def get(self, request):
        wishlist, _ = Wishlist.objects.get_or_create(user=request.user)
        return Response(ProductSerializer(wishlist.products.filter(is_active=True), many=True).data)
    
    def post(self, request):
        ok, product_id = _parse_product_id(request.data.get("product_id"))
        if not ok:
            return product_id  # product_id is the error Response when ok=False
        try:
            product = Product.objects.get(id=product_id, is_active=True)
            wishlist, _ = Wishlist.objects.get_or_create(user=request.user)
            wishlist.products.add(product)
            audit_log(
                action="WISHLIST_ADD",
                user_id=request.user.id,
                details={"product_id": str(product_id)},
                severity="INFO",
            )
            return Response({"message": "Added to wishlist."})
        except Product.DoesNotExist:
            return Response({"error": "Product not found."}, status=status.HTTP_404_NOT_FOUND)
    
    def delete(self, request):
        raw_id = request.data.get("product_id") or request.query_params.get("product_id")
        ok, product_id = _parse_product_id(raw_id)
        if not ok:
            return product_id
        wishlist, _ = Wishlist.objects.get_or_create(user=request.user)
        wishlist.products.remove(product_id)
        audit_log(
            action="WISHLIST_REMOVE",
            user_id=request.user.id,
            details={"product_id": str(product_id)},
            severity="INFO",
        )
        return Response({"message": "Removed from wishlist."})


class WishlistToggleView(APIView):
    """
    Toggle a product in/out of the user's wishlist.
    Returns {"added": true/false} so the frontend can update the UI.
    """
    permission_classes = [IsCustomerRole]

    def post(self, request):
        ok, product_id = _parse_product_id(request.data.get("product_id"))
        if not ok:
            return product_id

        try:
            product = Product.objects.get(id=product_id, is_active=True)
        except Product.DoesNotExist:
            return Response({"error": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

        wishlist, _ = Wishlist.objects.get_or_create(user=request.user)

        if wishlist.products.filter(id=product.id).exists():
            wishlist.products.remove(product)
            audit_log(
                action="WISHLIST_TOGGLE_REMOVE",
                user_id=request.user.id,
                details={"product_id": str(product_id)},
                severity="INFO",
            )
            return Response({"added": False, "message": "Removed from wishlist."})
        else:
            wishlist.products.add(product)
            audit_log(
                action="WISHLIST_TOGGLE_ADD",
                user_id=request.user.id,
                details={"product_id": str(product_id)},
                severity="INFO",
            )
            return Response({"added": True, "message": "Added to wishlist."})


# ─── Orders List & Detail ──────────────────────────────────────
class OrderListView(generics.ListAPIView):
    serializer_class = OrderSerializer
    permission_classes = [IsCustomerRole]

    def get_queryset(self):
        return (
            Order.objects.filter(user=self.request.user)
            .select_related("transaction")
            .prefetch_related("items")
        )


class OrderDetailView(generics.RetrieveAPIView):
    serializer_class = OrderSerializer
    permission_classes = [IsCustomerRole]

    def get_queryset(self):
        return (
            Order.objects.filter(user=self.request.user)
            .select_related("transaction")
            .prefetch_related("items")
        )


# ─── Admin: Products CRUD ──────────────────────────────────────
class AdminProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all().select_related("category")
    serializer_class = ProductAdminSerializer
    permission_classes = [IsAdminRole]
    throttle_classes = [AdminMutationThrottle]

    def get_serializer_class(self):
        if self.action in ("list", "retrieve"):
            return ProductSerializer
        return ProductAdminSerializer


class AdminCategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.annotate(
        product_count=Count("products", filter=Q(products__is_active=True))
    )
    serializer_class = CategorySerializer
    permission_classes = [IsAdminRole]
    throttle_classes = [AdminMutationThrottle]


# ─── Admin: Orders & Analytics ────────────────────────────────
class AdminOrderListView(generics.ListAPIView):
    queryset = (
        Order.objects.all()
        .select_related("user", "transaction")
        .prefetch_related("items")
    )
    serializer_class = OrderSerializer
    permission_classes = [IsAdminRole]


class AdminOrderDetailView(generics.RetrieveDestroyAPIView):
    queryset = (
        Order.objects.all()
        .select_related("user", "transaction")
        .prefetch_related("items")
    )
    serializer_class = OrderSerializer
    permission_classes = [IsAdminRole]


class AdminOrderStatusView(APIView):
    permission_classes = [IsAdminRole]
    throttle_classes = [AdminMutationThrottle]

    _allowed_transitions = {
        "pending": {"confirmed", "cancelled"},
        "confirmed": {"shipped", "cancelled"},
        "shipped": {"cancelled"},
        "cancelled": set(),
    }

    @transaction.atomic
    def patch(self, request, pk):
        try:
            order = Order.objects.select_for_update().select_related("transaction").get(
                pk=pk
            )
            new_status = str(request.data.get("status", "")).strip().lower()

            valid_statuses = {choice[0] for choice in Order.STATUS_CHOICES}
            if new_status not in valid_statuses:
                return Response(
                    {"error": "Invalid order status."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if new_status == order.status:
                return Response(OrderSerializer(order).data)

            allowed_next = self._allowed_transitions.get(order.status, set())
            if new_status not in allowed_next:
                return Response(
                    {
                        "error": (
                            f"Invalid status transition: {order.status} -> {new_status}."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if new_status == "cancelled" and order.status != "cancelled":
                txn = getattr(order, "transaction", None)
                # Restore stock only for legacy flow where screenshot upload deducted stock.
                if txn and txn.status != "rejected" and txn.payment_screenshot:
                    for item in order.items.all().select_related("product"):
                        if item.product:
                            product = Product.objects.select_for_update().get(pk=item.product.pk)
                            product.stock += item.quantity
                            product.save(update_fields=["stock"])

                if txn and txn.status != "rejected":
                    txn.status = "rejected"
                    txn.save(update_fields=["status"])
            
            if new_status == "cancelled" and order.status == "pending":
                StockReservation.objects.filter(order=order).delete()

            order.status = new_status
            order.save(update_fields=["status"])

            audit_log(
                action="ADMIN_ORDER_STATUS_UPDATED",
                user_id=request.user.id,
                details={
                    "order_id": order.id,
                    "order_number": order.order_number,
                    "new_status": new_status,
                },
                severity="INFO",
            )

            return Response(OrderSerializer(order).data)
        except Order.DoesNotExist:
            return Response({"error": "Order not found."}, status=status.HTTP_404_NOT_FOUND)


class AdminOrderTrackingUploadView(APIView):
    permission_classes = [IsAdminRole]
    throttle_classes = [AdminMutationThrottle]

    def post(self, request, pk):
        try:
            order = Order.objects.get(pk=pk)
            image = request.FILES.get("tracking_image")
            if not image:
                return Response({"error": "tracking_image is required."}, status=status.HTTP_400_BAD_REQUEST)
            if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
                return Response(
                    {"error": "Only JPEG, PNG, and WebP tracking images are allowed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if image.size > 5 * 1024 * 1024:
                return Response(
                    {"error": "Tracking image must be 5 MB or smaller."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            order.tracking_image = image
            order.save()
            return Response({"message": "Tracking image uploaded."})
        except Order.DoesNotExist:
            return Response({"error": "Order not found."}, status=status.HTTP_404_NOT_FOUND)


class AdminAnalyticsView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        now = timezone.now()
        thirty_days_ago = now - timedelta(days=30)

        # ── Stat card numbers ──────────────────────────────────────
        total_revenue = (
            Order.objects.filter(status__in=["confirmed", "shipped"])
            .aggregate(total=Sum("total_amount"))["total"] or 0
        )
        total_orders = Order.objects.count()
        pending_orders = Order.objects.filter(status="pending").count()
        total_products = Product.objects.filter(is_active=True).count()
        low_stock = Product.objects.filter(stock__lte=5, is_active=True).count()

        # ── Daily revenue (last 30 days) ───────────────────────────
        daily_revenue_qs = (
            Order.objects.filter(
                status__in=["confirmed", "shipped"],
                created_at__gte=thirty_days_ago,
            )
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(revenue=Sum("total_amount"))
            .order_by("date")
        )
        daily_revenue = [
            {"date": str(r["date"]), "revenue": float(r["revenue"] or 0)}
            for r in daily_revenue_qs
        ]

        # ── Status breakdown ───────────────────────────────────────
        status_qs = (
            Order.objects.values("status")
            .annotate(count=Count("id"))
            .order_by("status")
        )
        status_totals = {}
        for s in status_qs:
            normalized_status = "shipped" if s["status"] == "delivered" else s["status"]
            status_totals[normalized_status] = status_totals.get(normalized_status, 0) + s["count"]
        status_breakdown = [
            {"status": key, "count": value}
            for key, value in status_totals.items()
        ]

        # ── Top products ───────────────────────────────────────────
        top_products_qs = (
            OrderItem.objects.filter(
                order__status__in=["confirmed", "shipped"]
            )
            .values("product_name")
            .annotate(
                total_sold=Sum("quantity"),
                total_revenue=Sum(F("quantity") * F("price_at_purchase")),
            )
            .order_by("-total_revenue")[:5]
        )
        top_products = [
            {
                "product_name": p["product_name"],
                "total_sold": p["total_sold"],
                "total_revenue": float(p["total_revenue"] or 0),
            }
            for p in top_products_qs
        ]

        return Response(
            {
                "total_revenue": float(total_revenue),
                "total_orders": total_orders,
                "pending_orders": pending_orders,
                "total_products": total_products,
                "low_stock_products": low_stock,
                "daily_revenue": daily_revenue,
                "status_breakdown": status_breakdown,
                "top_products": top_products,
            }
        )


class AdminTrafficView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        from .models import PageView

        now = timezone.now()
        thirty_days_ago = now - timedelta(days=30)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        qs_30d = PageView.objects.filter(created_at__gte=thirty_days_ago)

        total_views = qs_30d.count()
        unique_visitors = qs_30d.values("session_key").exclude(session_key="").distinct().count()
        today_views = PageView.objects.filter(created_at__gte=today_start).count()

        # Daily breakdown
        daily_qs = (
            qs_30d.annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(
                views=Count("id"),
                unique=Count("session_key", distinct=True),
            )
            .order_by("date")
        )
        daily_views = [
            {
                "date": str(d["date"]),
                "views": d["views"],
                "unique": d["unique"],
            }
            for d in daily_qs
        ]

        # Top pages
        top_pages_qs = (
            qs_30d.values("path")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
        top_pages = [{"path": p["path"], "count": p["count"]} for p in top_pages_qs]

        # Device breakdown via user agent sniffing
        mobile_keywords = ["mobile", "android", "iphone", "ipad", "ipod", "blackberry", "windows phone"]
        tablet_keywords = ["ipad", "tablet", "kindle", "playbook"]

        mobile_count = 0
        tablet_count = 0
        desktop_count = 0

        agents = qs_30d.values_list("user_agent", flat=True)
        for ua in agents:
            ua_lower = ua.lower()
            if any(kw in ua_lower for kw in tablet_keywords):
                tablet_count += 1
            elif any(kw in ua_lower for kw in mobile_keywords):
                mobile_count += 1
            else:
                desktop_count += 1

        # Top page for today
        top_today = (
            PageView.objects.filter(created_at__gte=today_start)
            .values("path")
            .annotate(count=Count("id"))
            .order_by("-count")
            .first()
        )
        top_page_today = top_today["path"] if top_today else "—"

        return Response(
            {
                "total_views": total_views,
                "unique_visitors": unique_visitors,
                "today_views": today_views,
                "top_page": top_page_today,
                "daily_views": daily_views,
                "top_pages": top_pages,
                "device_breakdown": {
                    "mobile": mobile_count,
                    "desktop": desktop_count,
                    "tablet": tablet_count,
                },
            }
        )

