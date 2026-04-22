# Hosting Checklist

Use this checklist for Passenger/cPanel or VPS hosting.

## 1. Server Baseline

- [ ] Python 3.13 is installed on host.
- [ ] Virtual environment is created and activated.
- [ ] Required system services are available:
  - [ ] Database server (PostgreSQL/MySQL, or planned SQLite usage).
  - [ ] Redis (recommended for cache/Celery).
- [ ] Project files are uploaded to the application root.

## 2. Python Dependencies

- [ ] Install dependencies:

  ```bash
  python -m pip install -r requirements.txt
  ```

## 3. Environment Variables (.env)

- [ ] Copy `.env.example` to `.env`.
- [ ] Set required core values:
  - [ ] `SECRET_KEY`
  - [ ] `DEBUG=False`
  - [ ] `ALLOWED_HOSTS`
- [ ] Set database value:
  - [ ] `DATABASE_URL` or DB_ENGINE/DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT
- [ ] Set HTTPS/security values:
  - [ ] `SECURE_SSL_REDIRECT=True`
  - [ ] `SESSION_COOKIE_SECURE=True`
  - [ ] `CSRF_COOKIE_SECURE=True`
  - [ ] `CSRF_TRUSTED_ORIGINS`
- [ ] Set frontend/cross-origin values:
  - [ ] `CORS_ALLOWED_ORIGINS`
  - [ ] `FRONTEND_URL`
- [ ] Set email values for transactional mail:
  - [ ] `EMAIL_HOST`
  - [ ] `EMAIL_PORT`
  - [ ] `EMAIL_USE_TLS`
  - [ ] `EMAIL_HOST_USER`
  - [ ] `EMAIL_HOST_PASSWORD`
  - [ ] `DEFAULT_FROM_EMAIL`
- [ ] Set payment values:
  - [ ] `PHONEPE_MERCHANT_ID`
  - [ ] `PHONEPE_SALT_KEY`
  - [ ] `PHONEPE_SALT_INDEX`

## 4. Deploy Commands

- [ ] Run security/deploy checks:

  ```bash
  python manage.py check --deploy
  ```

- [ ] Confirm no migration drift:

  ```bash
  python manage.py makemigrations --check --dry-run
  ```

- [ ] Apply migrations:

  ```bash
  python manage.py migrate
  ```

- [ ] Collect static files:

  ```bash
  python manage.py collectstatic --noinput
  ```

## 5. App Entrypoint (Passenger)

- [ ] `passenger_wsgi.py` exists at app root.
- [ ] Host panel points Python app to this project.
- [ ] Python app is restarted from hosting panel after deploy.

## 6. Smoke Tests After Deploy

- [ ] Open homepage and catalog successfully.
- [ ] Login/signup works.
- [ ] Cart and checkout pages load.
- [ ] Payment health endpoint returns healthy:
  - [ ] `GET /api/payments/health-check/`
- [ ] Create a checkout with Tamil Nadu state and verify shipping fee is 50.
- [ ] Create a checkout with another India state and verify shipping fee is 80.
- [ ] Confirm shipping estimate updates after address/pincode changes in checkout.
- [ ] Confirm payment return/status flow works for a test transaction.
- [ ] Verify order appears in orders page after successful payment.

## 7. Monitoring and Backups

- [ ] Error logs are accessible.
- [ ] Audit/security logs are being written.
- [ ] Database backup job is scheduled.
- [ ] Media/static backup strategy is defined.

## 8. Rollback Plan

- [ ] Keep previous release snapshot available.
- [ ] Keep a database backup from pre-deploy.
- [ ] If rollback is needed:
  1. [ ] Restore previous release code.
  2. [ ] Restore database backup if schema/data changed incompatibly.
  3. [ ] Restart app and validate smoke tests.
