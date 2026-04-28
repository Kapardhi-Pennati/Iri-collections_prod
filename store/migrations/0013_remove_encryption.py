from django.db import migrations, models


class Migration(migrations.Migration):
    """Remove encryption from order PII fields — revert to plain text."""

    dependencies = [
        ("store", "0012_encrypt_order_pii"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="shipping_address",
            field=models.TextField(),
        ),
        migrations.AlterField(
            model_name="order",
            name="phone",
            field=models.CharField(max_length=20, blank=True),
        ),
    ]
