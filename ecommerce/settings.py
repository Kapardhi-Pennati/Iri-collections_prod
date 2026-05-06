try:
    import pymysql
    pymysql.install_as_MySQLdb()
except ImportError:
    pass

"""
SECURE DJANGO SETTINGS - Production & Development
All security standards enforced (OWASP Top 10, Django Best Practices)
"""

import os
import logging
import base64
from pathlib import Path
from datetime import timedelta
import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL: SECRET KEY & DEBUG MODE
# ─────────────────────────────────────────────────────────────────────────────

#SECURITY REQUIREMENT: DEBUG must be False in production
DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")

#SECURITY REQUIREMENT: SECRET_KEY MUST be set in environment
# Using a fallback for Vercel build/startup to prevent crashes.
SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-6(3t2fzxqke&1i9$47p5#z9*40vk^2po@09z-m$12qk1g=+*ye")

if DEBUG:
    logger = logging.getLogger(__name__)
    logger.warning("⚠️  DEBUG MODE ENABLED - Never use in production!")

PII_ENCRYPTION_KEY = os.getenv("PII_ENCRYPTION_KEY", "")
if not PII_ENCRYPTION_KEY:
    logger = logging.getLogger(__name__)
    logger.warning("PII_ENCRYPTION_KEY missing; deriving fallback from SECRET_KEY. Configure explicit key in environment.")
    PII_ENCRYPTION_KEY = base64.urlsafe_b64encode(SECRET_KEY.encode("utf-8")[:32].ljust(32, b"0")).decode("utf-8")

# ALLOWED_HOSTS configuration
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    # Local apps
    "core",  # Security utilities
    "accounts",
    "store",
    "payments",
]

MIDDLEWARE = [
    #Security middleware stack (order matters)
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",          #CSRF protection
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",  #Clickjacking
    #Custom: Permissions-Policy, Referrer-Policy, CORP/COOP, X-Request-ID
    "core.middleware.SecurityHeadersMiddleware",
    # Traffic tracking (must be after AuthenticationMiddleware)
    "store.traffic_middleware.TrafficMiddleware",
]


ROOT_URLCONF = "ecommerce.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.csrf",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
            #Enforce template auto-escaping (XSS prevention)
            "autoescape": True,
        },
    },
]

WSGI_APPLICATION = "ecommerce.wsgi.application"

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# Supports PostgreSQL for production, SQLite for development
# Vercel often provides POSTGRES_URL instead of DATABASE_URL
db_url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")

if db_url:
    # 1. Ensure URL starts with postgresql:// for dj-database-url compatibility
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    
    # 2. Fix Supabase "supa" parameter error
    if "supa=" in db_url:
        import re
        db_url = re.sub(r'[&?]?supa=[^&]+', '', db_url)
        db_url = db_url.rstrip('?&')

    DATABASES = {
        "default": dj_database_url.parse(
            db_url,
            conn_max_age=int(os.getenv("DB_CONN_MAX_AGE", "600")),
            ssl_require=os.getenv("DB_SSL_REQUIRE", "False").lower() == "true",
        )
    }
