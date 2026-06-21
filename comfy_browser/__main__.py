#!/usr/bin/env python3
"""
ComfyUI Output Browser

Point it at your ComfyUI output folder, it serves a local gallery with
filters for checkpoint, LoRA, embeddings, seed and sampler, pulled from
the workflow metadata ComfyUI embeds in each PNG.

Usage:
    python3 -m comfy_browser /path/to/ComfyUI/output

Then open http://localhost:8765 in your browser.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .cache import FileCache
from .metadata import extract_png_metadata
from .scanner import CACHE_FILENAME, FolderScanner
from .server import ScanCoordinator, create_server

DEFAULT_PORT = 8765


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Browse ComfyUI output PNGs with metadata filters.")
    parser.add_argument("folder", help="Path to your ComfyUI output folder")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port to serve on (default {DEFAULT_PORT})")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if not os.path.isdir(args.folder):
        print(f"Folder not found: {args.folder}")
        sys.exit(1)

    cache = FileCache(cache_path=Path(args.folder) / CACHE_FILENAME)
    scanner = FolderScanner(folder=args.folder, extractor=extract_png_metadata, cache=cache)
    coordinator = ScanCoordinator(scanner)

    server = create_server(coordinator, args.port)
    print(f"Serving at http://localhost:{args.port}")
    print(f"Watching {args.folder} (metadata cached in {CACHE_FILENAME}; "
          f"first scan of new/changed files happens in the browser, with progress shown there).")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
