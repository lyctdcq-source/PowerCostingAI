# -*- coding: utf-8 -*-
"""N9: dual_track_export — 院标 13-Sheet 双轨导出与经人工确认的企业长期记忆沉淀。"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from ..harness.runtime import safe_stream_emit
from ..harness.store import is_human_protected
from ..schemas.layout import LayoutProfile
from ..schemas.state import GraphState
from . import NodeContext


def _promote_confirmed_knowledge(ctx: NodeContext) -> dict[str, int]:
    """仅沉淀经 HITL 人工确认的条目至 DATA/tizi/enterprise_memory/，绝不污染长期记忆。"""
    if ctx.memory is None:
        return {"foundations": 0, "layouts": 0}
    store = ctx.ensure_store()
    promoted_fdn = 0
    promoted_lp = 0

    fdn_rows = (store.get_sheet("基础库") or {}).get("rows") or []
    for r in fdn_rows:
        out = r.get("out") or {}
        provs = r.get("prov") or {}
        model = str(out.get("基础型号") or "").strip().upper()
        conc = out.get("基础.砼量(m³)")
        pv_conc = provs.get("基础.砼量(m³)")
        if model and conc not in (None, "") and is_human_protected(pv_conc):
            ctx.memory.promote_foundation(
                model,
                {
                    "type": out.get("基础类型") or "掏挖基础",
                    "concrete_m3": conc,
                    "hrb400_kg": out.get("基础钢筋量（kg）.HRB400"),
                    "hpb300_kg": out.get("基础钢筋量（kg）.HPB300"),
                    "concrete_grade": out.get("混凝土标号") or "C25",
                },
                human_confirmed=True,
            )
            promoted_fdn += 1

    lp_map = ctx.workspace.read_json("01_catalog/layout_profiles.json", default={}) or {}
    for _, raw_lp in lp_map.items():
        if isinstance(raw_lp, dict) and raw_lp.get("source") == "vlm_learned" and raw_lp.get("sanity_passed"):
            try:
                lp = LayoutProfile.model_validate(raw_lp)
                ctx.memory.promote_layout_profile(lp, human_confirmed=True)
                promoted_lp += 1
            except Exception:  # noqa: BLE001
                continue

    return {"foundations": promoted_fdn, "layouts": promoted_lp}


def make_n9_export(ctx: NodeContext):
    def n9_dual_track_export(state: GraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        safe_stream_emit({"kind": "node_start", "node": "n9_export", "title": "生成院标双轨交付包"})
        ctx.emit("node_start", node="n9_export", title="生成院标双轨交付包")

        store = ctx.ensure_store()
        checks = list(ctx.pipeline.workbook_checks or []) if ctx.pipeline is not None else []
        all_decisions = ctx.workspace.read_jsonl("07_hitl/human_decisions.jsonl")

        exp_dir = ctx.workspace.resolve_safe("11_exports")
        res = ctx.registry.invoke(
            "tizi_excel_export",
            workspace=ctx.workspace,
            tracer=ctx.tracer,
            store=store,
            checks=checks,
            out_dir=exp_dir,
            human_decisions=all_decisions,
            enforce_gate=True,
            allow_draft_skip_unconfirmed=bool(state.get("allow_draft_skip", False)),
        )
        if not res.ok:
            return {
                "current_node": "n9_export",
                "status": "paused_hitl",
                "hitl_approved": False,
                "export_warnings": [res.summary],
            }

        data = res.data or {}
        names = list(data.get("names") or [])

        # 同步拷贝一份至兼容旧 UI 的 out/ 目录
        if ctx.out_dir:
            out_path = Path(ctx.out_dir)
            out_path.mkdir(parents=True, exist_ok=True)
            for src_key in ("standard", "audit"):
                sp = data.get(src_key)
                if sp and Path(sp).is_file():
                    shutil.copy2(sp, out_path / Path(sp).name)

        promoted = {"foundations": 0, "layouts": 0}
        if state.get("promote_memory"):
            promoted = _promote_confirmed_knowledge(ctx)

        elapsed = int((time.perf_counter() - t0) * 1000)
        ctx.workspace.update_manifest(status="ready", last_node="n9_export", exports=names, promoted_memory=promoted)
        if ctx.tracer:
            ctx.tracer.log_node("n9_export", "done", elapsed_ms=elapsed, exports=names, promoted=promoted)
        summary = f"双轨交付包已生成：{', '.join(names)}"
        safe_stream_emit({"kind": "node_done", "node": "n9_export", "elapsed_ms": elapsed, "summary": summary})
        ctx.emit("node_done", node="n9_export", elapsed_ms=elapsed, summary=summary)

        return {
            "current_node": "n9_export",
            "status": "ready",
            "export_files": names,
            "metrics": {"n9_ms": elapsed, "promoted_foundations": promoted["foundations"], "promoted_layouts": promoted["layouts"]},
        }

    return n9_dual_track_export
