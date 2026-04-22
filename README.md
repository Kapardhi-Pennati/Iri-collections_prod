# Iri Collections - E-Commerce Platform

A premium, full-featured jewelry e-commerce platform built with Django, Django REST Framework, and a custom high-performance vanilla JavaScript frontend.

## Enterprise Security Hardening
The platform has been hardened with a production-grade defensive security layer:
- **Secure Authentication**: Argon2 password hashing, cryptographically secure OTPs (`secrets`), and JWT session management.
- **Brute-Force Protection**: Account lockout (1 hour after 5 failed attempts) and endpoint-level throttling.
- **Integrity**: HMAC-SHA256 signature verification for all payment webhooks.
- **Infrastructure**: Strategic HTTP security headers (HSTS, CSP, X-Frame-Options), strict CSRF/CORS whitelisting, and secure cookie policies.
- **Audit Trail**: Detailed security-sensitive event logging in `logs/audit.log`.

## Features
- **Premium Design**: light Gold theme luxury aesthetic utilizing `Sans Funnel` typography.
- **Robust E-Commerce**: Product catalog, cart management, atomic order transactions with row-level locking.
- **PhonePe Integration**: UPI/Card/NetBanking payment flow with signed callback verification.
- **Printable Invoices**: Native browser-optimized print stylesheets for billing.

## Local Development Setup

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Setup your environment variables**:
   Create a `.env` file in the root directory (see `.env.example`):
   ```
   SECRET_KEY=your_secret_key
   DEBUG=True
   ALLOWED_HOSTS= localhost
   PHONEPE_MERCHANT_ID=your_test_merchant_id
   PHONEPE_SALT_KEY=your_test_salt_key
   PHONEPE_SALT_INDEX=1
   ```

3. **Infrastrucutre**:
   Ensure [Redis](https://redis.io/) is installed and running for session/rate limiting:
   ```bash
   redis-server
   ```

4. **Run Migrations & Seed Data**:
   ```bash
   python manage.py migrate
   python manage.py seed_data
   ```

5. **Run Server**:
   ```bash
   python manage.py runserver
   ```

## Production Deployment (Web Hoster)

This repository is configured for standard Python web hosting (shared hosting with Passenger, VPS, or dedicated server).

### Passenger/cPanel Deployment Steps

1. **Upload project files** to your hosting account.

2. **Create and activate virtual environment**, then install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**:
   - Copy `.env.example` to `.env`
   - Set production values (`DEBUG=False`, `SECRET_KEY`, `ALLOWED_HOSTS`, DB, Redis, SMTP, PhonePe)

4. **Run migrations and collect static files**:
   ```bash
   python manage.py migrate
   python manage.py collectstatic --noinput
   ```

5. **Use Passenger entrypoint**:
   - Ensure `passenger_wsgi.py` is in your app root
   - Point your host's Python application root to this project
   - Restart the Python app from your hosting panel

### Deployment Readiness Checklist

Run these before each production deploy:

1. **Environment and security checks**:
   ```bash
   py -3 manage.py check --deploy
   ```

2. **Migration drift check**:
   ```bash
   py -3 manage.py makemigrations --check --dry-run
   ```

3. **Apply migrations and collect static**:
   ```bash
   py -3 manage.py migrate
   py -3 manage.py collectstatic --noinput
   ```

4. **Payment gateway sanity check**:
   - Verify `PHONEPE_MERCHANT_ID`, `PHONEPE_SALT_KEY`, `PHONEPE_SALT_INDEX` are set.
   - Confirm `/api/payments/health-check/` returns `{"status":"healthy"...}`.

### Architecture

- **Runtime**: Python 3.13 (WSGI)
- **Server**: Passenger / Gunicorn + reverse proxy
- **Static Files**: WhiteNoise
- **Database**: PostgreSQL or MySQL
- **Media**: Local storage or external object storage (optional)

*(Note: When `DEBUG=False`, the app enforces HTTPS-related security settings and serves static files via WhiteNoise.)*
