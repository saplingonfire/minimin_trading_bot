"""Vercel entrypoint: expose FastAPI app at root so all routes are served."""
from dashboard.server import app
