# -*- coding: utf-8 -*-
"""N5: revisit_planner — 按影响权重 Top-K + 预算护栏规划局部视觉重访任务清单。"""
from __future__ import annotations

import os
import time
from typing import Any

from ..harness.runtime import safe_stream_emit
from ..schemas.conflict import RevisitTask
from ..schemas.state import GraphState
from . import NodeContext


def make_n5_plan_revisit(ctx: NodeContext):
    def n5_revisit_planner(state: GraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        next_round = int(state.get("revisit_round") or 0) + 1
        crops_used = int(state.get("vlm_crops_used") or 0)
        max_crops = int(state.get("max_vlm_crops") or 24)
        budget_left = max(0, max_crops - crops_used)

        safe_stream_emit({"kind": "node_start", "node": "n5_plan_revisit", "title": f"规划第 {next_round} 轮定向视觉重访"})
        ctx.emit("node_start", node="n5_plan_revisit", title=f"规划第 {next_round} 轮定向视觉重访")

        if budget_left <= 0 or ctx.gateway is None or not ctx.gateway.configured:
            return {
                "current_node": "n5_plan_revisit",
                "revisit_round": next_round,
                "revisit_tasks": [],
                "is_converged": True,
            }

        report = ctx.workspace.read_json(state.get("chk_report_uri") or "06_constraints/chk_round_0.json", default={}) or {}
        vis_idx = ctx.workspace.read_json(state.get("visual_index_uri") or "01_catalog/visual_index.json", default={}) or {}
        catalog = ctx.workspace.read_json(state.get("catalog_uri") or "01_catalog/catalog.json", default={}) or {}
        folder = state.get("folder") or ctx.folder

        vis_entities = vis_idx.get("entities") or {}
        detail_docs = catalog.get("detail_docs") or []

        # 跨轮去重：已尝试过且不再需要外扩的 (lookup_key, doc_rel, page) 绝不重复裁图
        tried_pairs: set[tuple[str, str, int]] = set()
        tried_models: set[str] = set()
        for x in state.get("tried_crops") or []:
            lk = str(x.get("model_code") or x.get("entity_id") or "").upper()
            dr = str(x.get("doc_rel") or "")
            pg_i = int(x.get("page") or 0)
            if lk:
                tried_pairs.add((lk, dr, pg_i))
                if not x.get("need_expand_crop"):
                    tried_models.add(lk)

        # 按 model_code 合并同一轮内同型号多实体（如 NA23/NA38/NA43 同属 V3-2E1Y9-J3），避免重复 VLM 调用
        grouped_by_model: dict[str, dict[str, Any]] = {}
        ordered_keys: list[str] = []
        batch_cap = min(budget_left, 12)

        for gap in (report.get("revisitable_gaps") or []):
            entity_id = str(gap.get("entity_id") or "").upper()
            lookup_key = str(gap.get("model_code") or entity_id or "").upper()
            if not lookup_key:
                continue

            sheet = str(gap.get("sheet") or "")
            expected_role = "foundation_detail" if sheet == "基础库" else "tower_profile"
            raw_hits = vis_entities.get(lookup_key) or []
            role_hits = [h for h in raw_hits if h.get("doc_role") in (expected_role, "")]
            cand_role_docs = [d for d in detail_docs if d.get("doc_role") == expected_role]

            doc_rel = ""
            doc_path = ""
            page = 0
            bbox = None

            # 挑选尚未尝试过的候选页面（第2轮仅当存在第2个未试过页面时才重访）
            picked_hit = next(
                (h for h in role_hits if (lookup_key, str(h.get("doc_rel") or ""), int(h.get("page") or 0)) not in tried_pairs),
                None,
            )
            if picked_hit is not None:
                doc_rel = str(picked_hit.get("doc_rel") or "")
                doc_path = str(picked_hit.get("doc_path") or os.path.join(folder, doc_rel))
                page = int(picked_hit.get("page") or 0)
                bbox = picked_hit.get("bbox")
            elif cand_role_docs and lookup_key not in tried_models and not tried_pairs.intersection(
                {(lookup_key, str(d.get("rel") or d.get("name") or ""), 0) for d in cand_role_docs}
            ):
                d0 = cand_role_docs[0]
                doc_rel = str(d0.get("rel") or d0.get("name") or "")
                doc_path = str(d0.get("path") or os.path.join(folder, doc_rel))
                page = 0
                bbox = None

            if not doc_path or not os.path.isfile(doc_path):
                continue

            if lookup_key not in grouped_by_model:
                if len(ordered_keys) >= batch_cap:
                    continue
                schema_id = "foundation_detail" if sheet == "基础库" else "tower_profile"
                rows_list = list(gap.get("target_rows") or ([] if gap.get("row_idx") is None else [int(gap["row_idx"])]))
                task = RevisitTask(
                    task_id=f"rev{next_round}_{lookup_key}",
                    conflict_id=str(gap.get("conflict_id") or ""),
                    chk_code=str(gap.get("chk_code") or ""),
                    sheet=sheet,
                    row_idx=rows_list[0] if rows_list else None,
                    entity_id=entity_id or lookup_key,
                    model_code=lookup_key,
                    target_rows=rows_list,
                    target_schema_id=schema_id,
                    doc_rel=doc_rel,
                    page=page,
                    bbox=bbox,
                    missing_fields=list(gap.get("missing_fields") or []),
                    constraint_hints=str(gap.get("impact_weight") or gap.get("reason") or ""),
                    round_index=next_round,
                )
                td = task.model_dump()
                td["doc_path"] = doc_path
                grouped_by_model[lookup_key] = td
                ordered_keys.append(lookup_key)
            else:
                grp = grouped_by_model[lookup_key]
                r_idx = gap.get("row_idx")
                if r_idx is not None and int(r_idx) not in grp["target_rows"]:
                    grp["target_rows"].append(int(r_idx))
                for mf in (gap.get("missing_fields") or []):
                    if mf not in grp["missing_fields"]:
                        grp["missing_fields"].append(mf)

        planned = [grouped_by_model[k] for k in ordered_keys]

        elapsed = int((time.perf_counter() - t0) * 1000)
        summary = f"规划 {len(planned)} 项局部视觉重访（剩余裁图预算 {budget_left}）"
        if ctx.tracer:
            ctx.tracer.log_node("n5_plan_revisit", "done", elapsed_ms=elapsed, planned=len(planned), round_index=next_round)
        safe_stream_emit({"kind": "node_done", "node": "n5_plan_revisit", "elapsed_ms": elapsed, "summary": summary})
        ctx.emit("node_done", node="n5_plan_revisit", elapsed_ms=elapsed, summary=summary)

        return {
            "current_node": "n5_plan_revisit",
            "revisit_round": next_round,
            "revisit_tasks": planned,
        }

    return n5_revisit_planner
