"""
HTTP server for the ComfyUI browser.

RequestHandler only knows about routes and HTTP concerns (status codes,
headers, path safety). It holds no scanning or parsing logic itself —
that all lives behind self.server.coordinator, which is injected by
main.py. This keeps the handler swappable/testable independently of how
files are actually scanned or cached.

ScanCoordinator owns the "is a scan currently running, what's its
progress, what was the last result" state, and runs scans on a
background thread so a slow cold scan doesn't block the HTTP response
(and therefore doesn't make the browser look hung).
"""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from .scanner import FolderScanner

STATIC_DIR = Path(__file__).parent / "static"


class ScanCoordinator:
    """Runs FolderScanner.scan() on a background thread and exposes
    progress/result state for the HTTP handler to poll."""

    def __init__(self, scanner: FolderScanner):
        self.scanner = scanner
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._done_count = 0
        self._total_count = 0
        self._result: list[dict] | None = None
        self._error: str | None = None

    def start_scan(self, force_refresh: bool = False) -> None:
        """Kick off a scan in the background if one isn't already running."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._done_count = 0
            self._total_count = 0
            self._error = None

        def _run():
            try:
                result = self.scanner.scan(
                    force_refresh=force_refresh,
                    on_progress=self._on_progress,
                )
                with self._lock:
                    self._result = result
            except Exception as e:
                with self._lock:
                    self._error = str(e)
            finally:
                with self._lock:
                    self._running = False

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def _on_progress(self, done: int, total: int) -> None:
        with self._lock:
            self._done_count = done
            self._total_count = total

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "done": self._done_count,
                "total": self._total_count,
                "has_result": self._result is not None,
                "error": self._error,
            }

    def get_result(self) -> list[dict] | None:
        with self._lock:
            return self._result


class ComfyBrowserServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer that carries a ScanCoordinator for its handlers to use."""

    def __init__(self, address, handler_cls, coordinator: ScanCoordinator):
        super().__init__(address, handler_cls)
        self.coordinator = coordinator


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet; flip on for debugging if needed

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self._serve_static_file("index.html", "text/html; charset=utf-8")
        elif parsed.path == "/api/data":
            self._serve_data(parsed.query)
        elif parsed.path == "/api/scan-status":
            self._serve_scan_status()
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

        coordinator = self.server.coordinator
        status = coordinator.status()

        # Kick off a scan if none has ever run, or a refresh was requested
        # and nothing is currently running.
        if not status["running"] and (force_refresh or not status["has_result"]):
            coordinator.start_scan(force_refresh=force_refresh)
            status = coordinator.status()

        result = coordinator.get_result() if not status["running"] else None

        body = json.dumps({
            "scanning": status["running"],
            "done": status["done"],
            "total": status["total"],
            "data": result,  # null while still scanning and no prior result exists
        }).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _serve_scan_status(self) -> None:
        status = self.server.coordinator.status()
        body = json.dumps(status).encode("utf-8")
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
        folder = Path(self.server.coordinator.scanner.folder).resolve()
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


def create_server(coordinator: ScanCoordinator, port: int) -> ComfyBrowserServer:
    return ComfyBrowserServer(("127.0.0.1", port), RequestHandler, coordinator)
