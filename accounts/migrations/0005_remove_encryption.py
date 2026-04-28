from django.db import migrations, models


class Migration(migrations.Migration):
    """Remove encryption from accounts PII fields — revert to plain text."""

    dependencies = [
        ("accounts", "0004_encrypt_pii_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="phone",
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name="user",
            name="full_name",
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name="address",
            name="street",
            field=models.TextField(),
        ),
        migrations.AlterField(
            model_name="address",
            name="pincode",
            field=models.TextField(),
        ),
        migrations.AlterField(
            model_name="address",
            name="phone",
            field=models.TextField(blank=True),
        ),
    ]
