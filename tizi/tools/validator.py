# -*- coding: utf-8 -*-
"""Tool: constraint_validator — 实体级硬校验 + CHK-01~08 跨表守恒 -> 结构化 ConflictReport。"""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from ..harness.registry import register_tool
from ..harness.store import ProjectTiziStore
from ..harness.workspace import TiziWorkspace
from ..schemas.base import ErrorCode, SideEffectLevel, ToolResult
from ..schemas.conflict import ConflictItem, ConflictReport


def build_conflict_report(
    store: ProjectTiziStore,
    checks: list[dict[str, Any]],
    *,
    round_index: int = 0,
    visual_index: Optional[dict[str, Any]] = None,
    detail_docs: Optional[list[dict[str, Any]]] = None,
) -> ConflictReport:
    """扫描全书 9 表与 CHK-01~08，生成带影响权重与可视觉重访标记的 ConflictReport。"""
    vis_entities = (visual_index or {}).get("entities") or {}
    fdn_doc_rels = [
        str(d.get("rel") or d.get("name") or "")
        for d in (detail_docs or [])
        if d and d.get("doc_role") == "foundation_detail"
    ]
    tower_doc_rels = [
        str(d.get("rel") or d.get("name") or "")
        for d in (detail_docs or [])
        if d and d.get("doc_role") == "tower_profile"
    ]
    cand_doc_rels = [str(d.get("rel") or d.get("name") or "") for d in (detail_docs or []) if d]

    conflicts: list[ConflictItem] = []
    revisitable_gaps: list[ConflictItem] = []

    # 1) 统计《基础明细表》各基础型号引用腿数，为 CHK-08 非标基础按影响权重排序
    leg_counts: Counter[str] = Counter()
    fdn_detail_rows = (store.get_sheet("基础明细表") or {}).get("rows") or []
    for r in fdn_detail_rows:
        out = r.get("out") or {}
        for lk in ("基础型号.A腿", "基础型号.B腿", "基础型号.C腿", "基础型号.D腿"):
            code = str(out.get(lk) or "").strip().upper()
            if code:
                leg_counts[code] += 1

    # 2) P0 冲突项扫描（基本信息运距冲突、量纲拦截冲突等）
    for sname in ("基本信息", "结构设备材料表", "杆塔明细表", "基础明细表", "基础库"):
        rows = (store.get_sheet(sname) or {}).get("rows") or []
        for r_idx, r in enumerate(rows):
            out = r.get("out") or {}
            provs = r.get("prov") or {}
            entity_id = str(
                out.get("杆塔号") or out.get("基础型号") or out.get("线路段名称") or out.get("材料名称") or f"R{r_idx + 1}"
            )
            for col_k, pv in provs.items():
                if not isinstance(pv, dict):
                    continue
                if pv.get("status") == "conflict":
                    item = ConflictItem(
                        conflict_id=f"P0-{sname}-{r_idx}-{col_k}",
                        chk_code="P0-CONFLICT",
                        severity="P0",
                        sheet=sname,
                        row_idx=r_idx,
                        entity_id=entity_id,
                        missing_fields=[col_k],
                        impact_score=100.0,
                        impact_weight=f"阻断级冲突：{sname}.{col_k}",
                        candidate_docs=cand_doc_rels[:3],
                        revisitable=False,
                        revisit_priority="HIGH",
                        reason=str(pv.get("note") or "多源数值或量纲冲突"),
                        current_value=out.get(col_k),
                    )
                    conflicts.append(item)

    # 3) CHK-08 基础库缺失项（BlueprintAgent 闭环 1：按引用腿数降序）
    fdn_lib_rows = (store.get_sheet("基础库") or {}).get("rows") or []
    chk08 = next((c for c in checks if c.get("code") == "CHK-08"), None)
    chk08_diff = chk08.get("diff") if chk08 else None
    for r_idx, r in enumerate(fdn_lib_rows):
        out = r.get("out") or {}
        model = str(out.get("基础型号") or "").strip().upper()
        if not model:
            continue
        missing = [
            col for col in ("基础.砼量(m³)", "基础钢筋量（kg）.HRB400", "基础钢筋量（kg）.HPB300")
            if out.get(col) in (None, "")
        ]
        if not missing:
            continue
        legs = leg_counts.get(model, 1)
        vis_hits = [x for x in (vis_entities.get(model) or []) if x.get("doc_role") in ("foundation_detail", "")]
        has_vis = bool(vis_hits)
        docs_for_model = [x["doc_rel"] for x in vis_hits] or fdn_doc_rels[:2]
        item = ConflictItem(
            conflict_id=f"GAP-CHK08-{model}",
            chk_code="CHK-08",
            severity="P1",
            sheet="基础库",
            row_idx=r_idx,
            entity_id=model,
            model_code=model,
            target_rows=[r_idx],
            missing_fields=missing,
            impact_score=float(legs * 10 + (20 if has_vis else 0)),
            impact_weight=f"{legs} 腿引用" + (f"，当前 CHK-08 差值 {chk08_diff} m³" if chk08_diff is not None else ""),
            candidate_docs=docs_for_model,
            revisitable=bool(docs_for_model),
            revisit_priority="HIGH" if legs >= 8 else "MEDIUM",
            reason=f"非标基础 {model} 缺标准库参数（{', '.join(missing)}）",
        )
        conflicts.append(item)
        if item.revisitable:
            revisitable_gaps.append(item)

    # 4) CHK-04 / 杆塔明细表缺重或缺失全高项（BlueprintAgent 闭环 2）
    tower_rows = (store.get_sheet("杆塔明细表") or {}).get("rows") or []
    for r_idx, r in enumerate(tower_rows):
        out = r.get("out") or {}
        tid = str(out.get("杆塔号") or "").strip().upper()
        ttype = str(out.get("杆塔型号") or "").strip().upper()
        missing_t = []
        if out.get("杆塔.全重(t)") in (None, ""):
            missing_t.append("杆塔.全重(t)")
        if out.get("杆塔.全高（m）") in (None, ""):
            missing_t.append("杆塔.全高（m）")
        if missing_t:
            vis_hits_t = [x for x in (vis_entities.get(ttype) or []) if x.get("doc_role") in ("tower_profile", "")]
            docs_for_tower = [x["doc_rel"] for x in vis_hits_t] or tower_doc_rels[:2]
            item = ConflictItem(
                conflict_id=f"GAP-CHK04-{tid}",
                chk_code="CHK-04",
                severity="P1",
                sheet="杆塔明细表",
                row_idx=r_idx,
                entity_id=tid or ttype,
                model_code=ttype,
                target_rows=[r_idx],
                missing_fields=missing_t,
                impact_score=85.0 + (15.0 if vis_hits_t else 0.0),
                impact_weight=f"杆塔 {tid} (塔型 {ttype}) 缺失 {'/'.join(missing_t)}",
                candidate_docs=docs_for_tower,
                revisitable=bool(docs_for_tower),
                revisit_priority="HIGH",
                reason=f"塔型 {ttype} 查表未命中 {', '.join(missing_t)}",
            )
            conflicts.append(item)
            if item.revisitable:
                revisitable_gaps.append(item)

    revisitable_gaps.sort(key=lambda x: (-x.impact_score, x.conflict_id))

    p0_cnt = sum(1 for c in conflicts if c.severity == "P0")
    p1_cnt = sum(1 for c in conflicts if c.severity == "P1")
    p2_cnt = sum(1 for c in conflicts if c.severity == "P2")
    chks_ok = sum(1 for c in checks if c.get("ok"))
    is_converged = (len(revisitable_gaps) == 0)

    return ConflictReport(
        round_index=int(round_index),
        chks=list(checks),
        conflicts=conflicts,
        revisitable_gaps=revisitable_gaps,
        p0_count=p0_cnt,
        p1_count=p1_cnt,
        p2_count=p2_cnt,
        is_converged=is_converged,
        summary=(
            f"第 {round_index} 轮校验：CHK 通过 {chks_ok}/{len(checks)}，"
            f"P0 冲突 {p0_cnt} 项，可视觉重访缺口 {len(revisitable_gaps)} 项"
        ),
    )


