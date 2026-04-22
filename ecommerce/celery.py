"""
Celery Application Configuration
=================================
Initializes the Celery app for asynchronous task processing.
Uses Redis as the message broker (already configured for caching).

Usage:
  Start worker:  celery -A ecommerce worker -l info
  Start beat:    celery -A ecommerce beat -l info (for scheduled tasks)
"""

import os
from celery import Celery

# ✅ Set default Django settings module for Celery workers.
# This ensures workers have access to all Django models and settings
# even when started from the command line (not via manage.py).
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ecommerce.settings")

app = Celery("ecommerce")

# ✅ Read Celery config from Django settings, using CELERY_ namespace.
# This means all Celery settings are prefixed with CELERY_ in settings.py
# (e.g., CELERY_BROKER_URL instead of broker_url).
app.config_from_object("django.conf:settings", namespace="CELERY")

# ✅ Auto-discover tasks from all INSTALLED_APPS.
# Celery will look for a tasks.py file in each installed app.
app.autodiscover_tasks()
