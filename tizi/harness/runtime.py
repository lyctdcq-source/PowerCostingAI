# -*- coding: utf-8 -*-
"""L3/L4 薄适配层（runtime.py）：隔离 LangGraph 与 SqliteSaver 依赖。

按方案 §A-补充：业务节点与工具严禁直接 import langgraph 私有 API，统一通过本模块获取运行时原语，
确保未来若有零依赖内网需求，可平滑替换为自建 TiziStateGraph 而不改动业务代码。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer as _lg_get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt


def open_sqlite_checkpointer(db_path: str | Path) -> tuple[SqliteSaver, sqlite3.Connection]:
    """创建可跨线程/跨重启复用的 SQLite Checkpointer。"""
    p = Path(db_path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver, conn


def safe_stream_emit(payload: dict[str, Any]) -> None:
    """在 LangGraph 节点内安全发射自定义流式事件（节点外直接单测调用时静默无害）。"""
    try:
        writer = _lg_get_stream_writer()
        if writer is not None:
            writer(payload)
    except Exception:  # noqa: BLE001
        pass


def make_thread_config(session_id: str, *, checkpoint_id: Optional[str] = None, recursion_limit: int = 32) -> dict[str, Any]:
    configurable: dict[str, Any] = {"thread_id": str(session_id)}
    if checkpoint_id:
        configurable["checkpoint_id"] = str(checkpoint_id)
    return {"configurable": configurable, "recursion_limit": int(recursion_limit)}


def extract_interrupt_payload(state_snapshot: Any) -> Optional[dict[str, Any]]:
    """从 LangGraph StateSnapshot 中提取挂起的 HITL interrupt 载荷。"""
    tasks = getattr(state_snapshot, "tasks", None) or ()
    for t in tasks:
        interrupts = getattr(t, "interrupts", None) or ()
        for intr in interrupts:
            val = getattr(intr, "value", intr)
            if isinstance(val, dict):
                return val
            return {"payload": val}
    return None


__all__ = [
    "START",
    "END",
    "StateGraph",
    "Send",
    "Command",
    "interrupt",
    "SqliteSaver",
    "open_sqlite_checkpointer",
    "safe_stream_emit",
    "make_thread_config",
    "extract_interrupt_payload",
]