@register_tool(
    "constraint_validator",
    SideEffectLevel.ARTIFACT_WRITE,
    artifact_subdir="06_constraints",
    description="执行实体级硬校验与 CHK-01~08 跨表守恒，输出 ConflictReport 与可重访缺口队列",
)
def constraint_validator(
    *,
    workspace: Optional[TiziWorkspace] = None,
    store: ProjectTiziStore,
    checks: list[dict[str, Any]],
    round_index: int = 0,
    visual_index: Optional[dict[str, Any]] = None,
    detail_docs: Optional[list[dict[str, Any]]] = None,
) -> ToolResult[dict[str, Any]]:
    report = build_conflict_report(
        store,
        checks,
        round_index=round_index,
        visual_index=visual_index,
        detail_docs=detail_docs,
    )
    payload = report.model_dump()
    uri = None
    if workspace is not None:
        uri = workspace.write_json(f"06_constraints/chk_round_{int(round_index)}.json", payload)
        workspace.write_json("06_constraints/latest_conflicts.json", {
            "round_index": report.round_index,
            "report_uri": uri,
            "p0_count": report.p0_count,
            "p1_count": report.p1_count,
            "revisitable_count": len(report.revisitable_gaps),
            "is_converged": report.is_converged,
            "top_gaps": [g.model_dump() for g in report.revisitable_gaps[:12]],
        })

    return ToolResult(
        ok=True,
        code=ErrorCode.OK,
        data={
            "round_index": report.round_index,
            "p0_count": report.p0_count,
            "p1_count": report.p1_count,
            "revisitable_count": len(report.revisitable_gaps),
            "is_converged": report.is_converged,
            "report_uri": uri,
        },
        artifact_uri=uri,
        summary=report.summary,
    )
