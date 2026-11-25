# -*- coding: utf-8 -*-
"""N8: post_hitl_recheck — 应用人工审核决策、触发 DAG 增量重算并执行 P0 零残留导出断言。"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from ..extractors import foundation as foundation_ext
from ..harness.runtime import safe_stream_emit
from ..harness.store import evaluate_export_gate, save_store_to_workspace
from ..schemas.hitl import HumanDecision
from ..schemas.state import GraphState
from . import NodeContext


def make_n8_post_hitl(ctx: NodeContext):
    def n8_post_hitl_recheck(state: GraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        safe_stream_emit({"kind": "node_start", "node": "n8_post_hitl", "title": "审核后 DAG 重算与导出门禁复核"})
        ctx.emit("node_start", node="n8_post_hitl", title="审核后 DAG 重算与导出门禁复核")

        store = ctx.ensure_store()
        decisions = list(state.get("hitl_decisions") or [])
        touched_fdn = False

        for idx, raw_d in enumerate(decisions):
            if not isinstance(raw_d, dict):
                continue
            action = str(raw_d.get("action") or "confirm")
            if action == "approve_export":
                continue
            dec = HumanDecision(
                decision_id=str(raw_d.get("decision_id") or f"dec-{int(time.time() * 1000)}-{idx}"),
                operator=str(raw_d.get("operator") or "engineer"),
                ts=str(raw_d.get("ts") or datetime.now().isoformat(timespec="seconds")),
                sheet=str(raw_d.get("sheet") or ""),
                row_idx=int(raw_d.get("row_idx", 0)),
                col_key=str(raw_d.get("col_key") or ""),
                action=action,  # type: ignore[arg-type]
                old_val=raw_d.get("old_val"),
                new_val=raw_d.get("new_val"),
                note=str(raw_d.get("note") or ""),
                promote_to_memory=bool(raw_d.get("promote_to_memory", False)),
            )
            store.edit_cell(
                dec.sheet,
                dec.row_idx,
                dec.col_key,
                value=dec.new_val,
                action=dec.action if dec.action in ("confirm", "revert") else "edit",
            )
            ctx.workspace.append_jsonl("07_hitl/human_decisions.jsonl", dec.model_dump())
            if dec.sheet == "基础库":
                touched_fdn = True

        if touched_fdn:
            foundation_ext.recalc_foundation_links(store)
        if ctx.pipeline is not None:
            ctx.pipeline.recompute_workbook_checks()

        store_uri = save_store_to_workspace(store, ctx.workspace)
        all_decisions = ctx.workspace.read_jsonl("07_hitl/human_decisions.jsonl")
        allow_draft = bool(state.get("allow_draft_skip", False))
        gate = evaluate_export_gate(store, human_decisions=all_decisions, allow_draft_skip_unconfirmed=allow_draft)

        # 实时刷新 review_queue.json，确保前端与 API 看到最新 P0/P1 剩余数
        from .n7_hitl_gate import build_review_queue
        latest_q = build_review_queue(ctx)
        ctx.workspace.write_json("07_hitl/review_queue.json", {
            "session_id": state.get("session_id") or ctx.workspace.session_id,
            "p0_count": sum(1 for x in latest_q if x["priority"] == "P0"),
            "p1_count": sum(1 for x in latest_q if x["priority"] == "P1"),
            "p2_count": sum(1 for x in latest_q if x["priority"] == "P2"),
            "items": latest_q,
        })

        approved = bool(state.get("hitl_approved", False)) and gate.allowed
        elapsed = int((time.perf_counter() - t0) * 1000)
        if ctx.tracer:
            ctx.tracer.log_node(
                "n8_post_hitl", "done", elapsed_ms=elapsed,
                decisions_applied=len(decisions),
                gate_allowed=gate.allowed,
                p0_remaining=len(gate.p0_blocking),
                p1_remaining=len(gate.p1_unconfirmed),
            )
        summary = "门禁复核通过，准备双轨导出" if approved else f"门禁拦截：{gate.message}"
        safe_stream_emit({"kind": "node_done", "node": "n8_post_hitl", "elapsed_ms": elapsed, "summary": summary})
        ctx.emit("node_done", node="n8_post_hitl", elapsed_ms=elapsed, summary=summary)

        return {
            "current_node": "n8_post_hitl",
            "store_uri": store_uri,
            "hitl_approved": approved,
            "hitl_decisions": [],
            "metrics": {
                "n8_ms": elapsed,
                "p0_remaining": len(gate.p0_blocking),
                "p1_remaining": len(gate.p1_unconfirmed),
            },
        }

    return n8_post_hitl_recheck