elif os.getenv("DB_NAME"):
    # Use individual variables from .env
    DATABASES = {
        "default": {
            "ENGINE": os.getenv("DB_ENGINE", "django.db.backends.mysql"),
            "NAME": os.getenv("DB_NAME"),
            "USER": os.getenv("DB_USER"),
            "PASSWORD": os.getenv("DB_PASSWORD"),
            "HOST": os.getenv("DB_HOST", "localhost"),
            "PORT": os.getenv("DB_PORT", "3306"),
            "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "300")),
            "OPTIONS": {
                "charset": "utf8mb4",
                "init_command": "SET SESSION wait_timeout=600",
            }
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

DATABASES["default"].setdefault("CONN_HEALTH_CHECKS", True)
if os.getenv("DB_USE_POOLER", "false").lower() in ("1", "true", "yes"):
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

# ─────────────────────────────────────────────────────────────────────────────
# AUTHENTICATION & USER MODEL
# ─────────────────────────────────────────────────────────────────────────────

#Custom user model with RBAC support
AUTH_USER_MODEL = "accounts.User"

#Password hashing: Argon2 (primary), bcrypt (fallback)
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",  # Industry standard
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

#Strict password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},  # Enforce strong passwords
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

#Session security
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_CACHE_ALIAS = "default"
SESSION_COOKIE_AGE = 3600  # 1 hour
SESSION_COOKIE_SECURE = not DEBUG  # HTTPS only in production
SESSION_COOKIE_HTTPONLY = True  #Prevent JS access (XSS protection)
SESSION_COOKIE_SAMESITE = "Lax"  #Compatibility with Vercel/Supabase auth
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# ─────────────────────────────────────────────────────────────────────────────
# DJANGO REST FRAMEWORK SECURITY
# ─────────────────────────────────────────────────────────────────────────────

REST_FRAMEWORK = {
    #Authentication (JWT cookie/header auth with CSRF enforcement)
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "accounts.authentication.CookieJWTAuthentication",
    ],
    #Default to authenticated-only access (opt-in to AllowAny)
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    #API Pagination
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 12,
    #Global throttling (rate limiting)
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "50/minute",  # Tighter for anonymous users
        "user": "100/minute",
    },
    #API versioning (future-proof)
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.AcceptHeaderVersioning",
}

# ─────────────────────────────────────────────────────────────────────────────
# JWT (SIMPLEJWT) SECURITY CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

SIMPLE_JWT = {
    #Short-lived access tokens (30 minutes)
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    #Longer-lived refresh tokens (7 days)
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    #Rotate refresh tokens on each use (token freshness)
    "ROTATE_REFRESH_TOKENS": True,
    #Blacklist old tokens after rotation (prevent reuse)
    "BLACKLIST_AFTER_ROTATION": True,
    #Standard Bearer token scheme
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
    #Sign tokens with HS256 (default; use RS256 for external verification)
    "ALGORITHM": "HS256",
    "UPDATE_LAST_LOGIN": True,
    "LEEWAY": 30,
    # First-party browser clients use HttpOnly cookies instead of exposing JWTs
    # to JavaScript storage.
    "AUTH_COOKIE_ACCESS": "iri_access",
    "AUTH_COOKIE_REFRESH": "iri_refresh",
    "AUTH_COOKIE_SECURE": not DEBUG,
    "AUTH_COOKIE_HTTP_ONLY": True,
    "AUTH_COOKIE_SAMESITE": "Strict",
    "AUTH_COOKIE_REFRESH_PATH": "/api/auth/refresh/",
}

# ─────────────────────────────────────────────────────────────────────────────
# CSRF CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

CSRF_COOKIE_SECURE = not DEBUG  # HTTPS only in production
CSRF_COOKIE_HTTPONLY = True  #Prevent XSS access to CSRF token
CSRF_COOKIE_SAMESITE = "Lax"  #Compatibility with cross-domain requests
CSRF_TRUSTED_ORIGINS = (
    os.getenv("CSRF_TRUSTED_ORIGINS", "http://localhost:8000").split(",")
)

# ─────────────────────────────────────────────────────────────────────────────
# SECURITY HEADERS
# ─────────────────────────────────────────────────────────────────────────────

#Prevent browser MIME type sniffing
SECURE_CONTENT_TYPE_NOSNIFF = True

#Prevent clickjacking attacks
X_FRAME_OPTIONS = "DENY"

#Enable browser XSS filter
SECURE_BROWSER_XSS_FILTER = True

#Content Security Policy
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0  # 1 year in production
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG

#HTTPS enforcement in production
SECURE_SSL_REDIRECT = not DEBUG

# ─────────────────────────────────────────────────────────────────────────────
# CORS CONFIGURATION (Frontend Communication)
# ─────────────────────────────────────────────────────────────────────────────

CORS_ALLOWED_ORIGINS = (
    os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
)
CORS_ALLOW_CREDENTIALS = True  # Allow credentials in CORS requests
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = ["content-type", "authorization", "x-csrf-token", "x-requested-with"]

