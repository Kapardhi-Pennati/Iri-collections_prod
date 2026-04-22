web: gunicorn ecommerce.wsgi:application --bind 0.0.0.0:$PORT
worker: celery -A ecommerce worker -l info
