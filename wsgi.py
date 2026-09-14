"""
WSGI configuration for PythonAnywhere deployment.
Replace 'yourusername' with your actual PythonAnywhere username in the path below.
"""
import sys
import os

# Add your project directory to sys.path
project_home = '/home/rabasoff/crm-master'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Set environment variable for production
os.environ.setdefault('FLASK_ENV', 'production')

# SQLite database (local file — simpler, no MySQL needed)
# To switch to MySQL later, uncomment and set password:
# os.environ.setdefault('DATABASE_URL', 'mysql+pymysql://rabasoff:PASSWORD@rabasoff.mysql.pythonanywhere-services.com/rabasoff$werkplaats?charset=utf8mb4')

# Import your Flask app
from app import app as application
