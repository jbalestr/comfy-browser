"""
HTTP server for the ComfyUI browser.

RequestHandler only knows about routes and HTTP concerns (status codes,
headers, path safety). It holds no scanning or parsing logic itself —
that all lives behind self.server.scanner, which is injected by main.py.
This keeps the handler swappable/testable independently of how files
are actually scanned or cached.
"""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from .scanner import FolderScanner

STATIC_DIR = Path(__file__).parent / "static"


class ComfyBrowserServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer that carries a FolderScanner for its handlers to use."""

    def __init__(self, address, handler_cls, scanner: FolderScanner):
        super().__init__(address, handler_cls)
        self.scanner = scanner


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet; flip on for debugging if needed

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self._serve_static_file("index.html", "text/html; charset=utf-8")
        elif parsed.path == "/api/data":
            self._serve_data(parsed.query)
        elif parsed.path.startswith("/static/"):
            self._serve_static_file(parsed.path[len("/static/"):])
        elif parsed.path.startswith("/image/"):
            self._serve_image(parsed.path[len("/image/"):])
        else:
            self.send_response(404)
            self.end_headers()

    # ---------- Route handlers ----------

    def _serve_data(self, query_string: str) -> None:
        qs = parse_qs(query_string)
        force_refresh = qs.get("refresh", ["0"])[0] == "1"

        data = self.server.scanner.scan(force_refresh=force_refresh)
        body = json.dumps(data).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static_file(self, rel_path_encoded: str, content_type: str | None = None) -> None:
        rel_path = unquote(rel_path_encoded)
        full_path = (STATIC_DIR / rel_path).resolve()
        if STATIC_DIR.resolve() not in full_path.parents and full_path != STATIC_DIR.resolve():
            self.send_response(403)
            self.end_headers()
            return
        if not full_path.exists():
            self.send_response(404)
            self.end_headers()
            return

        if content_type is None:
            content_type, _ = mimetypes.guess_type(str(full_path))
            content_type = content_type or "application/octet-stream"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        with open(full_path, "rb") as f:
            self.wfile.write(f.read())

    def _serve_image(self, rel_path_encoded: str) -> None:
        rel_path = unquote(rel_path_encoded)
        folder = Path(self.server.scanner.folder).resolve()
        full_path = (folder / rel_path).resolve()

        # Prevent path traversal outside the target folder.
        if folder not in full_path.parents and full_path != folder:
            self.send_response(403)
            self.end_headers()
            return
        if not full_path.exists():
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.end_headers()
        with open(full_path, "rb") as f:
            self.wfile.write(f.read())


def create_server(folder: str, scanner: FolderScanner, port: int) -> ComfyBrowserServer:
    return ComfyBrowserServer(("127.0.0.1", port), RequestHandler, scanner)
