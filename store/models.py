import uuid
from django.db import models
from django.conf import settings
from django.utils.text import slugify
from django.utils import timezone
from core.encryption import EncryptedCharField, EncryptedTextField


class StockReservationQuerySet(models.QuerySet):
    def active(self):
        return self.filter(expires_at__gt=timezone.now()).filter(
            models.Q(order__isnull=True) | models.Q(order__status="pending")
        )


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="categories/", blank=True, null=True)

    class Meta:
        db_table = "categories"
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    compare_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    stock = models.PositiveIntegerField(default=0)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products"
    )
    image = models.ImageField(upload_to="products/", blank=True, null=True)
    image_url = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    is_featured = models.BooleanField(default=False, db_index=True)
    material = models.CharField(max_length=100, blank=True)
    weight = models.CharField(max_length=50, blank=True, help_text="e.g. 5.2g")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "products"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(price__gte=0), name="product_price_positive"
            ),
            models.CheckConstraint(
                check=models.Q(stock__gte=0), name="product_stock_positive"
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            slug = base_slug
            counter = 1
            while Product.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @property
    def in_stock(self):
        return self.stock > 0

    @property
    def display_image(self):
        if self.image:
            return self.image.url
        return self.image_url or ""

    def get_available_stock(self):
        """Return stock minus active cart/order reservations."""
        reserved = (
            StockReservation.objects.active()
            .filter(product=self)
            .aggregate(total=models.Sum("quantity"))["total"]
            or 0
        )
        return max(0, self.stock - reserved)

    def get_available_stock_for_user(self, user_id):
        """
        Return available stock while ignoring the caller's own cart reservation.

        This lets customers edit their existing hold without self-blocking while
        still accounting for every other active reservation.
        """
        reserved = (
            StockReservation.objects.active()
            .filter(product=self)
            .exclude(user_id=user_id, order__isnull=True)
            .aggregate(total=models.Sum("quantity"))["total"]
            or 0
        )
        return max(0, self.stock - reserved)


class Cart(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "carts"

    def __str__(self):
        return f"Cart of {self.user.email}"

    @property
    def total(self):
        return sum(item.subtotal for item in self.items.all())

    @property
    def item_count(self) -> int:
        """
        Return the total number of in-stock items in the cart.

        Refactored from a Python loop (N+1 queries — one per item) to a
        single ORM aggregate query. Using filter(product__stock__gt=0)
        pushes the stock check into SQL, not Python.
        """
        from django.db.models import Sum
        result = (
            self.items.filter(product__stock__gt=0)
            .aggregate(total=Sum("quantity"))["total"]
        )
        return result or 0


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "cart_items"
        unique_together = ("cart", "product")
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=0), name="cartitem_quantity_positive"
            )
        ]

    def __str__(self):
        return f"{self.quantity}x {self.product.name}"

    @property
    def subtotal(self):
        if self.product.stock <= 0:
            return 0
        return self.product.price * self.quantity


class Order(models.Model):
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("shipped", "Shipped"),
        ("cancelled", "Cancelled"),
    )
    order_number = models.CharField(
        max_length=20,
        unique=True,
        editable=False,
        null=True,
        blank=True,
    )
    checkout_reference = models.CharField(
        max_length=36,
        unique=True,
        editable=False,
        db_index=True,
        default=uuid.uuid4,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders"
    )
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    shipping_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default="pending", db_index=True
    )
    shipping_address = EncryptedTextField()
    phone = EncryptedCharField(blank=True)
    notes = models.TextField(blank=True)
    tracking_image = models.ImageField(upload_to="tracking/", blank=True, null=True)
    tracking_id = models.CharField(max_length=120, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(total_amount__gte=0), name="order_total_positive"
            )
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_status = self.status

    def save(self, *args, **kwargs):
        if self.status in {"confirmed", "shipped"} and not self.order_number:
            self.order_number = f"IRI-{uuid.uuid4().hex[:8].upper()}"

        super().save(*args, **kwargs)

        # ✅ Email notifications are now handled asynchronously via:
        #    store/signals.py → post_save signal → Celery task
        #    (see core/tasks.py → task_send_order_status_email)
        #
        # The signal reads _original_status to detect changes and
        # dispatches the task with .delay() so SMTP never blocks the request.

    def __str__(self):
        return self.order_number or f"PENDING-{str(self.checkout_reference)[:8].upper()}"

    def finalize_order_number(self) -> str:
        if not self.order_number:
            self.order_number = f"IRI-{uuid.uuid4().hex[:8].upper()}"
            self.save(update_fields=["order_number"])
        return self.order_number


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    product_name = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField()
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table = "order_items"
        indexes = [
            models.Index(fields=["product", "order"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=0), name="orderitem_quantity_positive"
            ),
            models.CheckConstraint(
                check=models.Q(price_at_purchase__gte=0),
                name="orderitem_price_positive",
            ),
        ]

    def __str__(self):
        return f"{self.quantity}x {self.product_name}"

    @property
    def subtotal(self):
        return self.price_at_purchase * self.quantity


class Transaction(models.Model):
    """Records a payment transaction against an order via manual UPI QR verification."""
    STATUS_CHOICES = (
        ("pending_verification", "Pending Verification"),
        ("paid", "Paid"),
        ("rejected", "Rejected"),
    )
    order = models.OneToOneField(
        Order, on_delete=models.CASCADE, related_name="transaction"
    )
    # Customer uploads a screenshot of their UPI payment
    payment_screenshot = models.ImageField(
        upload_to="payment_proofs/", blank=True, null=True,
        help_text="Screenshot of UPI payment uploaded by customer",
    )
    # Customer enters the UPI transaction reference / UTR number
    upi_reference_id = models.CharField(
        max_length=100, blank=True,
        help_text="UPI transaction reference (UTR) entered by customer",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=25, choices=STATUS_CHOICES, default="pending_verification", db_index=True
    )
    admin_notes = models.TextField(
        blank=True,
        help_text="Admin notes when approving/rejecting payment",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Txn for Order {self.order.order_number} - {self.status}"


class Wishlist(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wishlist"
    )
    products = models.ManyToManyField(Product, related_name="wishlisted_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "wishlists"

    def __str__(self):
        return f"Wishlist of {self.user.email}"


class PageView(models.Model):
    """Tracks page visits for website traffic analytics."""
    path = models.CharField(max_length=500)
    session_key = models.CharField(max_length=100, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="page_views",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "page_views"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.path} @ {self.created_at:%Y-%m-%d %H:%M}"


class StockReservation(models.Model):
    """
    Temporary reservation of stock when an item is in a user's cart or a pending order.
    Reservations expire if not converted to an order or paid within a window.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reservations")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reservations")
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="reservations", null=True, blank=True)
    quantity = models.PositiveIntegerField()
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = StockReservationQuerySet.as_manager()

    class Meta:
        db_table = "stock_reservations"
        indexes = [
            models.Index(fields=["product", "expires_at"]),
            models.Index(fields=["user", "order"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=0),
                name="stockreservation_quantity_positive",
            )
        ]

    def __str__(self):
        if self.order_id:
            return f"Reserve {self.quantity}x {self.product.name} for Order {self.order.order_number}"
        return f"Reserve {self.quantity}x {self.product.name} in cart for {self.user.email}"
