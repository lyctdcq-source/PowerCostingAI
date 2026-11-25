# -*- coding: utf-8 -*-
"""Graph 节点运行时上下文（将不可序列化句柄与 GraphState 彻底分离，确保 SQLite Checkpoint 无损跨重启恢复）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..harness.gateway import VlmGateway
from ..harness.memory import EnterpriseMemory
from ..harness.registry import ToolRegistry, get_default_registry
from ..harness.store import ProjectTiziStore, load_store_from_workspace
from ..harness.tracer import TraceWriter
from ..harness.workspace import TiziWorkspace


@dataclass
class NodeContext:
    workspace: TiziWorkspace
    folder: str
    pipeline: Any = None
    store: Optional[ProjectTiziStore] = None
    gateway: Optional[VlmGateway] = None
    memory: Optional[EnterpriseMemory] = None
    tracer: Optional[TraceWriter] = None
    registry: ToolRegistry = field(default_factory=get_default_registry)
    cache_dir: Optional[str] = None
    out_dir: Optional[str] = None
    emit_cb: Optional[Callable[[str, dict[str, Any]], None]] = None

    def ensure_store(self) -> ProjectTiziStore:
        if self.store is None:
            if self.pipeline is not None and getattr(self.pipeline, "store", None) is not None:
                self.store = self.pipeline.store
            else:
                self.store = load_store_from_workspace(self.workspace)
        return self.store

    def emit(self, kind: str, **payload: Any) -> None:
        if self.emit_cb is not None:
            try:
                clean = {k: v for k, v in payload.items() if k != "kind"}
                self.emit_cb(kind, clean)
            except Exception:  # noqa: BLE001
                pass
