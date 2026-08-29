#!/usr/bin/env python3
"""Create a reproducible manifest for the generated scraper output."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
files = {}
for path in sorted(DATA_DIR.glob("*.json")):
    if path.name.startswith("_"):
        continue
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    value = json.loads(path.read_text(encoding="utf-8"))
    count = len(value) if isinstance(value, list) else 0
    files[path.stem] = {"file": path.name, "records": count, "sha256": digest}

manifest = {
    "manifestVersion": 1,
    "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "source": "gzw-scraper",
    "datasets": files,
}
(DATA_DIR / "_manifest.json").write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(f"Wrote {DATA_DIR / '_manifest.json'} for {len(files)} dataset(s).")
