from rest_framework import serializers
from .models import Category, Product, Cart, CartItem, Order, OrderItem, Transaction


class CategorySerializer(serializers.ModelSerializer):
    product_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ("id", "name", "slug", "description", "image", "product_count")

    def get_product_count(self, obj):
        return obj.products.filter(is_active=True).count()


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
            "merchant_transaction_id",
            "phonepe_transaction_id",
            "status",
            "amount",
            "created_at",
        )


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    transaction = TransactionSerializer(read_only=True)

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
            "created_at",
            "updated_at",
        )


class OrderCreateSerializer(serializers.Serializer):
    shipping_address = serializers.CharField()
    state = serializers.CharField(max_length=100, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=15)
    notes = serializers.CharField(required=False, allow_blank=True)
