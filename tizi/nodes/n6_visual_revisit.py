# -*- coding: utf-8 -*-
"""N6: targeted_visual_revisit — 并行 Fan-out 单实体局部视觉读数 + 确定性单位换算 + 渐进回写 Store。"""
from __future__ import annotations

import re
import time
from typing import Any

from ..harness.confidence import calibrate_confidence
from ..harness.runtime import safe_stream_emit
from ..provenance import UnitConverter, make_evidence, make_prov
from ..schemas.state import GraphState
from . import NodeContext

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

_TARGET_UNITS = {
    "基础.砼量(m³)": "m³",
    "基础钢筋量（kg）.HRB400": "kg",
    "基础钢筋量（kg）.HPB300": "kg",
    "杆塔.全重(t)": "t",
    "杆塔.全高（m）": "m",
}


_RANGE_SEP_RE = re.compile(r"[～~]|(?:\d\s*[-—]\s*\d)")


def _convert_proposal_value(field_name: str, raw_value: str, raw_unit: str | None) -> tuple[Any, str] | tuple[None, None]:
    """铁律 4：Model 只报原文值与原单位，换算 100% 由确定性 UnitConverter 执行；拒绝占位词与未收敛区间。"""
    s = str(raw_value or "").strip()
    if field_name in _TARGET_UNITS:
        if not s or any(k in s for k in ("未标注", "未知", "无", "缺")) or _RANGE_SEP_RE.search(s):
            return None, None
        m = _NUM_RE.search(s)
        if not m:
            return None, None
    else:
        m = _NUM_RE.search(s)
        if not m:
            return s, f"原文直取：{s}"
    num = float(m.group())
    dst_unit = _TARGET_UNITS.get(field_name, "")
    src_u = UnitConverter.norm_unit(raw_unit or "")
    dst_u = UnitConverter.norm_unit(dst_unit)
    if src_u and dst_u and src_u != dst_u:
        try:
            conv, trace = UnitConverter.convert(num, src_u, dst_u, decimals=3)
            return conv, trace
        except ValueError:
            pass
    rounded = int(num) if num == int(num) else round(num, 3)
    return rounded, f"{rounded:g} {dst_unit or raw_unit or ''}".strip()


def make_n6_visual_revisit(ctx: NodeContext):
    def n6_targeted_visual_revisit(task_state: dict[str, Any]) -> dict[str, Any]:
        """支持两种调用形态：LangGraph Send 单任务字典，或常规状态全批执行。"""
        t0 = time.perf_counter()
        task = task_state.get("_send_task") or task_state
        entity_id = str(task.get("entity_id") or "")
        model_code = str(task.get("model_code") or entity_id)
        sheet = str(task.get("sheet") or "")
        row_idx = task.get("row_idx")
        target_rows = [int(r) for r in (task.get("target_rows") or ([] if row_idx is None else [row_idx]))]
        doc_path = str(task.get("doc_path") or "")
        doc_rel = str(task.get("doc_rel") or "")
        page = int(task.get("page") or 0)
        bbox = task.get("bbox")
        round_idx = int(task.get("round_index") or 1)

        res = ctx.registry.invoke(
            "vlm_visual_read",
            workspace=ctx.workspace,
            tracer=ctx.tracer,
            entity_id=model_code,
            target_schema_id=str(task.get("target_schema_id") or "foundation_detail"),
            missing_fields=list(task.get("missing_fields") or []),
            constraint_hints=str(task.get("constraint_hints") or ""),
            pdf_path=doc_path,
            doc_id=model_code,
            page=page,
            bbox=bbox,
            round_index=round_idx,
            gateway=ctx.gateway,
        )

        tried_rec = {
            "entity_id": model_code,
            "model_code": model_code,
            "doc_rel": doc_rel,
            "page": page,
            "round_index": round_idx,
            "ok": res.ok,
            "need_expand_crop": bool((res.data or {}).get("need_expand_crop", False)),
            "evidence_uri": res.artifact_uri or "",
        }
        if not res.ok or not isinstance(res.data, dict):
            return {
                "tried_crops": [tried_rec],
                "revisit_results": [{"entity_id": model_code, "applied": 0, "error": res.summary}],
            }

        proposals = res.data.get("proposals") or []
        crop_uri = str(res.data.get("crop_uri") or "")
        ev_uri = str(res.data.get("evidence_uri") or res.artifact_uri or "")
        allowed_fields = set(task.get("missing_fields") or [])
        patches = []
        store = ctx.ensure_store() if target_rows and proposals else None
        sheet_rows = ((store.get_sheet(sheet) or {}).get("rows") or []) if store else []

        if target_rows and proposals:
            for p in proposals:
                col_key = str(p.get("field_name") or "")
                if allowed_fields and col_key not in allowed_fields:
                    continue
                val, unit_trace = _convert_proposal_value(col_key, str(p.get("raw_value") or ""), p.get("raw_unit"))
                if val is None:
                    continue
                cal_conf, cal_status = calibrate_confidence(
                    source="vlm_visual_read",
                    chk_corroborated=False,
                    model_self_conf=float(p.get("model_self_conf") or 0.7),
                )
                ev = make_evidence(
                    "VLM 局部高清重读",
                    doc_rel or crop_uri,
                    page=page + 1,
                    bbox=bbox,
                    raw_text=str(p.get("raw_quote") or "")[:140],
                )
                ev["crop_uri"] = crop_uri
                ev["evidence_uri"] = ev_uri
                prov = make_prov(
                    "vlm_visual_read",
                    conf=cal_conf,
                    status=cal_status,
                    unit=_TARGET_UNITS.get(col_key, ""),
                    machine_value=val,
                    note=f"VLM 局部重读补全（{p.get('reasoning') or '待人工复核'}）",
                    formula_trace=f"原文 '{p.get('raw_quote')}' -> 确定性换算 {unit_trace}",
                    evidences=[ev],
                )
                for r_i in target_rows:
                    if 0 <= r_i < len(sheet_rows):
                        cur_v = (sheet_rows[r_i].get("out") or {}).get(col_key)
                        if cur_v not in (None, ""):
                            continue
                    patches.append({
                        "sheet": sheet,
                        "row_idx": int(r_i),
                        "col_key": col_key,
                        "value": val,
                        "prov": prov,
                    })

        applied = 0
        if patches:
            store = store or ctx.ensure_store()
            patch_res = ctx.registry.invoke(
                "workbook_store_patch",
                workspace=ctx.workspace,
                tracer=ctx.tracer,
                store=store,
                patches=patches,
                actor="vlm",
            )
            applied = int((patch_res.data or {}).get("applied_count") or 0)
            if applied > 0:
                ev_payload = {
                    "entity_id": model_code,
                    "sheet": sheet,
                    "row_idx": target_rows[0] if target_rows else row_idx,
                    "target_rows": target_rows,
                    "applied": applied,
                    "crop_uri": crop_uri,
                    "evidence_uri": ev_uri,
                }
                safe_stream_emit({"kind": "vlm_patch_applied", **ev_payload})
                ctx.emit("vlm_patch_applied", **ev_payload)

        elapsed = int((time.perf_counter() - t0) * 1000)
        return {
            "tried_crops": [tried_rec],
            "revisit_results": [{
                "entity_id": model_code,
                "sheet": sheet,
                "applied": applied,
                "crop_uri": crop_uri,
                "evidence_uri": ev_uri,
                "elapsed_ms": elapsed,
            }],
        }

    return n6_targeted_visual_revisit
