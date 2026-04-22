import os
import sys

# Set up paths and environment variables
sys.path.insert(0, os.path.dirname(__file__))

# Tell Django where the settings module is
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ecommerce.settings")

# Import the Django WSGI application
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
