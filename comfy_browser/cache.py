"""
Persistent cache for per-file metadata, keyed by filename + mtime + size.

This module knows nothing about ComfyUI or PNGs — it just stores and
retrieves JSON-serialisable dicts against a stamp (mtime, size) so a
caller can tell whether a previously-cached entry is still valid for
a given file. Keeping that logic here (rather than in the scanner)
means the on-disk cache format could change without touching scanning
or metadata-extraction code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class FileCache:
    """A simple on-disk cache mapping a file's (path, mtime, size) stamp
    to an arbitrary JSON-serialisable record."""

    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self._store: dict[str, dict] = {}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.cache_path.exists():
            self._store = {}
            return
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                self._store = json.load(f)
        except Exception:
            self._store = {}

    def save(self) -> None:
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self._store, f)
        except OSError as e:
            print(f"Warning: could not write cache file {self.cache_path}: {e}")

    @staticmethod
    def _stamp_key(rel_path: str, mtime: float, size: int) -> str:
        return f"{rel_path}|{mtime}|{size}"

    def get(self, rel_path: str, mtime: float, size: int) -> Optional[dict]:
        """Return the cached record if present and the stamp matches, else None."""
        self.load()
        return self._store.get(self._stamp_key(rel_path, mtime, size))

    def put(self, rel_path: str, mtime: float, size: int, record: dict) -> None:
        self._store[self._stamp_key(rel_path, mtime, size)] = record

    def replace_all(self, new_store: dict) -> None:
        """Swap in a freshly-built store (drops stale entries for deleted files)."""
        self._store = new_store

    def clear(self) -> None:
        self._store = {}
        self._loaded = True
