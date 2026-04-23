# Generated migration: Switch from PhonePe to static UPI QR code payment

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("store", "0007_switch_stripe_to_phonepe"),
    ]

    operations = [
        # Remove PhonePe-specific fields
        migrations.RemoveField(
            model_name="transaction",
            name="merchant_transaction_id",
        ),
        migrations.RemoveField(
            model_name="transaction",
            name="phonepe_transaction_id",
        ),
        # Add UPI payment proof fields
        migrations.AddField(
            model_name="transaction",
            name="payment_screenshot",
            field=models.ImageField(
                blank=True,
                help_text="Screenshot of UPI payment uploaded by customer",
                null=True,
                upload_to="payment_proofs/",
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="upi_reference_id",
            field=models.CharField(
                blank=True,
                help_text="UPI transaction reference (UTR) entered by customer",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="admin_notes",
            field=models.TextField(
                blank=True,
                help_text="Admin notes when approving/rejecting payment",
            ),
        ),
        # Update status field choices and default
        migrations.AlterField(
            model_name="transaction",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending_verification", "Pending Verification"),
                    ("paid", "Paid"),
                    ("rejected", "Rejected"),
                ],
                db_index=True,
                default="pending_verification",
                max_length=25,
            ),
        ),
    ]
