from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify
from store.models import Product
import csv
import re


def _normalize_seed(value: str, max_len: int = 8) -> str:
    if not value:
        return ''
    s = slugify(value)  # produces ascii-safe slug with hyphens
    s = re.sub(r'[^A-Za-z0-9]', '', s).upper()
    return s[:max_len]


class Command(BaseCommand):
    help = 'Populate SKUs for existing products. Use --csv to import mappings or generate deterministic SKUs.'

    def add_arguments(self, parser):
        parser.add_argument('--csv', type=str, help='CSV file path. Columns: product_id or slug,sku')
        parser.add_argument('--force', action='store_true', help='Overwrite existing SKUs')
        parser.add_argument('--dry-run', action='store_true', help="Don't persist changes; show planned updates")
        parser.add_argument('--pattern', type=str, default='IRI-{id}-{seed}', help='SKU pattern. Available tokens: {id}, {seed}')

    def handle(self, *args, **options):
        csv_path = options.get('csv')
        force = options.get('force')
        dry_run = options.get('dry_run')
        pattern = options.get('pattern') or 'IRI-{id}-{seed}'

        updates = []

        if csv_path:
            self.stdout.write(f'Importing SKUs from CSV: {csv_path}')
            try:
                with open(csv_path, newline='', encoding='utf-8') as fh:
                    reader = csv.DictReader(fh)
                    for row in reader:
                        sku = (row.get('sku') or row.get('SKU') or '').strip()
                        pid = row.get('product_id') or row.get('id') or row.get('pk')
                        slug = row.get('slug')
                        if not sku:
                            self.stdout.write(self.style.WARNING(f'Skipping row without SKU: {row}'))
                            continue
                        prod = None
                        if pid:
                            try:
                                prod = Product.objects.get(pk=int(pid))
                            except Exception:
                                prod = None
                        if not prod and slug:
                            prod = Product.objects.filter(slug=slug).first()
                        if not prod:
                            self.stdout.write(self.style.WARNING(f'Product not found for row: {row}'))
                            continue

                        if prod.sku and not force:
                            self.stdout.write(self.style.NOTICE(f'Skipping existing SKU for product {prod.pk}'))
                            continue

                        final_sku = sku.upper()
                        # ensure uniqueness
                        counter = 1
                        base = final_sku
                        while Product.objects.filter(sku=final_sku).exclude(pk=prod.pk).exists():
                            counter += 1
                            final_sku = f"{base}-{counter}"

                        updates.append((prod, final_sku))
            except FileNotFoundError:
                self.stderr.write(self.style.ERROR('CSV file not found'))
                return
        else:
            self.stdout.write('Generating SKUs for products...')
            qs = Product.objects.all().order_by('id')
            for prod in qs:
                if prod.sku and not force:
                    continue
                seed = prod.slug or prod.name
                seed_normalized = _normalize_seed(seed)
                candidate = pattern.format(id=prod.id, seed=seed_normalized)
                candidate = re.sub(r'[^A-Za-z0-9\-]', '', candidate).upper()

                # ensure uniqueness
                final_sku = candidate
                counter = 1
                while Product.objects.filter(sku=final_sku).exclude(pk=prod.pk).exists():
                    counter += 1
                    final_sku = f"{candidate}-{counter}"

                updates.append((prod, final_sku))

        if not updates:
            self.stdout.write(self.style.WARNING('No SKUs to update.'))
            return

        self.stdout.write(f'Planned updates: {len(updates)}')
        for prod, sku in updates:
            self.stdout.write(f'Product {prod.pk} -> {sku}')

        if dry_run:
            self.stdout.write(self.style.SUCCESS('Dry run complete. No changes saved.'))
            return

        # Persist updates in a transaction
        with transaction.atomic():
            for prod, sku in updates:
                prod.sku = sku
                prod.save(update_fields=['sku'])

        self.stdout.write(self.style.SUCCESS(f'Updated {len(updates)} products with SKUs.'))
