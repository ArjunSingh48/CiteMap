"""Vercel entry point. Vercel routes every request to this function (see vercel.json); the original
path arrives in the '__path' query parameter, so we put it back before FastAPI sees the request."""
import os
from urllib.parse import parse_qsl, urlencode

os.environ.setdefault("CITEMAP_DB", "/tmp/citemap.db")   # Vercel can only write to /tmp
from server.app import app as _app  # noqa: E402


async def app(scope, receive, send):
    if scope.get("type") == "http":
        qs = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
        orig = next((v for k, v in qs if k == "__path"), None)
        if orig is not None or scope.get("path", "").startswith("/api/index"):
            path = "/" + (orig or "").lstrip("/")
            rest = urlencode([(k, v) for k, v in qs if k != "__path"])
            scope = dict(scope, path=path, raw_path=path.encode(), query_string=rest.encode())
    await _app(scope, receive, send)
