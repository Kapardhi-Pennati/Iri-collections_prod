# ✅ Import Celery app so it's loaded when Django starts.
# This ensures the @shared_task decorator uses this Celery app instance
# and that task autodiscovery works correctly.
from .celery import app as celery_app

__all__ = ("celery_app",)
