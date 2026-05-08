"""Seed the database with sample jewelry products and categories."""

import random
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from store.models import Category, Product

User = get_user_model()

CATEGORIES = [
    {
        "name": "Necklaces",
        "description": "Elegant necklaces and pendants for every occasion",
    },
    {"name": "Earrings", "description": "Statement earrings from studs to chandeliers"},
    {"name": "Bracelets", "description": "Beautiful bracelets and bangles"},
    {
        "name": "Rings",
        "description": "Stunning rings for engagement, wedding, and fashion",
    },
    {"name": "Anklets", "description": "Delicate anklets for a graceful touch"},
]

PRODUCTS = [
    # Necklaces
    {
        "name": "Golden Empress Necklace",
        "description": "A breathtaking 22K gold necklace featuring intricate filigree work inspired by Mughal architecture. The centerpiece showcases a stunning kundan setting with emerald-cut stones, cascading into delicate gold chains. Perfect for bridal wear or grand celebrations.",
        "price": 45999.00,
        "compare_price": 52999.00,
        "stock": 8,
        "category": "Necklaces",
        "is_featured": True,
        "material": "22K Gold, Kundan",
        "weight": "28.5g",
        "image_url": "https://images.unsplash.com/photo-1599643478518-a784e5dc4c8f?w=600",
    },
    {
        "name": "Silver Moonlight Pendant",
        "description": "Minimalist sterling silver pendant with a luminous moonstone centerpiece. The pendant hangs from a fine 18-inch silver chain with a spring clasp. Ideal for daily wear or as an elegant gift.",
        "price": 3499.00,
        "compare_price": 4299.00,
        "stock": 25,
        "category": "Necklaces",
        "is_featured": True,
        "material": "925 Sterling Silver, Moonstone",
        "weight": "8.2g",
        "image_url": "https://images.unsplash.com/photo-1515562141589-67f0d879ef44?w=600",
    },
    {
        "name": "Pearl Cascade Choker",
        "description": "Multi-strand freshwater pearl choker with gold-filled accents. Features three graduated rows of perfectly matched pearls with a ornate clasp design. A timeless piece for sophisticated occasions.",
        "price": 12999.00,
        "stock": 12,
        "category": "Necklaces",
        "material": "Freshwater Pearls, Gold-filled",
        "weight": "45g",
        "image_url": "https://images.unsplash.com/photo-1611652022419-a9419f74343d?w=600",
    },
    # Earrings
    {
        "name": "Diamond Stardust Studs",
        "description": "Dazzling lab-grown diamond studs set in 18K white gold. Each earring features a 0.5 carat round brilliant diamond with VS1 clarity and F color. Comes with push-back closures for secure wear.",
        "price": 28999.00,
        "compare_price": 34999.00,
        "stock": 15,
        "category": "Earrings",
        "is_featured": True,
        "material": "18K White Gold, Lab Diamond",
        "weight": "3.8g",
        "image_url": "https://images.unsplash.com/photo-1535632066927-ab7c9ab60908?w=600",
    },
    {
        "name": "Ruby Jhumka Drop Earrings",
        "description": "Traditional Indian jhumka earrings crafted in gold-plated silver with genuine ruby accents. Features intricate meenakari work and small pearl danglers. A perfect blend of heritage and elegance.",
        "price": 7999.00,
        "stock": 20,
        "category": "Earrings",
        "material": "Gold-plated Silver, Ruby, Pearl",
        "weight": "12.5g",
        "image_url": "https://images.unsplash.com/photo-1630019852942-f89202989a59?w=600",
    },
    {
        "name": "Minimalist Gold Hoops",
        "description": "Sleek 14K gold hoop earrings with a polished finish. 25mm diameter, lightweight design for all-day comfort. Features a secure hinged closure. Perfect for stacking or wearing alone.",
        "price": 5499.00,
        "compare_price": 6999.00,
        "stock": 30,
        "category": "Earrings",
        "material": "14K Gold",
        "weight": "4.2g",
        "image_url": "https://images.unsplash.com/photo-1573408301185-9146fe634ad0?w=600",
    },
    # Bracelets
    {
        "name": "Sapphire Tennis Bracelet",
        "description": "Stunning tennis bracelet featuring alternating blue sapphires and diamonds set in 18K white gold. Total gem weight of 5.2 carats with a secure fold-over clasp. An investment piece for a lifetime.",
        "price": 67999.00,
        "compare_price": 79999.00,
        "stock": 5,
        "category": "Bracelets",
        "is_featured": True,
        "material": "18K White Gold, Sapphire, Diamond",
        "weight": "18.5g",
        "image_url": "https://images.unsplash.com/photo-1611591437281-460bfbe1220a?w=600",
    },
    {
        "name": "Rose Gold Chain Bracelet",
        "description": "Delicate rose gold-plated chain bracelet with a tiny heart charm. Adjustable length from 6.5 to 7.5 inches with an extender chain. Hypoallergenic and tarnish-resistant.",
        "price": 2499.00,
        "stock": 40,
        "category": "Bracelets",
        "material": "Rose Gold Plated Brass",
        "weight": "5.1g",
        "image_url": "https://images.unsplash.com/photo-1573408301185-9146fe634ad0?w=600",
    },
    # Rings
    {
        "name": "Solitaire Engagement Ring",
        "description": "Classic solitaire ring featuring a 1-carat lab-grown diamond in a six-prong platinum setting. The diamond exhibits exceptional brilliance with Hearts & Arrows cut. Comes with IGI certification.",
        "price": 89999.00,
        "compare_price": 99999.00,
        "stock": 7,
        "category": "Rings",
        "is_featured": True,
        "material": "Platinum, Lab Diamond 1ct",
        "weight": "6.8g",
        "image_url": "https://images.unsplash.com/photo-1605100804763-247f67b3557e?w=600",
    },
    {
        "name": "Emerald Art Deco Ring",
        "description": "Vintage-inspired art deco ring with a 0.8ct natural emerald surrounded by pavé-set diamonds. Crafted in 14K yellow gold with milgrain detailing. A statement piece that celebrates old-world charm.",
        "price": 35999.00,
        "stock": 10,
        "category": "Rings",
        "material": "14K Gold, Emerald, Diamond",
        "weight": "5.5g",
        "image_url": "https://images.unsplash.com/photo-1603561591411-07134e71a2a9?w=600",
    },
    {
        "name": "Silver Stackable Band Set",
        "description": "Set of 3 stackable rings in sterling silver with different textures: hammered, twisted wire, and smooth polished. Available in sizes 5-10. Mix, match, and stack for a personalized look.",
        "price": 1999.00,
        "compare_price": 2799.00,
        "stock": 50,
        "category": "Rings",
        "material": "925 Sterling Silver",
        "weight": "6.0g (set)",
        "image_url": "https://images.unsplash.com/photo-1611652022419-a9419f74343d?w=600",
    },
    # Anklets
    {
        "name": "Gold Charm Anklet",
        "description": "Dainty gold-filled anklet with tiny gem charms spaced along a fine cable chain. Adjustable from 9 to 11 inches. Water-resistant and perfect for beach or festive wear.",
        "price": 1799.00,
        "stock": 35,
        "category": "Anklets",
        "material": "Gold-filled, CZ",
        "weight": "3.2g",
        "image_url": "https://images.unsplash.com/photo-1535632066927-ab7c9ab60908?w=600",
    },
]

