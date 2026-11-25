# -*- coding: utf-8 -*-
"""N4: constraint_validator — 运行 CHK-01~08 跨表守恒与实体级冲突报告，驱动收敛或视觉重访回边。"""
from __future__ import annotations

import time
from typing import Any

from ..harness.runtime import safe_stream_emit
from ..schemas.state import GraphState
from . import NodeContext


def make_n4_validate(ctx: NodeContext):
    def n4_constraint_validator(state: GraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        round_idx = int(state.get("revisit_round") or 0)
        safe_stream_emit({"kind": "node_start", "node": "n4_validate", "title": f"约束校核（第 {round_idx} 轮）"})
        ctx.emit("node_start", node="n4_validate", title=f"约束校核（第 {round_idx} 轮）")

        store = ctx.ensure_store()
        checks = []
        if ctx.pipeline is not None:
            if round_idx == 0:
                ctx.pipeline.step5_validate()
            else:
                ctx.pipeline.recompute_workbook_checks()
            checks = list(ctx.pipeline.workbook_checks or [])

        catalog = ctx.workspace.read_json(state.get("catalog_uri") or "01_catalog/catalog.json", default={}) or {}
        vis_idx = ctx.workspace.read_json(state.get("visual_index_uri") or "01_catalog/visual_index.json", default={}) or {}

        res = ctx.registry.invoke(
            "constraint_validator",
            workspace=ctx.workspace,
            tracer=ctx.tracer,
            store=store,
            checks=checks,
            round_index=round_idx,
            visual_index=vis_idx,
            detail_docs=catalog.get("detail_docs") or [],
        )
        data = res.data or {}
        is_converged = bool(data.get("is_converged", True))
        report_uri = data.get("report_uri") or f"06_constraints/chk_round_{round_idx}.json"

        elapsed = int((time.perf_counter() - t0) * 1000)
        if ctx.tracer:
            ctx.tracer.log_node(
                "n4_validate", "done", elapsed_ms=elapsed,
                round_index=round_idx,
                p0_count=data.get("p0_count", 0),
                revisitable=data.get("revisitable_count", 0),
                is_converged=is_converged,
            )
        safe_stream_emit({"kind": "node_done", "node": "n4_validate", "elapsed_ms": elapsed, "summary": res.summary})
        ctx.emit("node_done", node="n4_validate", elapsed_ms=elapsed, summary=res.summary)

        return {
            "current_node": "n4_validate",
            "chk_report_uri": report_uri,
            "is_converged": is_converged,
            "metrics": {
                f"n4_r{round_idx}_ms": elapsed,
                "p0_count": data.get("p0_count", 0),
                "revisitable_count": data.get("revisitable_count", 0),
            },
        }

    return n4_constraint_validator
