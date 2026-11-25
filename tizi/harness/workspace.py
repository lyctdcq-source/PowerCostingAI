# -*- coding: utf-8 -*-
"""L4 文件系统协作原语（01_catalog ~ 11_exports 沙箱工作区与大对象自动外置）。"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

WORKSPACE_DIRS = (
    "01_catalog",
    "02_features",
    "03_cv_segments",
    "04_ocr_crops",
    "05_vlm_evidence",
    "06_constraints",
    "07_hitl",
    "08_store",
    "09_checkpoints",
    "10_traces",
    "11_exports",
)

INLINE_BYTE_LIMIT = 2048
INLINE_ITEM_LIMIT = 20


class WorkspacePathError(PermissionError):
    """越权访问工作区外部路径时抛出。"""


class TiziWorkspace:
    """管理单个会话 DATA/tizi/<session_id>/ 下的 11 层不可变工件目录。"""

    def __init__(self, root_dir: str | Path, session_id: str = ""):
        self.root = Path(root_dir).resolve()
        self.session_id = session_id or self.root.name
        self._io_lock = threading.RLock()
        self.ensure_layout()

    def ensure_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in WORKSPACE_DIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def resolve_safe(self, rel_uri: str | Path) -> Path:
        raw = str(rel_uri or "").replace("\\", "/").lstrip("/")
        if not raw:
            raise WorkspacePathError("空工件路径")
        target = (self.root / raw).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise WorkspacePathError(f"越权路径被拒绝：{rel_uri}") from exc
        return target

    def to_uri(self, path: Path) -> str:
        rel = Path(path).resolve().relative_to(self.root)
        return str(rel).replace("\\", "/")

    def write_json(self, rel_uri: str, payload: Any) -> str:
        target = self.resolve_safe(rel_uri)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        with self._io_lock:
            tmp = target.with_name(f"{target.name}.{threading.get_ident()}.{uuid.uuid4().hex[:6]}.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, target)
        return self.to_uri(target)

    def read_json(self, rel_uri: str, default: Any = None) -> Any:
        target = self.resolve_safe(rel_uri)
        with self._io_lock:
            if not target.is_file():
                return default
            return json.loads(target.read_text(encoding="utf-8"))

    def append_jsonl(self, rel_uri: str, record: dict[str, Any]) -> str:
        target = self.resolve_safe(rel_uri)
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with self._io_lock:
            with target.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return self.to_uri(target)

    def read_jsonl(self, rel_uri: str) -> list[dict[str, Any]]:
        target = self.resolve_safe(rel_uri)
        with self._io_lock:
            if not target.is_file():
                return []
            raw = target.read_text(encoding="utf-8")
        out: list[dict[str, Any]] = []
        for ln in raw.splitlines():
            s = ln.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    out.append(obj)
            except ValueError:
                continue
        return out

    def write_bytes(self, rel_uri: str, data: bytes) -> str:
        target = self.resolve_safe(rel_uri)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._io_lock:
            tmp = target.with_name(f"{target.name}.{threading.get_ident()}.{uuid.uuid4().hex[:6]}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        return self.to_uri(target)

    def read_slice(self, rel_uri: str, offset: int = 0, limit: int = 20, key: str = "") -> dict[str, Any]:
        """按分片读取大型 JSON 列表或字典，防止上下文腐烂。"""
        obj = self.read_json(rel_uri)
        if obj is None:
            return {"exists": False, "uri": rel_uri, "items": [], "total": 0}
        if key and isinstance(obj, dict):
            obj = obj.get(key)
        if isinstance(obj, list):
            start = max(0, int(offset))
            end = start + max(1, min(100, int(limit)))
            return {
                "exists": True,
                "uri": rel_uri,
                "total": len(obj),
                "offset": start,
                "items": obj[start:end],
            }
        if isinstance(obj, dict):
            keys = list(obj.keys())
            start = max(0, int(offset))
            end = start + max(1, min(100, int(limit)))
            sub = {k: obj[k] for k in keys[start:end]}
            return {
                "exists": True,
                "uri": rel_uri,
                "total": len(keys),
                "offset": start,
                "items": sub,
            }
        return {"exists": True, "uri": rel_uri, "value": obj}

    def externalize_if_large(self, subdir: str, prefix: str, payload: Any) -> tuple[Any, str | None, str]:
        """Artifact-by-Reference：返回体超 2048 字节或 >20 条明细时强制外置落盘。"""
        raw = json.dumps(payload, ensure_ascii=False, default=str)
        raw_bytes = len(raw.encode("utf-8"))
        item_count = None
        if isinstance(payload, list):
            item_count = len(payload)
        elif isinstance(payload, dict):
            for k in ("rows", "items", "regions", "conflicts", "records"):
                if isinstance(payload.get(k), list):
                    item_count = len(payload[k])
                    break
        if raw_bytes <= INLINE_BYTE_LIMIT and (item_count is None or item_count <= INLINE_ITEM_LIMIT):
            return payload, None, f"inline ({raw_bytes}B)"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
        uri = self.write_json(f"{subdir}/{prefix}_{digest}.json", payload)
        summary = f"externalized to {uri} ({raw_bytes}B" + (f", {item_count} items)" if item_count is not None else ")")
        return None, uri, summary

    def update_manifest(self, **fields: Any) -> str:
        cur = self.read_json("manifest.json", default={}) or {}
        cur.setdefault("session_id", self.session_id)
        cur.setdefault("graph_version", "2.0")
        cur["updated_at"] = datetime.now().isoformat(timespec="seconds")
        cur.update(fields)
        return self.write_json("manifest.json", cur)

    @property
    def checkpoint_db_path(self) -> Path:
        return self.resolve_safe("09_checkpoints/langgraph.sqlite")
