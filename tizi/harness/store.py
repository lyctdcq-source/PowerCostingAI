# -*- coding: utf-8 -*-
"""L4 权威状态仓库扩展（harness/store.py）：人工已确认禁覆盖守卫 + P0/P1 导出门禁 + 工作区落盘。"""
from __future__ import annotations

import copy
from typing import Any, Optional

from ..provenance import append_revision, make_prov
from ..schemas.hitl import ExportGateResult, ReviewQueueItem
from ..store import SHEET_ORDER, SHEET_SCHEMAS, ProjectTiziStore, cell_display_status
from .workspace import TiziWorkspace


def is_human_protected(prov: Optional[dict[str, Any]]) -> bool:
    """判断单元格是否已由人工确认/修订，后续自动重跑或 VLM 重访严禁静默覆盖。"""
    if not isinstance(prov, dict):
        return False
    if prov.get("method") in ("manual", "drawing_select"):
        return True
    revs = prov.get("revisions") or []
    for rev in reversed(revs):
        if rev.get("action") in ("confirm", "manual_edit", "drawing_select"):
            return True
    return False


def patch_store_cell(
    store: ProjectTiziStore,
    *,
    sheet_name: str,
    row_idx: int,
    col_key: str,
    value: Any,
    prov: Optional[dict[str, Any]] = None,
    actor: str = "deterministic",
    allow_overwrite_human: bool = False,
) -> dict[str, Any]:
    """全系统唯一受控写格入口：强制保护已人工确认单元格。"""
    sd = store.get_sheet(sheet_name)
    rows = sd["rows"]
    if not (0 <= row_idx < len(rows)):
        raise ValueError(f"行号超出范围：{sheet_name}[{row_idx}]")
    valid_cols = {c["key"] for c in SHEET_SCHEMAS[sheet_name]["columns"]}
    if col_key not in valid_cols:
        raise ValueError(f"字段不存在：{sheet_name}.{col_key}")

    row = rows[row_idx]
    out_map = row.setdefault("out", {})
    prov_map = row.setdefault("prov", {})
    cur_prov = prov_map.get(col_key)

    if actor != "human" and not allow_overwrite_human and is_human_protected(cur_prov):
        return {
            "applied": False,
            "skipped_reason": "human_confirmed_protected",
            "sheet": sheet_name,
            "row_idx": row_idx,
            "col_key": col_key,
            "value": out_map.get(col_key),
        }

    old_val = out_map.get(col_key)
    new_val = store._coerce(value)
    out_map[col_key] = new_val

    if prov is not None:
        new_prov = copy.deepcopy(prov)
    else:
        new_prov = copy.deepcopy(cur_prov) or make_prov(actor, machine_value=new_val)
    new_prov["actor"] = actor
    if actor == "vlm":
        new_prov = append_revision(
            new_prov,
            action="vlm_patch",
            old_val=old_val,
            new_val=new_val,
            note=new_prov.get("note") or "VLM 局部视觉重读补全",
        )
    prov_map[col_key] = new_prov
    return {
        "applied": True,
        "sheet": sheet_name,
        "row_idx": row_idx,
        "col_key": col_key,
        "old": old_val,
        "value": new_val,
        "status": new_prov.get("status", "confirmed"),
    }


