import os
import sys

# Set up paths and environment variables
# This ensures the virtual environment is used
venv_path = os.path.join(os.path.dirname(__file__), 'venv/bin/python3')
if sys.executable != venv_path and os.path.exists(venv_path):
    os.execl(venv_path, venv_path, *sys.argv)

sys.path.insert(0, os.path.dirname(__file__))

# Tell Django where the settings module is
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ecommerce.settings")

# Import the Django WSGI application
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
