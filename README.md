# Iri Collections - E-Commerce Platform

A full-featured jewelry e-commerce platform built with Django, Django REST Framework, and a custom vanilla JavaScript frontend.

## Security Highlights

- Secure authentication with Argon2 password hashing, OTP flows, and JWT sessions.
- Brute-force protection with account lockout and API throttling.
- Signed payment callback verification (HMAC-SHA256).
- Strong production headers/cookies (HSTS, CSP-compatible setup, CSRF/CORS controls).
- Audit logging for sensitive actions.

## Functional Highlights

- Product catalog, cart, wishlist, and order lifecycle.
- PhonePe payment flow:
  - `POST /api/payments/initiate/`
  - `POST /api/payments/callback/`
  - `GET /api/payments/status/<merchant_transaction_id>/`
  - `GET /api/payments/health-check/`
- Shipping logic:
  - Tamil Nadu: ₹50
  - Any other Indian state: ₹80
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
   - Add PhonePe sandbox values for payment testing:

   ```env
   PHONEPE_MERCHANT_ID=your_test_merchant_id
   PHONEPE_SALT_KEY=your_test_salt_key
   PHONEPE_SALT_INDEX=1
   ```

3. Optional infrastructure:

   - Redis is recommended for Celery and cache in realistic local testing.

4. Run migrations and seed data:

   ```bash
   python manage.py migrate
   python manage.py seed_data
   ```

5. Run server:

   ```bash
   python manage.py runserver
   ```

## Production Deployment (Web Hoster)

This repository is configured for standard Python web hosting (Passenger/cPanel, VPS, or dedicated Linux hosting).

### Passenger/cPanel Steps

1. Upload project files.
2. Create/activate virtual environment and install requirements.
3. Configure `.env` with production values (`DEBUG=False`, secure `SECRET_KEY`, DB, Redis, SMTP, PhonePe).
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

   - Verify `PHONEPE_MERCHANT_ID`, `PHONEPE_SALT_KEY`, `PHONEPE_SALT_INDEX`.
   - Confirm `/api/payments/health-check/` reports healthy.

## Notes

- With `DEBUG=False`, the app enforces HTTPS-related security behavior.
- Current repository has minimal automated tests; add app-level tests for order and payment flows for stronger release confidence.
