# -*- coding: utf-8 -*-
"""Harness 扩展 HTTP 路由分发（/api/tizi/review-queue, /checkpoints, /tools, /harness/run, /resume, /replay）。"""
from __future__ import annotations

from typing import Any

from ..tools import list_registered_tools


def dispatch_harness_get(job: Any, route: str, query: dict[str, list[str]]) -> tuple[bool, dict[str, Any]]:
    q1 = lambda key, default="": (query.get(key) or [default])[0]
    if route == "/api/tizi/review-queue":
        return True, job.get_review_queue()
    if route == "/api/tizi/checkpoints":
        limit = int(q1("limit", "20") or "20")
        return True, job.list_checkpoints(limit=limit)
    if route == "/api/tizi/tools":
        return True, {"ok": True, "tools": list_registered_tools()}
    return False, {}


def dispatch_harness_post(job: Any, route: str, body: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    if route == "/api/tizi/harness/run":
        res = job.run_harness_graph(
            max_revisits=int(body.get("max_revisits", 2)),
            max_vlm_crops=int(body.get("max_vlm_crops", 24)),
            max_vlm_schema_crops=int(body.get("max_vlm_schema_crops", 4)),
            hitl_required=bool(body.get("hitl_required", True)),
            async_run=not bool(body.get("sync", False)),
        )
        return True, res
    if route == "/api/tizi/resume":
        res = job.resume_hitl(
            decisions=body.get("decisions") or [],
            approve_export=bool(body.get("approve_export", True)),
            promote_memory=bool(body.get("promote_memory", False)),
            allow_draft_skip=bool(body.get("allow_draft_skip", False)),
        )
        return True, res
    if route == "/api/tizi/replay":
        res = job.replay_checkpoint(str(body.get("checkpoint_id") or ""))
        return True, res
    return False, {}
