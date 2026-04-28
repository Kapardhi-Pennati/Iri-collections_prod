"""
One-time script to decrypt any existing encrypted data in the database.

Run on VPS after deploying the encryption removal:
    cd /var/www/iricollections
    ./venv/bin/python manage.py shell < scripts/decrypt_existing_data.py
"""

from core.encryption import decrypt_text

ENC_PREFIX = "encv1:"


def decrypt_field(value):
    """Decrypt a value if it has the encryption prefix, otherwise return as-is."""
    if value and isinstance(value, str) and value.startswith(ENC_PREFIX):
        try:
            return decrypt_text(value)
        except Exception as e:
            print(f"  ⚠ Failed to decrypt value: {e}")
            return value
    return value


def process_users():
    from accounts.models import User

    users = User.objects.all()
    count = 0
    for user in users:
        changed = False
        for field in ("phone", "full_name"):
            old_val = getattr(user, field, "")
            new_val = decrypt_field(old_val)
            if new_val != old_val:
                setattr(user, field, new_val)
                changed = True

        if changed:
            user.save(update_fields=["phone", "full_name"])
            count += 1

    print(f"✅ Users decrypted: {count}/{users.count()}")


def process_addresses():
    from accounts.models import Address

    addresses = Address.objects.all()
    count = 0
    for addr in addresses:
        changed = False
        for field in ("street", "pincode", "phone"):
            old_val = getattr(addr, field, "")
            new_val = decrypt_field(old_val)
            if new_val != old_val:
                setattr(addr, field, new_val)
                changed = True

        if changed:
            # Skip the custom save() to avoid re-hashing issues during migration
            Address.objects.filter(pk=addr.pk).update(
                street=addr.street,
                pincode=addr.pincode,
                phone=addr.phone,
            )
            count += 1

    print(f"✅ Addresses decrypted: {count}/{addresses.count()}")


def process_orders():
    from store.models import Order

    orders = Order.objects.all()
    count = 0
    for order in orders:
        changed = False
        for field in ("shipping_address", "phone"):
            old_val = getattr(order, field, "")
            new_val = decrypt_field(old_val)
            if new_val != old_val:
                setattr(order, field, new_val)
                changed = True

        if changed:
            Order.objects.filter(pk=order.pk).update(
                shipping_address=order.shipping_address,
                phone=order.phone,
            )
            count += 1

    print(f"✅ Orders decrypted: {count}/{orders.count()}")


print("=" * 50)
print("DECRYPTING EXISTING DATA")
print("=" * 50)

process_users()
process_addresses()
process_orders()

print("=" * 50)
print("DONE — All encrypted data has been converted to plaintext.")
print("=" * 50)
