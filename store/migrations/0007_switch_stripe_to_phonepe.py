"""
Migration: Switch payment gateway from Stripe to PhonePe.

Replaces:
  - stripe_checkout_session_id  →  merchant_transaction_id
  - stripe_payment_intent_id    →  phonepe_transaction_id

Also adds 'pending' as a new Transaction status choice to reflect
PhonePe's PAYMENT_PENDING state.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("store", "0006_pageview"),
    ]

    operations = [
        # Remove Stripe-specific fields
        migrations.RemoveField(
            model_name="transaction",
            name="stripe_checkout_session_id",
        ),
        migrations.RemoveField(
            model_name="transaction",
            name="stripe_payment_intent_id",
        ),

        # Add PhonePe fields
        migrations.AddField(
            model_name="transaction",
            name="merchant_transaction_id",
            field=models.CharField(
                max_length=255,
                blank=True,
                db_index=True,
                default="",
                help_text="Our unique ID sent to PhonePe (merchantTransactionId)",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="transaction",
            name="phonepe_transaction_id",
            field=models.CharField(
                max_length=255,
                blank=True,
                default="",
                help_text="PhonePe's own transaction reference ID",
            ),
            preserve_default=False,
        ),

        # Update status choices to include 'pending'
        migrations.AlterField(
            model_name="transaction",
            name="status",
            field=models.CharField(
                choices=[
                    ("created", "Created"),
                    ("paid", "Paid"),
                    ("failed", "Failed"),
                    ("pending", "Pending"),
                ],
                db_index=True,
                default="created",
                max_length=10,
            ),
        ),
    ]
