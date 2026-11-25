# -*- coding: utf-8 -*-
"""L1/L4 共享页级特征与视觉索引缓存（下沉复用 tizi/cache.py）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .. import cache as _legacy_cache

get_doc_features = _legacy_cache.get_doc_features
get_page_drawings = _legacy_cache.get_page_drawings
file_fingerprint = _legacy_cache.file_fingerprint


def get_visual_index_cache(pdf_path: str, cache_dir: str | Path) -> Optional[dict[str, Any]]:
    fp = file_fingerprint(pdf_path)
    p = Path(cache_dir) / f"visidx_{fp}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def set_visual_index_cache(pdf_path: str, cache_dir: str | Path, payload: dict[str, Any]) -> Path:
    fp = file_fingerprint(pdf_path)
    p = Path(cache_dir) / f"visidx_{fp}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p
