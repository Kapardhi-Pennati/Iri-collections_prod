#!/bin/bash

echo "Building project..."
python3.12 -m pip install -r requirements.txt
python3.12 manage.py migrate --noinput
python3.12 manage.py collectstatic --noinput
echo "Build complete."
