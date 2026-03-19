"""Vercel serverless entry: all routes are sent here via vercel.json routes."""
import sys
from pathlib import Path

# Vercel runs this file from api/; project root must be on path for "dashboard" and "roostoo"
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dashboard.server import app