RANDOM_CATEGORY_PROFILES = {
    "Necklaces": {
        "names": ["Pendant", "Layered Chain", "Choker", "Statement Necklace", "Solitaire Strand"],
        "materials": ["Sterling Silver", "18K Gold Plating", "Rose Gold", "Pearl", "Kundan"],
        "themes": ["celestial", "heritage", "minimal", "luxury", "festival"],
    },
    "Earrings": {
        "names": ["Studs", "Hoops", "Drops", "Jhumkas", "Dangles"],
        "materials": ["Sterling Silver", "14K Gold", "Gold Plating", "Pearl", "CZ Stones"],
        "themes": ["everyday", "bridal", "party", "classic", "bold"],
    },
    "Bracelets": {
        "names": ["Chain Bracelet", "Cuff", "Bangle", "Tennis Bracelet", "Charm Bracelet"],
        "materials": ["Rose Gold", "Sterling Silver", "Gold Plating", "Sapphire", "Pearl"],
        "themes": ["refined", "stackable", "sleek", "festive", "giftable"],
    },
    "Rings": {
        "names": ["Ring", "Band", "Statement Ring", "Solitaire", "Stackable Ring"],
        "materials": ["Platinum", "Sterling Silver", "14K Gold", "Emerald", "Diamond"],
        "themes": ["romantic", "modern", "vintage", "bold", "luxury"],
    },
    "Anklets": {
        "names": ["Anklet", "Payal", "Chain Anklet", "Charm Anklet", "Festival Anklet"],
        "materials": ["Gold-filled", "Sterling Silver", "Pearl", "CZ Stones", "Rose Gold"],
        "themes": ["delicate", "beach", "festive", "heritage", "lightweight"],
    },
}

