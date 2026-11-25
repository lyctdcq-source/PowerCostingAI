# -*- coding: utf-8 -*-
"""N2: hybrid_route_and_schema_learn — 载体分级 + 混合路由 + 跨设计院表头一次性学习 + T3 视觉索引。"""
from __future__ import annotations

import os
import time
from typing import Any

from ..harness.runtime import safe_stream_emit
from ..schemas.state import GraphState
from ..tools.cv_layout import build_visual_index_for_docs
from . import NodeContext


def make_n2_route_schema(ctx: NodeContext):
    def n2_hybrid_route_and_schema_learn(state: GraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        folder = state.get("folder") or ctx.folder
        safe_stream_emit({"kind": "node_start", "node": "n2_route_schema", "title": "载体分级、跨院版式路由与视觉索引"})
        ctx.emit("node_start", node="n2_route_schema", title="载体分级、跨院版式路由与视觉索引")

        if ctx.pipeline is not None:
            ctx.pipeline.step1_carrier(folder)

        catalog = ctx.workspace.read_json(state.get("catalog_uri") or "01_catalog/catalog.json", default={}) or {}
        aux = catalog.get("aux_sources") or {}
        detail_docs = catalog.get("detail_docs") or []

        # 1) 对 T3 详图与候选基础/型式分册建立图框级视觉索引（01_catalog/visual_index.json）
        vis_idx = build_visual_index_for_docs(
            detail_docs,
            workspace=ctx.workspace,
            cache_dir=ctx.cache_dir,
        )

        # 2) 跨设计院表头指纹匹配 & 按需 VLM 一次性学习（严控 max_vlm_schema_crops 预算）
        layout_profiles: dict[str, Any] = {}
        schema_crops_used = int(state.get("vlm_schema_crops_used") or 0)
        max_schema_crops = int(state.get("max_vlm_schema_crops") or 4)

        schema_targets = []
        sources_list = ((catalog.get("sources_cfg") or {}).get("sources")) or []
        prim_src = next((s for s in sources_list if s.get("id") == "primary" or s.get("role") == "主数据源"), None)
        if prim_src and prim_src.get("path"):
            p_struct = prim_src["path"] if os.path.isabs(prim_src["path"]) else os.path.join(folder, prim_src["path"])
            if os.path.isfile(p_struct):
                schema_targets.append(("struct", "杆塔明细表", p_struct))
        if aux.get("rho") and os.path.isfile(aux["rho"].get("path", "")):
            schema_targets.append(("rho", "土壤电阻率", aux["rho"]["path"]))
        if aux.get("materials") and os.path.isfile(aux["materials"].get("path", "")):
            schema_targets.append(("materials", "设备材料表", aux["materials"]["path"]))

        for doc_key, target_sheet, pdf_path in schema_targets:
            if schema_crops_used >= max_schema_crops:
                break
            lr = ctx.registry.invoke(
                "vlm_learn_layout_schema",
                workspace=ctx.workspace,
                tracer=ctx.tracer,
                pdf_path=pdf_path,
                doc_id=doc_key,
                target_sheet=target_sheet,
                page=0,
                gateway=ctx.gateway,
                memory=ctx.memory,
                cache_dir=ctx.cache_dir,
            )
            if lr.ok and isinstance(lr.data, dict):
                layout_profiles[doc_key] = lr.data
                if lr.data.get("source") == "vlm_learned":
                    schema_crops_used += 1

        lp_uri = ctx.workspace.write_json("01_catalog/layout_profiles.json", layout_profiles)

        elapsed = int((time.perf_counter() - t0) * 1000)
        if ctx.tracer:
            ctx.tracer.log_node(
                "n2_route_schema", "done", elapsed_ms=elapsed,
                visual_entities=vis_idx.get("entity_count", 0),
                learned_profiles=len(layout_profiles),
            )
        summary = f"视觉索引命中 {vis_idx.get('entity_count', 0)} 个型号，版式映射 {len(layout_profiles)} 项"
        safe_stream_emit({"kind": "node_done", "node": "n2_route_schema", "elapsed_ms": elapsed, "summary": summary})
        ctx.emit("node_done", node="n2_route_schema", elapsed_ms=elapsed, summary=summary)

        return {
            "current_node": "n2_route_schema",
            "visual_index_uri": "01_catalog/visual_index.json",
            "layout_profiles_uri": lp_uri,
            "vlm_schema_crops_used": schema_crops_used,
            "metrics": {"n2_ms": elapsed, "visual_entities": vis_idx.get("entity_count", 0)},
        }

    return n2_hybrid_route_and_schema_learn
