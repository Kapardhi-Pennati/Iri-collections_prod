# Iri Collections - E-Commerce Platform

A full-featured jewelry e-commerce platform built with Django, Django REST Framework, and a custom vanilla JavaScript frontend.

## Security Highlights

- Secure authentication with Argon2 password hashing, OTP flows, and JWT sessions.
- Brute-force protection with account lockout and API throttling.
- Static UPI QR checkout with manual payment verification.
- Strong production headers/cookies (HSTS, CSP-compatible setup, CSRF/CORS controls).
- Audit logging for sensitive actions.

## Functional Highlights

- Product catalog, cart, wishlist, and order lifecycle.
- Static UPI payment flow:
  - Customer uploads proof: `POST /api/payments/upload-proof/`
  - Admin approves payment: `POST /api/payments/approve/<order_id>/`
  - Admin rejects payment: `POST /api/payments/reject/<order_id>/`
- Checkout UX:
  - Live shipping estimate appears in Step 1 after pincode/address updates.
  - Shipping state is carried into order creation for consistent fee calculation.
- Printable invoice page for completed/placed orders.

## Local Development Setup

1. Install dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

2. Configure environment variables:

   - Copy `.env.example` to `.env`.
   - For local development, set:

   ```env
   DEBUG=True
   SECRET_KEY=your-local-secret-key
   ALLOWED_HOSTS=localhost,127.0.0.1
   ```

   - If you want SQLite locally, ensure `DATABASE_URL` is unset or set to `sqlite:///db.sqlite3`.
  - Add UPI checkout values:

   ```env
   UPI_ID=your-upi-id@bank
   UPI_DISPLAY_NAME=Iri Collections
   ```

3. Optional infrastructure:

   - Redis is recommended for Celery and cache in realistic local testing.

4. Run migrations and seed data:

   ```bash
   python manage.py migrate
   python manage.py seed_data
   ```

   The seed command now generates curated products plus random starter items for every category. Use `--random-items-per-category` to change the amount.

5. Run server:

   ```bash
   python manage.py runserver
   ```

## Production Deployment (Web Hoster)

This repository is configured for standard Python web hosting (Passenger/cPanel, VPS, or dedicated Linux hosting).

### Passenger/cPanel Steps

1. Upload project files.
2. Create/activate virtual environment and install requirements.
3. Configure `.env` with production values (`DEBUG=False`, secure `SECRET_KEY`, DB, Redis, SMTP, UPI settings).
4. Run migrations and collect static files:

   ```bash
   python manage.py migrate
   python manage.py collectstatic --noinput
   ```

5. Use `passenger_wsgi.py` as the app entrypoint and restart the app from hosting panel.

## Deployment Readiness Checklist

Run before each production release:

1. Security/system checks:

   ```bash
   python manage.py check --deploy
   ```

2. Migration drift:

   ```bash
   python manage.py makemigrations --check --dry-run
   ```

3. Static and DB:

   ```bash
   python manage.py migrate
   python manage.py collectstatic --noinput
   ```

4. Payment sanity:

   - Verify `UPI_ID` and `UPI_DISPLAY_NAME`.
   - Confirm checkout payment step shows the correct UPI details.
   - Submit payment proof and verify admin can approve/reject it.

## Notes

- With `DEBUG=False`, the app enforces HTTPS-related security behavior.
- Current repository has minimal automated tests; add app-level tests for order and payment flows for stronger release confidence.
