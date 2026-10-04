import os
os.environ.setdefault("CITEMAP_DB", "/tmp/citemap.db")   # Vercel can only write to /tmp
from server.app import app  # noqa: E402,F401
