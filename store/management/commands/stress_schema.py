import time
import random
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction, connection
from django.db.models import Sum, Count, F
from django.contrib.auth import get_user_model

from store.models import Category, Product, Order, OrderItem

User = get_user_model()


class Command(BaseCommand):
    help = "Stress test the database schema with bulk inserts and complex queries."

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.WARNING("Starting Schema Stress Test..."))

        try:
            with transaction.atomic():
                self.run_stress_test()
                # Rollback changes to keep the database clean
                self.stdout.write(
                    self.style.WARNING(
                        "Rolling back transaction to preserve DB state..."
                    )
                )
                transaction.set_rollback(True)
                self.stdout.write(
                    self.style.SUCCESS(
                        "Schema Stress Test complete. Database restored."
                    )
                )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error during stress test: {e}"))

    def run_stress_test(self):
        # 1. Bulk Inserts
        self.stdout.write("\n--- 1. Bulk Insert Performance ---")

        # Categories
        cat_start = time.time()
        categories = [
            Category(
                name=f"StressCat_{i}",
                slug=f"stress-cat-{i}",
                description="Stress test category",
            )
            for i in range(100)
        ]
        Category.objects.bulk_create(categories)
        cat_time = time.time() - cat_start
        self.stdout.write(f"Created 100 Categories in {cat_time:.4f}s")

        cats_in_db = list(Category.objects.filter(name__startswith="StressCat_"))

        # Products
        prod_start = time.time()
        products = []
        for i in range(10000):
            products.append(
                Product(
                    category=random.choice(cats_in_db),
                    name=f"Stress Product {i}",
                    slug=f"stress-product-{i}",
                    description="A product generated for stress testing the schema.",
                    price=Decimal(random.randint(100, 10000)),
                    stock=random.randint(0, 500),
                )
            )
        Product.objects.bulk_create(products, batch_size=1000)
        prod_time = time.time() - prod_start
        self.stdout.write(f"Created 10,000 Products in {prod_time:.4f}s")

        prods_in_db = list(Product.objects.filter(name__startswith="Stress Product"))

        # Users
        user_start = time.time()
        users = [
            User(
                username=f"stress_user_{i}",
                email=f"stress_user_{i}@test.com",
                full_name=f"Stress User {i}",
            )
            for i in range(1000)
        ]
        User.objects.bulk_create(users)
        user_time = time.time() - user_start
        self.stdout.write(f"Created 1000 Users in {user_time:.4f}s")

        users_in_db = list(User.objects.filter(email__startswith="stress_user_"))

        # Orders & OrderItems
        order_start = time.time()
        orders = []
        for i in range(10000):
            orders.append(
                Order(
                    user=random.choice(users_in_db),
                    order_number=f"STRESS-ORD-{i}",
                    total_amount=Decimal(random.randint(1000, 50000)),
                    status=random.choice(
                        ["pending", "confirmed", "shipped", "delivered"]
                    ),
                    shipping_address="123 Stress St, Test City, TS 12345",
                )
            )
        Order.objects.bulk_create(orders, batch_size=1000)
        order_time = time.time() - order_start
        self.stdout.write(f"Created 10,000 Orders in {order_time:.4f}s")

        orders_in_db = list(
            Order.objects.filter(order_number__startswith="STRESS-ORD-")
        )

        item_start = time.time()
        order_items = []
        for order in orders_in_db:
            # 1 to 3 items per order
            for _ in range(random.randint(1, 3)):
                p = random.choice(prods_in_db)
                order_items.append(
                    OrderItem(
                        order=order,
                        product=p,
                        product_name=p.name,
                        quantity=random.randint(1, 5),
                        price_at_purchase=p.price,
                    )
                )
        OrderItem.objects.bulk_create(order_items, batch_size=2000)
        item_time = time.time() - item_start
        self.stdout.write(f"Created {len(order_items)} OrderItems in {item_time:.4f}s")

        # 2. Complex Queries (Read Performance)
        self.stdout.write("\n--- 2. Complex Read Performance ---")

        # Query 1: Pagination & Filtering across ForeignKeys
        q1_start = time.time()
        list(
            Product.objects.filter(
                is_active=True, price__gte=5000, stock__lte=50
            ).select_related("category")[:50]
        )
        q1_time = time.time() - q1_start
        self.stdout.write(
            f"Filtered & Paginated Products (select_related) in {q1_time:.4f}s"
        )

        # Query 2: Aggregation (Admin Dashboard Analytics style)
        q2_start = time.time()
        revenue_metrics = Order.objects.filter(
            status__in=["confirmed", "shipped", "delivered"]
        ).aggregate(total_revenue=Sum("total_amount"), total_orders=Count("id"))
        q2_time = time.time() - q2_start
        self.stdout.write(
            f"Aggregated Revenue spanning 10k+ rows in {q2_time:.4f}s (Result: {revenue_metrics})"
        )

        # Query 3: Complex Group By & Joins (Top 10 Products)
        q3_start = time.time()
        top_products = list(
            OrderItem.objects.values("product_name")
            .annotate(
                total_sold=Sum("quantity"),
                total_revenue=Sum(F("price_at_purchase") * F("quantity")),
            )
            .order_by("-total_sold")[:10]
        )
        q3_time = time.time() - q3_start
        self.stdout.write(
            f"Calculated Top 10 Selling Products (Join + Group By) in {q3_time:.4f}s"
        )

        # Query 4: User Order History (prefetch_related)
        q4_start = time.time()
        random_user = random.choice(users_in_db)
        user_orders = list(
            Order.objects.filter(user=random_user).prefetch_related(
                "items", "items__product"
            )
        )
        q4_time = time.time() - q4_start
        self.stdout.write(
            f"Fetched comprehensive user order history (prefetch_related) in {q4_time:.4f}s"
        )