IMAGE_POOL = [
    "https://images.unsplash.com/photo-1599643478518-a784e5dc4c8f?w=600",
    "https://images.unsplash.com/photo-1515562141589-67f0d879ef44?w=600",
    "https://images.unsplash.com/photo-1535632066927-ab7c9ab60908?w=600",
    "https://images.unsplash.com/photo-1630019852942-f89202989a59?w=600",
    "https://images.unsplash.com/photo-1605100804763-247f67b3557e?w=600",
    "https://images.unsplash.com/photo-1603561591411-07134e71a2a9?w=600",
]


def _build_random_product(category_name: str, index: int, rng: random.Random) -> dict:
    profile = RANDOM_CATEGORY_PROFILES[category_name]
    core_name = rng.choice(profile["names"])
    material = rng.choice(profile["materials"])
    theme = rng.choice(profile["themes"])
    image_url = rng.choice(IMAGE_POOL)
    price = Decimal(rng.randint(1499, 59999)).quantize(Decimal("0.01"))
    compare_price = (price * Decimal(rng.uniform(1.10, 1.30))).quantize(Decimal("0.01"))

    return {
        "name": f"{theme.title()} {category_name[:-1]} {index}",
        "description": (
            f"A {theme} {core_name.lower()} crafted for the {category_name.lower()} collection. "
            f"Finished in {material} with a balanced silhouette for everyday wear and gifting."
        ),
        "price": price,
        "compare_price": compare_price,
        "stock": rng.randint(6, 40),
        "category": category_name,
        "is_featured": rng.choice([True, False, False]),
        "material": material,
        "weight": f"{rng.uniform(2.5, 28.5):.1f}g",
        "image_url": image_url,
    }


class Command(BaseCommand):
    help = "Seed the database with sample jewelry products"

    def add_arguments(self, parser):
        parser.add_argument(
            "--random-items-per-category",
            type=int,
            default=2,
            help="Number of generated products to create for each category.",
        )
        parser.add_argument(
            "--random-seed",
            type=int,
            default=20260509,
            help="Deterministic seed for random product generation.",
        )

    def handle(self, *args, **options):
        random_items_per_category = max(0, int(options["random_items_per_category"]))
        rng = random.Random(int(options["random_seed"]))

        # Create admin user
        if not User.objects.filter(email="admin@iri.com").exists():
            admin = User.objects.create_superuser(
                email="admin@iri.com",
                username="admin",
                password="Admin@123",
                full_name="Iri Admin",
                role="admin",
            )
            self.stdout.write(
                self.style.SUCCESS(f"Created admin: admin@iri.com / Admin@123")
            )

        # Create test customer
        if not User.objects.filter(email="customer@test.com").exists():
            User.objects.create_user(
                email="customer@test.com",
                username="customer",
                password="Customer@123",
                full_name="Test Customer",
                phone="+91 9876543210",
                role="customer",
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created customer: customer@test.com / Customer@123"
                )
            )

        # Create categories
        cat_map = {}
        for cat_data in CATEGORIES:
            cat, created = Category.objects.get_or_create(
                name=cat_data["name"], defaults={"description": cat_data["description"]}
            )
            cat_map[cat.name] = cat
            status = "Created" if created else "Exists"
            self.stdout.write(f'  {status}: Category "{cat.name}"')

        # Create products
        for prod_data in PRODUCTS:
            prod_defaults = prod_data.copy()
            cat_name = prod_defaults.pop("category")
            prod_defaults["category"] = cat_map[cat_name]
            _, created = Product.objects.get_or_create(
                name=prod_defaults["name"],
                defaults=prod_defaults,
            )
            status = "Created" if created else "Exists"
            self.stdout.write(f'  {status}: Product "{prod_defaults["name"]}"')

        if random_items_per_category:
            for category_name in sorted(cat_map):
                for index in range(1, random_items_per_category + 1):
                    product_data = _build_random_product(category_name, index, rng)
                    _, created = Product.objects.get_or_create(
                        name=product_data["name"],
                        defaults={**product_data, "category": cat_map[category_name]},
                    )
                    status = "Created" if created else "Exists"
                    self.stdout.write(
                        f'  {status}: Random Product "{product_data["name"]}" in {category_name}'
                    )

        self.stdout.write(self.style.SUCCESS("\n✅ Database seeded successfully!"))
