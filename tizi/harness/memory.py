# -*- coding: utf-8 -*-
"""L4 跨工程企业长期记忆（EnterpriseMemory）：仅允许经 HITL 人工确认的条目沉淀复用。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..schemas.layout import LayoutProfile

_LOG = logging.getLogger("tizi.harness.memory")


class EnterpriseMemory:
    """位于 DATA/tizi/enterprise_memory/ 的版本化长期知识库。"""

    def __init__(self, base_dir: str | Path):
        self.root = Path(base_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.foundation_file = self.root / "foundation_lib_custom.jsonl"
        self.tower_file = self.root / "tower_param_custom.jsonl"
        self.layout_file = self.root / "institute_layout_profiles.jsonl"

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        items: list[dict[str, Any]] = []
        for ln in path.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    items.append(obj)
            except ValueError:
                continue
        return items

    @staticmethod
    def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # 1. 非标基础库（foundation_lib_custom.jsonl）
    def load_foundation_lib(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for rec in self._read_jsonl(self.foundation_file):
            model = str(rec.get("model") or "").strip().upper()
            if model and rec.get("human_confirmed") is True:
                out[model] = rec
        return out

    def promote_foundation(self, model: str, params: dict[str, Any], *, operator: str = "engineer", human_confirmed: bool = False) -> bool:
        if not human_confirmed:
            raise PermissionError("严禁将未经 HITL 人工确认的非标基础条目写入企业长期记忆")
        key = str(model or "").strip().upper()
        if not key:
            return False
        rec = dict(params)
        rec.update({
            "model": key,
            "human_confirmed": True,
            "operator": operator,
            "promoted_at": datetime.now().isoformat(timespec="seconds"),
        })
        self._append_jsonl(self.foundation_file, rec)
        return True

    # ------------------------------------------------------------------
    # 2. 塔型补充库（tower_param_custom.jsonl）
    def load_tower_params(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for rec in self._read_jsonl(self.tower_file):
            tt = str(rec.get("tower_type") or "").strip().upper()
            if tt and rec.get("human_confirmed") is True:
                out[tt] = rec
        return out

    def promote_tower_param(self, tower_type: str, params: dict[str, Any], *, operator: str = "engineer", human_confirmed: bool = False) -> bool:
        if not human_confirmed:
            raise PermissionError("严禁将未经 HITL 人工确认的塔型参数写入企业长期记忆")
        key = str(tower_type or "").strip().upper()
        if not key:
            return False
        rec = dict(params)
        rec.update({
            "tower_type": key,
            "human_confirmed": True,
            "operator": operator,
            "promoted_at": datetime.now().isoformat(timespec="seconds"),
        })
        self._append_jsonl(self.tower_file, rec)
        return True

    # ------------------------------------------------------------------
    # 3. 跨设计院版式指纹库（institute_layout_profiles.jsonl）
    def lookup_layout_profile(self, header_fingerprint: str, target_sheet: str = "") -> Optional[LayoutProfile]:
        fp = str(header_fingerprint or "").strip()
        if not fp:
            return None
        matched = None
        for rec in self._read_jsonl(self.layout_file):
            if rec.get("human_confirmed") is not True:
                continue
            if rec.get("header_fingerprint") == fp and (not target_sheet or rec.get("target_sheet") == target_sheet):
                matched = rec
        if not matched:
            return None
        payload = {k: v for k, v in matched.items() if k not in ("human_confirmed", "operator", "promoted_at")}
        payload["source"] = "enterprise_cache"
        payload["sanity_passed"] = True
        try:
            return LayoutProfile.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("EnterpriseMemory layout profile parse failed: %s", exc)
            return None

    def promote_layout_profile(self, profile: LayoutProfile, *, operator: str = "engineer", human_confirmed: bool = False) -> bool:
        if not human_confirmed:
            raise PermissionError("严禁将未经 HITL 人工确认的跨设计院版式模板写入企业长期记忆")
        rec = profile.model_dump()
        rec.update({
            "human_confirmed": True,
            "operator": operator,
            "promoted_at": datetime.now().isoformat(timespec="seconds"),
        })
        self._append_jsonl(self.layout_file, rec)
        return True
