# -*- coding: utf-8 -*-
"""N7: hitl_review_gate — 生成三级待审队列（P0/P1/P2）并调用 LangGraph interrupt() 持久化挂起。"""
from __future__ import annotations

import time
from typing import Any

from ..harness.runtime import interrupt, safe_stream_emit
from ..harness.store import SHEET_ORDER, SHEET_SCHEMAS, evaluate_export_gate, is_human_protected
from ..schemas.hitl import ReviewQueueItem
from ..schemas.state import GraphState
from . import NodeContext


def build_review_queue(ctx: NodeContext) -> list[dict[str, Any]]:
    store = ctx.ensure_store()
    prior_decisions = ctx.workspace.read_jsonl("07_hitl/human_decisions.jsonl")
    gate = evaluate_export_gate(store, human_decisions=prior_decisions)

    items: list[dict[str, Any]] = [x.model_dump() for x in gate.p0_blocking]
    items.extend(x.model_dump() for x in gate.p1_unconfirmed)

    # 追加 P2 提示级缺源项（如仍缺参数的基础库条目或缺失来源的表）
    seen_ids = {it["item_id"] for it in items}
    for sname in SHEET_ORDER:
        sd = store.get_sheet(sname) or {}
        if sd.get("missing_source"):
            iid = f"P2-SRC-{sname}"
            if iid not in seen_ids:
                items.append(ReviewQueueItem(
                    item_id=iid,
                    priority="P2",
                    sheet=sname,
                    row_idx=None,
                    col_key="*",
                    entity_id=sname,
                    status="missing_source",
                    calibrated_conf=0.0,
                    reason=str(sd.get("missing_source")),
                ).model_dump())
                seen_ids.add(iid)

        sc = SHEET_SCHEMAS[sname]
        core_cols = [c["key"] for c in sc["columns"] if not c["is_default"]]
        for r_idx, r in enumerate((sd.get("rows") or [])[:250]):
            out = r.get("out") or {}
            provs = r.get("prov") or {}
            entity_id = str(
                out.get("杆塔号") or out.get("基础型号") or out.get("线路段名称") or f"R{r_idx + 1}"
            )
            for col in core_cols:
                pv = provs.get(col) or {}
                if is_human_protected(pv):
                    continue
                if pv.get("status") == "pending" and out.get(col) in (None, ""):
                    iid = f"P2-{sname}-{r_idx}-{col}"
                    if iid not in seen_ids and len(items) < 200:
                        items.append(ReviewQueueItem(
                            item_id=iid,
                            priority="P2",
                            sheet=sname,
                            row_idx=r_idx,
                            col_key=col,
                            entity_id=entity_id,
                            current_value=out.get(col),
                            status="pending",
                            calibrated_conf=float(pv.get("conf") or 0.0),
                            reason=str(pv.get("note") or "重访后仍缺源，需人工核补"),
                        ).model_dump())
                        seen_ids.add(iid)
    return items


def make_n7_hitl_gate(ctx: NodeContext):
    def n7_hitl_review_gate(state: GraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        safe_stream_emit({"kind": "node_start", "node": "n7_hitl_gate", "title": "人工复核闸门（HITL）"})
        ctx.emit("node_start", node="n7_hitl_gate", title="人工复核闸门（HITL）")

        queue = build_review_queue(ctx)
        p0_cnt = sum(1 for x in queue if x["priority"] == "P0")
        p1_cnt = sum(1 for x in queue if x["priority"] == "P1")
        p2_cnt = sum(1 for x in queue if x["priority"] == "P2")

        queue_payload = {
            "session_id": state.get("session_id") or ctx.workspace.session_id,
            "p0_count": p0_cnt,
            "p1_count": p1_cnt,
            "p2_count": p2_cnt,
            "items": queue,
        }
        queue_uri = ctx.workspace.write_json("07_hitl/review_queue.json", queue_payload)
        ctx.workspace.update_manifest(status="paused_hitl", review_queue_uri=queue_uri, p0_count=p0_cnt, p1_count=p1_cnt)

        # 若外部显式指定非中断全自动模式且无 P0/P1 阻断项，可直接放行
        need_pause = bool(state.get("hitl_required", True)) or p0_cnt > 0 or p1_cnt > 0
        if not need_pause:
            return {
                "current_node": "n7_hitl_gate",
                "review_queue_uri": queue_uri,
                "hitl_approved": True,
                "hitl_decisions": [],
                "status": "running",
            }

        intr_payload = {
            "node": "n7_hitl_gate",
            "review_queue_uri": queue_uri,
            "p0_count": p0_cnt,
            "p1_count": p1_cnt,
            "p2_count": p2_cnt,
            "message": f"待人工审核：P0 阻断 {p0_cnt} 项、P1 视觉补全 {p1_cnt} 项、P2 提示 {p2_cnt} 项",
        }
        safe_stream_emit({"kind": "hitl_interrupt", **intr_payload})
        ctx.emit("hitl_interrupt", **intr_payload)
        if ctx.tracer:
            ctx.tracer.log_node("n7_hitl_gate", "paused", elapsed_ms=int((time.perf_counter() - t0) * 1000), p0=p0_cnt, p1=p1_cnt)

        # LangGraph 原生中断点：状态自动落盘至 09_checkpoints/langgraph.sqlite，跨进程重启后由 Command(resume=...) 恢复
        resume_payload = interrupt(intr_payload)

        decisions: list[dict[str, Any]] = []
        approved = False
        promote_mem = False
        allow_draft_skip = False
        if isinstance(resume_payload, dict):
            decisions = list(resume_payload.get("decisions") or [])
            approved = bool(resume_payload.get("approve_export", True))
            promote_mem = bool(resume_payload.get("promote_memory", False))
            allow_draft_skip = bool(resume_payload.get("allow_draft_skip", False))
        elif isinstance(resume_payload, list):
            decisions = list(resume_payload)
            approved = True

        return {
            "current_node": "n7_hitl_gate",
            "review_queue_uri": queue_uri,
            "hitl_decisions": decisions,
            "hitl_approved": approved,
            "promote_memory": promote_mem,
            "allow_draft_skip": allow_draft_skip,
            "status": "running",
        }

    return n7_hitl_review_gate
