import os
import sys

# Tambahkan root directory ke sys.path untuk Vercel Serverless Function
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from loan_project.wsgi import app
