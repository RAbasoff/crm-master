"""
WSGI configuration for PythonAnywhere deployment.
Replace 'yourusername' with your actual PythonAnywhere username in the path below.
"""
import sys
import os

# Add your project directory to sys.path
project_home = '/home/yourusername/CRM_Мастерская'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Set environment variable for production
os.environ.setdefault('FLASK_ENV', 'production')

# Import your Flask app
from app import app as application