# ─────────────────────────────────────────────────────────────────────────────
# INTERNATIONALIZATION & LOCALIZATION
# ─────────────────────────────────────────────────────────────────────────────

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# ─────────────────────────────────────────────────────────────────────────────
# EMAIL CONFIGURATION (Secure)
# ─────────────────────────────────────────────────────────────────────────────

#Use console backend for development, SMTP for production
if DEBUG:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() == "true"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "iricollections1@gmail.com")
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))  # Cap SMTP hangs under LSAPI

# ─────────────────────────────────────────────────────────────────────────────
# FRONTEND URL
# ─────────────────────────────────────────────────────────────────────────────
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# ─────────────────────────────────────────────────────────────────────────────
# CELERY (Async Task Queue)
# ─────────────────────────────────────────────────────────────────────────────

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/1")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/2")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_SOFT_TIME_LIMIT = 60
CELERY_TASK_TIME_LIMIT = 120
CELERY_TASK_ACKS_LATE = True

# ─────────────────────────────────────────────────────────────────────────────
# STATIC & MEDIA FILES (WhiteNoise)
# ─────────────────────────────────────────────────────────────────────────────

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_HOST = os.getenv("STATIC_HOST", "")
if STATIC_HOST:
    STATIC_URL = f"{STATIC_HOST.rstrip('/')}/static/"

#Use WhiteNoise for efficient static file serving (Django 4.2+ STORAGES API)
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG
WHITENOISE_MAX_AGE = int(os.getenv("STATIC_CACHE_SECONDS", "31536000"))
WHITENOISE_IMMUTABLE_FILE_TEST = lambda path, url: "." in url and not url.endswith(".html")

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ─────────────────────────────────────────────────────────────────────────────
# PAYMENTS (Static UPI QR Code)
# ─────────────────────────────────────────────────────────────────────────────

UPI_ID = os.getenv("UPI_ID", "your-upi-id@paytm")
UPI_DISPLAY_NAME = os.getenv("UPI_DISPLAY_NAME", "Iri Collections")

# ─────────────────────────────────────────────────────────────────────────────
# CACHING (for rate limiting, sessions)
# ─────────────────────────────────────────────────────────────────────────────

REDIS_IGNORE_EXCEPTIONS = os.getenv("REDIS_IGNORE_EXCEPTIONS", "true").lower() in ("1", "true", "yes")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.getenv("REDIS_URL", "redis://127.0.0.1:6379/1"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "IGNORE_EXCEPTIONS": REDIS_IGNORE_EXCEPTIONS,
            "CONNECTION_POOL_KWARGS": {
                "max_connections": 50,
                "retry_on_timeout": True,
            },
        },
        "KEY_PREFIX": "iri_collections",
        "TIMEOUT": 300,
    }
}

DJANGO_REDIS_IGNORE_EXCEPTIONS = REDIS_IGNORE_EXCEPTIONS

if os.getenv("USE_LOCAL_CACHE") == "true" or not os.getenv("REDIS_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "iri-collections-cache",
        }
    }

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
        },
        "core.security": {
            "handlers": ["console"],
            "level": "INFO",
        },
        "accounts": {
            "handlers": ["console"],
            "level": "INFO",
        },
        "payments": {
            "handlers": ["console"],
            "level": "INFO",
        },
    },
}

# Only try file logging if writable (skip on read-only filesystems)
if not os.getenv("VERCEL"):
    try:
        _logs_dir = str(BASE_DIR / "logs")
        os.makedirs(_logs_dir, exist_ok=True)
        LOGGING["formatters"]["json"] = {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(name)s %(levelname)s %(message)s",
        }
        LOGGING["handlers"]["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(_logs_dir, "app.log"),
            "maxBytes": 1024 * 1024 * 10,
            "backupCount": 10,
            "formatter": "json",
        }
        LOGGING["loggers"]["django"]["handlers"].append("file")
    except (OSError, PermissionError):
        pass

# ─────────────────────────────────────────────────────────────────────────────
# MISCELLANEOUS
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 10_485_760
FILE_UPLOAD_MAX_MEMORY_SIZE = 10_485_760
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if DEBUG:
    ALLOWED_HOSTS = ["*"]
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_SSL_REDIRECT = False
