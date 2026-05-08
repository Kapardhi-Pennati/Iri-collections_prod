from django.db import migrations


class Migration(migrations.Migration):
    """Compatibility migration to preserve the historical migration chain."""

    dependencies = [
        ("store", "0011_checkout_reference_tracking_indexes"),
    ]

    operations = []