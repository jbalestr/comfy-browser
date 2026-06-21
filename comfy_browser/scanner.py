"""
Walks a target folder for PNG files and returns metadata for each,
using a FileCache to skip re-parsing unchanged files.

FolderScanner depends on a Cache-like object and a metadata-extractor
callable passed in at construction time, rather than importing concrete
implementations directly. This means the cache backend or the parsing
logic can be swapped (e.g. for tests, or a future SQLite cache) without
changing this file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Protocol

from .cache import FileCache

MetadataExtractor = Callable[[Path], Optional[dict]]

EMPTY_FIELDS = {
    "checkpoints": [], "loras": [], "embeddings": [],
    "seeds": [], "samplers": [], "positive_prompt": "",
    "negative_prompt": "", "width": None, "height": None,
}

CACHE_FILENAME = ".comfy_browser_cache.json"


class CacheLike(Protocol):
    def get(self, rel_path: str, mtime: float, size: int) -> Optional[dict]: ...
    def put(self, rel_path: str, mtime: float, size: int, record: dict) -> None: ...
    def replace_all(self, new_store: dict) -> None: ...
    def save(self) -> None: ...
    def clear(self) -> None: ...


class FolderScanner:
    def __init__(
        self,
        folder: str | Path,
        extractor: MetadataExtractor,
        cache: Optional[CacheLike] = None,
    ):
        self.folder = Path(folder)
        self.extractor = extractor
        self.cache = cache if cache is not None else FileCache(self.folder / CACHE_FILENAME)

    def scan(self, force_refresh: bool = False) -> list[dict]:
        """Return metadata for every PNG under the folder (recursive).
        Unchanged files are served from cache unless force_refresh is set."""
        if not self.folder.exists():
            return []

        if force_refresh:
            self.cache.clear()

        png_files = sorted(
            self.folder.rglob("*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        results: list[dict] = []
        fresh_store: dict[str, dict] = {}
        any_new = False

        for png_path in png_files:
            rel_path = str(png_path.relative_to(self.folder))
            stat = png_path.stat()

            cached = self.cache.get(rel_path, stat.st_mtime, stat.st_size)
            if cached is not None:
                entry = cached
            else:
                entry = self._build_entry(png_path, rel_path, stat.st_mtime)
                any_new = True

            self.cache.put(rel_path, stat.st_mtime, stat.st_size, entry)
            fresh_store[self.cache._stamp_key(rel_path, stat.st_mtime, stat.st_size)] = entry
            results.append(entry)

        # Drop entries for files that no longer exist, then persist.
        self.cache.replace_all(fresh_store)
        if any_new or force_refresh:
            self.cache.save()

        return results

    def _build_entry(self, png_path: Path, rel_path: str, mtime: float) -> dict:
        meta = self.extractor(png_path)
        entry = {"filename": rel_path, "mtime": mtime}
        entry.update(meta if meta else EMPTY_FIELDS)
        return entry
