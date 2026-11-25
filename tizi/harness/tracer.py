# -*- coding: utf-8 -*-
"""L5 评估与观测层：结构化 TraceWriter（10_traces/trace.jsonl）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .workspace import TiziWorkspace


class TraceWriter:
    def __init__(self, workspace: TiziWorkspace, run_id: str = ""):
        self.workspace = workspace
        self.run_id = run_id
        self.rel_uri = "10_traces/trace.jsonl"

    def emit(self, event_type: str, **fields: Any) -> None:
        rec = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "type": event_type,
        }
        rec.update(fields)
        self.workspace.append_jsonl(self.rel_uri, rec)

    def log_node(self, node: str, phase: str, elapsed_ms: int = 0, **extra: Any) -> None:
        self.emit("node", node=node, phase=phase, elapsed_ms=elapsed_ms, **extra)

    def log_tool(self, tool: str, side_effect: str, ok: bool, code: str, elapsed_ms: int, **extra: Any) -> None:
        self.emit("tool", tool=tool, side_effect=side_effect, ok=ok, code=code, elapsed_ms=elapsed_ms, **extra)

    def log_vlm(self, entity_id: str, prompt_version: str, crop_uri: str, usage: dict[str, int], **extra: Any) -> None:
        self.emit(
            "vlm",
            entity_id=entity_id,
            prompt_version=prompt_version,
            crop_uri=crop_uri,
            prompt_tokens=int((usage or {}).get("prompt_tokens") or 0),
            completion_tokens=int((usage or {}).get("completion_tokens") or 0),
            total_tokens=int((usage or {}).get("total_tokens") or 0),
            **extra,
        )

    def summarize(self) -> dict[str, Any]:
        rows = self.workspace.read_jsonl(self.rel_uri)
        vlm_calls = [r for r in rows if r.get("type") == "vlm"]
        tool_calls = [r for r in rows if r.get("type") == "tool"]
        return {
            "total_events": len(rows),
            "tool_calls": len(tool_calls),
            "vlm_calls": len(vlm_calls),
            "total_tokens": sum(int(r.get("total_tokens") or 0) for r in vlm_calls),
        }
