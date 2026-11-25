# -*- coding: utf-8 -*-
"""SSE 协议桥接器：统一序列化 node_start / node_done / sheet_progress / vlm_patch_applied / hitl_interrupt / run_complete。"""
from __future__ import annotations

import json
from typing import Any


def format_sse_event(ev: dict[str, Any]) -> bytes:
    seq = int(ev.get("seq") or 0)
    kind = str(ev.get("kind") or "message")
    payload = json.dumps(ev, ensure_ascii=False)
    return f"id: {seq}\nevent: {kind}\ndata: {payload}\n\n".encode("utf-8")