def evaluate_export_gate(
    store: ProjectTiziStore,
    *,
    human_decisions: Optional[list[dict[str, Any]]] = None,
    allow_draft_skip_unconfirmed: bool = False,
) -> ExportGateResult:
    """N8/N9 导出前硬门禁：严禁未人工裁决的 P0 冲突或未确认 VLM 条目静默写入标准导入版。"""
    confirmed_keys: set[tuple[str, int, str]] = set()
    for d in human_decisions or []:
        if d.get("action") in ("confirm", "edit", "drawing_select"):
            confirmed_keys.add((str(d.get("sheet") or ""), int(d.get("row_idx", -1)), str(d.get("col_key") or "")))

    p0_blocking: list[ReviewQueueItem] = []
    p1_unconfirmed: list[ReviewQueueItem] = []

    for sname in SHEET_ORDER:
        sc = SHEET_SCHEMAS[sname]
        core_cols = [c["key"] for c in sc["columns"] if not c["is_default"]]
        rows = (store.get_sheet(sname) or {}).get("rows") or []
        for r_idx, r in enumerate(rows):
            out = r.get("out") or {}
            provs = r.get("prov") or {}
            entity_id = str(
                out.get("杆塔号") or out.get("基础型号") or out.get("线路段名称") or out.get("材料名称") or f"R{r_idx + 1}"
            )
            for col in core_cols:
                pv = provs.get(col) or {}
                st = str(pv.get("status") or "confirmed")
                if (sname, r_idx, col) in confirmed_keys or is_human_protected(pv):
                    continue
                evs = pv.get("evidences") or []
                crop_uri = next((e.get("crop_uri") for e in evs if isinstance(e, dict) and e.get("crop_uri")), None)
                ev_uri = next((e.get("evidence_uri") for e in evs if isinstance(e, dict) and e.get("evidence_uri")), None)
                if st == "conflict":
                    p0_blocking.append(
                        ReviewQueueItem(
                            item_id=f"P0-{sname}-{r_idx}-{col}",
                            priority="P0",
                            sheet=sname,
                            row_idx=r_idx,
                            col_key=col,
                            entity_id=entity_id,
                            current_value=out.get(col),
                            status=st,
                            calibrated_conf=float(pv.get("conf") or 0.5),
                            reason=str(pv.get("note") or "多源/量纲冲突，必须人工裁决"),
                            crop_uri=crop_uri,
                            evidence_uri=ev_uri,
                            conflict_candidates=list(pv.get("conflict_candidates") or []),
                        )
                    )
                elif st == "vlm_pending":
                    p1_unconfirmed.append(
                        ReviewQueueItem(
                            item_id=f"P1-{sname}-{r_idx}-{col}",
                            priority="P1",
                            sheet=sname,
                            row_idx=r_idx,
                            col_key=col,
                            entity_id=entity_id,
                            current_value=out.get(col),
                            status=st,
                            calibrated_conf=float(pv.get("conf") or 0.7),
                            reason=str(pv.get("note") or "VLM 视觉补全项未经交叉印证，需人工确认"),
                            crop_uri=crop_uri,
                            evidence_uri=ev_uri,
                        )
                    )

    if p0_blocking:
        return ExportGateResult(
            allowed=False,
            p0_blocking=p0_blocking,
            p1_unconfirmed=p1_unconfirmed,
            message=f"存在 {len(p0_blocking)} 项 P0 阻断冲突未人工裁决，禁止导出标准导入版",
        )
    if p1_unconfirmed and not allow_draft_skip_unconfirmed:
        return ExportGateResult(
            allowed=False,
            p0_blocking=[],
            p1_unconfirmed=p1_unconfirmed,
            message=f"存在 {len(p1_unconfirmed)} 项 VLM 视觉补全项未人工确认，请先在审核台确认或显式选择草稿跳过",
        )
    return ExportGateResult(
        allowed=True,
        p0_blocking=[],
        p1_unconfirmed=p1_unconfirmed,
        message="导出门禁校验通过",
    )


def save_store_to_workspace(store: ProjectTiziStore, workspace: TiziWorkspace) -> str:
    return workspace.write_json("08_store/workbook_v2.json", store.to_dict())


def load_store_from_workspace(workspace: TiziWorkspace) -> ProjectTiziStore:
    store = ProjectTiziStore()
    data = workspace.read_json("08_store/workbook_v2.json")
    if isinstance(data, dict):
        store.load_dict(data)
    return store


__all__ = [
    "SHEET_ORDER",
    "SHEET_SCHEMAS",
    "ProjectTiziStore",
    "cell_display_status",
    "is_human_protected",
    "patch_store_cell",
    "evaluate_export_gate",
    "save_store_to_workspace",
    "load_store_from_workspace",
]
