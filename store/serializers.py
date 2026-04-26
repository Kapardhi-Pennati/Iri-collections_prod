from django.conf import settings
from rest_framework import serializers
from .models import Category, Product, Cart, CartItem, Order, OrderItem, Transaction
from core.validators import InputValidator


class CategorySerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        fields = ("id", "name", "slug", "description", "image", "product_count")

class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    display_image = serializers.ReadOnlyField()
    in_stock = serializers.ReadOnlyField()

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "description",
            "price",
            "compare_price",
            "stock",
            "category",
            "category_name",
            "image",
            "image_url",
            "display_image",
            "is_active",
            "is_featured",
            "material",
            "weight",
            "in_stock",
            "created_at",
        )


class ProductAdminSerializer(serializers.ModelSerializer):
    """Writable serializer for admin product management."""

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "description",
            "price",
            "compare_price",
            "stock",
            "category",
            "image",
            "image_url",
            "is_active",
            "is_featured",
            "material",
            "weight",
        )
        read_only_fields = ("slug",)

    def validate(self, attrs):
        price = attrs.get("price", getattr(self.instance, "price", None))
        compare_price = attrs.get(
            "compare_price",
            getattr(self.instance, "compare_price", None),
        )
        stock = attrs.get("stock", getattr(self.instance, "stock", 0))

        if price is not None and price < 0:
            raise serializers.ValidationError({"price": "Price must be non-negative."})
        if compare_price is not None and compare_price < 0:
            raise serializers.ValidationError(
                {"compare_price": "Compare price must be non-negative."}
            )
        if compare_price is not None and price is not None and compare_price < price:
            raise serializers.ValidationError(
                {"compare_price": "Compare price cannot be lower than price."}
            )
        if stock is not None and stock < 0:
            raise serializers.ValidationError({"stock": "Stock must be non-negative."})
        return attrs


class CartItemSerializer(serializers.ModelSerializer):
    product_detail = ProductSerializer(source="product", read_only=True)
    subtotal = serializers.ReadOnlyField()
    is_out_of_stock = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ("id", "product", "product_detail", "quantity", "subtotal", "is_out_of_stock")

    def get_is_out_of_stock(self, obj):
        return obj.product.stock <= 0


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    total = serializers.ReadOnlyField()
    item_count = serializers.ReadOnlyField()

    class Meta:
        model = Cart
        fields = ("id", "items", "total", "item_count")


class OrderItemSerializer(serializers.ModelSerializer):
    subtotal = serializers.ReadOnlyField()

    class Meta:
        model = OrderItem
        fields = (
            "id",
            "product",
            "product_name",
            "quantity",
            "price_at_purchase",
            "subtotal",
        )


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = (
            "id",
            "payment_screenshot",
            "upi_reference_id",
            "status",
            "amount",
            "admin_notes",
            "created_at",
        )


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    transaction = TransactionSerializer(read_only=True)
    upi_url = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            "id",
            "order_number",
            "total_amount",
            "shipping_fee",
            "status",
            "shipping_address",
            "phone",
            "notes",
            "tracking_image",
            "items",
            "transaction",
            "upi_url",
            "created_at",
            "updated_at",
        )

    def get_upi_url(self, obj):
        """Generates a standard UPI payment URI."""
        upi_id = getattr(settings, "UPI_ID", "your-upi-id@paytm")
        name = getattr(settings, "UPI_DISPLAY_NAME", "Iri Collections")
        # Format: upi://pay?pa=VPA&pn=NAME&am=AMOUNT&cu=INR&tn=NOTES
        return f"upi://pay?pa={upi_id}&pn={name}&am={obj.total_amount}&cu=INR&tn=Order-{obj.order_number}"


class OrderCreateSerializer(serializers.Serializer):
    shipping_address = serializers.CharField(max_length=500, trim_whitespace=True)
    state = serializers.CharField(max_length=100, required=False, allow_blank=True, trim_whitespace=True)
    phone = serializers.CharField(max_length=20, trim_whitespace=True)
    recipient_name = serializers.CharField(max_length=150, required=False, allow_blank=True, trim_whitespace=True)
    street = serializers.CharField(max_length=500, required=False, allow_blank=True, trim_whitespace=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True, trim_whitespace=True)
    pincode = serializers.CharField(max_length=10, required=False, allow_blank=True, trim_whitespace=True)
    save_address = serializers.BooleanField(required=False, default=True)
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True, trim_whitespace=True)

    def validate_shipping_address(self, value: str) -> str:
        is_valid, sanitized = InputValidator.validate_address(value)
        if not is_valid:
            raise serializers.ValidationError("Enter a valid shipping address.")
        return sanitized

    def validate_phone(self, value: str) -> str:
        is_valid, normalized = InputValidator.validate_phone(value)
        if not is_valid:
            raise serializers.ValidationError("Enter a valid phone number.")
        return normalized

    def validate_state(self, value: str) -> str:
        return value.strip()[:100]

    def validate_city(self, value: str) -> str:
        return value.strip()[:100]

    def validate_pincode(self, value: str) -> str:
        if not value:
            return value
        is_valid, normalized = InputValidator.validate_pincode(value)
        if not is_valid:
            raise serializers.ValidationError("Enter a valid pincode.")
        return normalized
