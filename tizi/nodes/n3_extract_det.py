# -*- coding: utf-8 -*-
"""N3: deterministic_extract — T2 确定性快车道批量取数 + 9 表首轮填充 + 落盘 08_store/workbook_v2.json。"""
from __future__ import annotations

import time
from typing import Any

from ..harness.runtime import safe_stream_emit
from ..harness.store import save_store_to_workspace
from ..schemas.state import GraphState
from . import NodeContext


def make_n3_extract_det(ctx: NodeContext):
    def n3_deterministic_extract(state: GraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        safe_stream_emit({"kind": "node_start", "node": "n3_extract_det", "title": "本地确定性快车道 9 表首轮提取"})
        ctx.emit("node_start", node="n3_extract_det", title="本地确定性快车道 9 表首轮提取")

        # 读取 N2 版式学习产物并强制执行 §F.4.3 自检守门（未通过 5 行自检的 VLM 版式严禁放行至 N3）
        raw_profiles = ctx.workspace.read_json(
            state.get("layout_profiles_uri") or "01_catalog/layout_profiles.json",
            default={},
        ) or {}
        valid_profiles: dict[str, Any] = {}
        for k, lp in raw_profiles.items():
            if not isinstance(lp, dict):
                continue
            if lp.get("source") == "vlm_learned" and not lp.get("sanity_passed"):
                continue
            valid_profiles[k] = lp

        if ctx.pipeline is not None:
            ctx.pipeline.layout_profiles = valid_profiles
            ctx.pipeline.step2_parse()
            ctx.pipeline.step3_mapping()
            ctx.pipeline.step4_rules()
            ctx.store = ctx.pipeline.store

        store = ctx.ensure_store()
        store_uri = save_store_to_workspace(store, ctx.workspace)
        summaries = store.sheet_summaries()
        ready_cnt = sum(1 for s in summaries if s.get("ready"))

        elapsed = int((time.perf_counter() - t0) * 1000)
        if ctx.tracer:
            ctx.tracer.log_node("n3_extract_det", "done", elapsed_ms=elapsed, sheets_ready=ready_cnt)
        summary = f"确定性提取完成：{ready_cnt}/9 表就绪"
        safe_stream_emit({"kind": "node_done", "node": "n3_extract_det", "elapsed_ms": elapsed, "summary": summary})
        ctx.emit("node_done", node="n3_extract_det", elapsed_ms=elapsed, summary=summary)

        return {
            "current_node": "n3_extract_det",
            "store_uri": store_uri,
            "metrics": {"n3_ms": elapsed, "sheets_ready": ready_cnt},
        }

    return n3_deterministic_extract
