# Static UPI QR Code Payment — Implementation Plan

## Concept
Replace the PhonePe payment gateway with a **static UPI QR code** hardcoded into the website. Customers scan, pay, and upload a screenshot. Admin reviews and approves.

**Security benefit**: Even if the site is hacked, the QR code is a static image — attackers can't redirect payments to a different UPI ID.

## Changes Required

### 1. Database Migration
- **Remove** from `Transaction`: `merchant_transaction_id`, `phonepe_transaction_id`
- **Add** to `Transaction`: `payment_screenshot` (ImageField) — customer uploads proof
- **Add** to `Transaction`: `upi_reference_id` (CharField) — customer enters UTR/UPI ref
- **Update** `Transaction.STATUS_CHOICES`: `created → pending_verification → paid → failed → rejected`

### 2. Backend — New Payment Flow
- **New endpoint**: `POST /api/payments/upload-proof/` — Customer uploads screenshot + UTR for a pending order
- **Modified endpoint**: `PATCH /api/store/admin/orders/<pk>/status/` — Remove the "must have paid transaction" guard for `pending → confirmed` (admin manually confirms after verifying screenshot)
- **New endpoint**: `POST /api/payments/approve/<pk>/` — Admin approves payment (creates transaction as "paid", deducts stock)
- **New endpoint**: `POST /api/payments/reject/<pk>/` — Admin rejects payment (marks as "rejected")

### 3. Frontend — Checkout Template
- **Step 3**: Instead of PhonePe redirect, show the static UPI QR code + UPI ID
- Add file upload input for payment screenshot
- Add text input for UTR/UPI reference number
- Submit button to upload proof

### 4. Frontend — Admin Dashboard
- Show payment screenshots on order cards
- Add "Approve Payment" / "Reject Payment" buttons
- Show UTR reference for verification

### 5. Cleanup
- Remove `payments/services.py` (PhonePe service layer)
- Simplify `payments/views.py` (remove PhonePe views)
- Remove PhonePe env vars from `.env` and `settings.py`
- Update `payments/urls.py`

## File-by-file Changes

| File | Action |
|------|--------|
| `store/models.py` | Update `Transaction` model |
| `store/migrations/0008_*.py` | New migration |
| `payments/views.py` | Rewrite: upload-proof, approve, reject |
| `payments/services.py` | Delete or gut entirely |
| `payments/urls.py` | Update URL patterns |
| `store/views.py` | Update `AdminOrderStatusView` to remove paid-txn guard |
| `templates/checkout.html` | Replace payment step with QR + upload |
| `templates/admin_dashboard.html` | Add screenshot review + approve/reject |
| `ecommerce/settings.py` | Remove PhonePe settings |
| `.env` | Remove PhonePe env vars |
