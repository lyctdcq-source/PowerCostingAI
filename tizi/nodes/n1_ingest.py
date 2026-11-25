# -*- coding: utf-8 -*-
"""N1: catalog_ingest — 目录扫描、去重、分册定性与规则枚举卡预解析。"""
from __future__ import annotations

import time
from typing import Any

from ..harness.runtime import safe_stream_emit
from ..schemas.state import GraphState
from . import NodeContext


def make_n1_ingest(ctx: NodeContext):
    def n1_catalog_ingest(state: GraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        folder = state.get("folder") or ctx.folder
        safe_stream_emit({"kind": "node_start", "node": "n1_ingest", "title": "目录探查与去重"})
        ctx.emit("node_start", node="n1_ingest", title="目录探查与去重")

        res = ctx.registry.invoke(
            "pdf_probe_catalog",
            workspace=ctx.workspace,
            tracer=ctx.tracer,
            folder_path=folder,
        )
        if not res.ok:
            return {"status": "error", "current_node": "n1_ingest", "error": res.summary}

        data = res.data or {}
        catalog_uri = data.get("catalog_uri") or res.artifact_uri or "01_catalog/catalog.json"
        enum_card_uri = data.get("enum_card_uri") or "01_catalog/enum_anchor_card.json"

        # 同步旧版 Pipeline step0 状态（若挂载了 Pipeline），保障前端与历史单测兼容
        if ctx.pipeline is not None:
            ctx.pipeline.step0_discover(folder)

        elapsed = int((time.perf_counter() - t0) * 1000)
        ctx.workspace.update_manifest(folder=folder, project=data.get("project", ""), last_node="n1_ingest")
        if ctx.tracer:
            ctx.tracer.log_node("n1_ingest", "done", elapsed_ms=elapsed, n_files=data.get("n_files", 0))
        safe_stream_emit({"kind": "node_done", "node": "n1_ingest", "elapsed_ms": elapsed, "summary": res.summary})
        ctx.emit("node_done", node="n1_ingest", elapsed_ms=elapsed, summary=res.summary)

        return {
            "current_node": "n1_ingest",
            "catalog_uri": catalog_uri,
            "enum_card_uri": enum_card_uri,
            "metrics": {"n1_ms": elapsed, "n_files": data.get("n_files", 0)},
        }

    return n1_catalog_ingest
