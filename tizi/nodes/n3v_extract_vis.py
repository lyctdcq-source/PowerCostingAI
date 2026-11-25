# -*- coding: utf-8 -*-
"""N3-V: visual_first_extract — 对 T0 扫描页或纯图片型核心表执行 Visual-First 首轮抽取。"""
from __future__ import annotations

import os
import time
from typing import Any

from ..harness.confidence import calibrate_confidence
from ..harness.runtime import safe_stream_emit
from ..harness.store import save_store_to_workspace
from ..prompts import load_prompt
from ..provenance import make_evidence, make_prov
from ..schemas.state import GraphState
from ..store import SHEET_SCHEMAS
from . import NodeContext


def _infer_scan_target_sheet(name: str, rel: str) -> str:
    txt = f"{name} {rel}"
    if any(k in txt for k in ("勘察", "岩土", "地质", "土质")):
        return "勘测信息一览表"
    if any(k in txt for k in ("基础库", "基础表", "基础明细")):
        return "基础库"
    if any(k in txt for k in ("杆塔", "塔型", "配置")):
        return "杆塔明细表"
    return "杆塔明细表"


def make_n3v_extract_vis(ctx: NodeContext):
    def n3v_visual_first_extract(state: GraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        if ctx.gateway is None or not ctx.gateway.configured:
            return {"current_node": "n3v_extract_vis", "metrics": {"n3v_ms": 0, "n3v_skipped": True}}

        catalog = ctx.workspace.read_json(state.get("catalog_uri") or "01_catalog/catalog.json", default={}) or {}
        folder = state.get("folder") or ctx.folder
        scan_docs = [
            f for f in (catalog.get("files") or [])
            if f.get("kind") == "scan" or str(f.get("carrier") or "").upper() == "T0"
        ]
        if not scan_docs:
            elapsed = int((time.perf_counter() - t0) * 1000)
            safe_stream_emit({"kind": "node_done", "node": "n3v_extract_vis", "elapsed_ms": elapsed, "summary": "无 T0 扫描件，视觉通道待命"})
            return {"current_node": "n3v_extract_vis", "metrics": {"n3v_ms": elapsed, "n3v_scan_docs": 0}}

        safe_stream_emit({"kind": "node_start", "node": "n3v_extract_vis", "title": f"T0 扫描件视觉首轮抽取（{len(scan_docs)} 份）"})
        ctx.emit("node_start", node="n3v_extract_vis", title=f"T0 扫描件视觉首轮抽取（{len(scan_docs)} 份）")

        store = ctx.ensure_store()
        crops_used = int(state.get("vlm_crops_used") or 0)
        max_crops = int(state.get("max_vlm_crops") or 24)
        budget_left = max(0, max_crops - crops_used)
        max_scan_crops = min(budget_left, 6)

        sys_prompt, user_tmpl = load_prompt("vlm_scan_table_v1")
        extracted_rows_cnt = 0
        crops_done = 0
        patched_sheets: set[str] = set()

        for sdoc in scan_docs:
            if crops_done >= max_scan_crops:
                break
            rel = str(sdoc.get("rel") or sdoc.get("name") or "")
            pdf_path = str(sdoc.get("path") or os.path.join(folder, rel))
            if not pdf_path or not os.path.isfile(pdf_path):
                continue
            target_sheet = _infer_scan_target_sheet(str(sdoc.get("name") or ""), rel)
            if target_sheet not in SHEET_SCHEMAS:
                continue
            cands = [c["key"] for c in SHEET_SCHEMAS[target_sheet]["columns"] if not c["is_default"]][:12]
            n_pages = min(int(sdoc.get("pages") or 1), 2)

            for p_idx in range(n_pages):
                if crops_done >= max_scan_crops:
                    break
                seg_res = ctx.registry.invoke(
                    "cv_layout_segment",
                    workspace=ctx.workspace,
                    pdf_path=pdf_path,
                    doc_id=target_sheet,
                    page=p_idx,
                    cache_dir=ctx.cache_dir,
                )
                regions = ((seg_res.data or {}).get("regions") or []) if seg_res.ok else []
                param_reg = next((r for r in regions if r.get("kind") == "param_table"), None) or (
                    next((r for r in regions if r.get("kind") == "diagram"), None)
                )
                crop_res = ctx.registry.invoke(
                    "pdf_crop_render",
                    workspace=ctx.workspace,
                    pdf_path=pdf_path,
                    doc_id=f"scan_{target_sheet}",
                    page=p_idx,
                    bbox=param_reg.get("bbox") if param_reg else None,
                    dpi=220,
                )
                if not crop_res.ok or not crop_res.data:
                    continue

                crops_done += 1
                crop_abs = crop_res.data["abs_path"]
                crop_uri = crop_res.data["png_uri"]
                user_prompt = user_tmpl.format(
                    doc_id=rel or os.path.basename(pdf_path),
                    page_no=p_idx + 1,
                    target_sheet=target_sheet,
                    candidate_fields=", ".join(cands),
                )
                try:
                    raw, usage = ctx.gateway.call_vision_json(
                        system_prompt=sys_prompt,
                        user_prompt=user_prompt,
                        image_paths=[crop_abs],
                        prompt_version="vlm_scan_table_v1",
                    )
                except Exception:  # noqa: BLE001
                    continue

                if ctx.tracer:
                    ctx.tracer.log_vlm(
                        entity_id=f"scan:{rel}:p{p_idx + 1}",
                        prompt_version="vlm_scan_table_v1",
                        crop_uri=crop_uri,
                        usage=usage,
                        proposals=len((raw or {}).get("rows") or []),
                        need_expand_crop=False,
                        expand_level=0,
                    )

                raw_rows = (raw or {}).get("rows") or []
                if not raw_rows:
                    continue

                valid_cols = {c["key"] for c in SHEET_SCHEMAS[target_sheet]["columns"]}
                cur_sheet = store.get_sheet(target_sheet) or {}
                existing_rows = list(cur_sheet.get("rows") or [])
                pk_col = SHEET_SCHEMAS[target_sheet]["columns"][0]["key"]
                key_to_idx = {
                    str((r.get("out") or {}).get(pk_col) or "").strip().upper(): idx
                    for idx, r in enumerate(existing_rows)
                    if (r.get("out") or {}).get(pk_col)
                }

                patches: list[dict[str, Any]] = []
                new_rows: list[dict[str, Any]] = []
                for rr in raw_rows:
                    if not isinstance(rr, dict):
                        continue
                    fields = rr.get("fields") or {}
                    ent_id = str(rr.get("entity_id") or fields.get(pk_col) or "").strip().upper()
                    if not ent_id and not fields:
                        continue
                    self_conf = float(rr.get("model_self_conf") or 0.7)
                    cal_conf, cal_status = calibrate_confidence(
                        source="vlm_visual_read", chk_corroborated=False, model_self_conf=self_conf,
                    )
                    ev = make_evidence(
                        "T0 扫描件视觉识别", rel,
                        page=p_idx + 1, bbox=crop_res.data.get("pdf_bbox"),
                        raw_text=str(rr.get("raw_quote") or ent_id)[:140],
                    )
                    ev["crop_uri"] = crop_uri

                    if ent_id and ent_id in key_to_idx:
                        r_idx = key_to_idx[ent_id]
                        for fk, fv in fields.items():
                            if fk in valid_cols and fv not in (None, ""):
                                pv = make_prov(
                                    "vlm_visual_read", conf=cal_conf, status=cal_status,
                                    machine_value=fv, note="T0 扫描件 Visual-First 提取，待 N7 确认",
                                    evidences=[ev],
                                )
                                patches.append({"sheet": target_sheet, "row_idx": r_idx, "col_key": fk, "value": fv, "prov": pv})
                                extracted_rows_cnt += 1
                    else:
                        out_rec: dict[str, Any] = {}
                        prov_rec: dict[str, Any] = {}
                        if pk_col in valid_cols and ent_id:
                            out_rec[pk_col] = ent_id
                            prov_rec[pk_col] = make_prov("vlm_visual_read", conf=cal_conf, status=cal_status, machine_value=ent_id, evidences=[ev])
                        for fk, fv in fields.items():
                            if fk in valid_cols and fv not in (None, ""):
                                out_rec[fk] = fv
                                prov_rec[fk] = make_prov(
                                    "vlm_visual_read", conf=cal_conf, status=cal_status,
                                    machine_value=fv, note="T0 扫描件 Visual-First 建行，待 N7 确认",
                                    evidences=[ev],
                                )
                        if out_rec:
                            new_rows.append({"row_id": ent_id or f"S{len(existing_rows) + len(new_rows) + 1}", "out": out_rec, "prov": prov_rec})
                            extracted_rows_cnt += 1

                if new_rows:
                    existing_rows.extend(new_rows)
                    store.set_sheet(target_sheet, existing_rows, source_files=[rel] if rel else [])
                    save_store_to_workspace(store, ctx.workspace)
                    patched_sheets.add(target_sheet)
                if patches:
                    ctx.registry.invoke(
                        "workbook_store_patch",
                        workspace=ctx.workspace, tracer=ctx.tracer,
                        store=store, patches=patches, actor="vlm",
                    )
                    patched_sheets.add(target_sheet)

        elapsed = int((time.perf_counter() - t0) * 1000)
        summary = f"扫描件视觉通道处理 {crops_done} 页，提取 {extracted_rows_cnt} 项（待 N7 复核）"
        safe_stream_emit({"kind": "node_done", "node": "n3v_extract_vis", "elapsed_ms": elapsed, "summary": summary})
        ctx.emit("node_done", node="n3v_extract_vis", elapsed_ms=elapsed, summary=summary)
        out_dict: dict[str, Any] = {
            "current_node": "n3v_extract_vis",
            "vlm_crops_used": crops_used + crops_done,
            "metrics": {"n3v_ms": elapsed, "n3v_scan_docs": len(scan_docs), "n3v_rows": extracted_rows_cnt, "n3v_sheets": sorted(patched_sheets)},
        }
        if patched_sheets:
            out_dict["store_uri"] = "08_store/workbook_v2.json"
        return out_dict

    return n3v_visual_first_extract
