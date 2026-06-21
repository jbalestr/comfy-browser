# ComfyUI Output Browser

A local gallery for browsing ComfyUI-generated PNGs, with filters for
checkpoint, LoRA, embeddings, sampler, and a text search over prompts.

## Setup

```bash
pip install -r requirements.txt --break-system-packages
```

## Run

```bash
python3 -m comfy_browser /path/to/ComfyUI/output
```

Then open http://localhost:8765

In VS Code: open this folder, press **F5**, and enter your output folder
path when prompted (also editable as the `default` in `.vscode/launch.json`).

## Project layout

```
comfy_browser/
├── __main__.py      entry point — wires concrete pieces together
├── metadata.py       parses ComfyUI's embedded PNG workflow JSON
├── cache.py           on-disk cache, keyed by filename + mtime + size
├── scanner.py         walks the folder, combines cache + metadata
├── server.py          HTTP routing only — no parsing/cache logic
└── static/
    ├── index.html
    ├── app.js
    └── style.css
```

Each module has one job:

- **metadata.py** — knows ComfyUI's node graph shape. Adding support for
  a new node type (ControlNet, IPAdapter, etc.) means registering a new
  handler function here, not editing a long if/elif chain.
- **cache.py** — knows nothing about PNGs or ComfyUI. Just a stamp-keyed
  JSON store. Could be swapped for SQLite later without touching anything
  else.
- **scanner.py** — coordinates the cache and the metadata extractor, both
  passed in rather than imported directly, so either can be swapped or
  mocked independently (e.g. in tests).
- **server.py** — pure HTTP plumbing. Delegates all real work to the
  injected `FolderScanner`.

A `.comfy_browser_cache.json` file is created inside your output folder
to persist the cache between runs. Use the "Rescan folder" button in the
UI to force a full re-parse.
