# -*- coding: utf-8 -*-
"""《杆塔明细表》V2.0 确定性构建器（6 项准确性修正 + 多源 Left Join + ProvenanceDAG）。

修正清单：
1. 严格对齐院标模板 杆塔明细表 C01–C19（待填列）+ C20–C26（默认值参考列）；
2. 剔除门型构架行（^AN\\d+ 或 GJ-）进 excluded_gantries 审计列表，基数严格闭环；
3. 线路转角：耐张塔转 4 位十进制度，直线塔按模板要求强制留空；
4. 呼称高 vs 全高：若塔型库提供 head_height 则相加；缺塔头高时暂填呼称高并强制标 pending 告警；
5. 四腿接腿高度 A~D 自动推导「是否高低腿」（是/否）与「基数=1」；
6. 以归一化杆塔号为键 Left Join《土壤电阻率报告》《防雷接地装置图》《敏感因素专章》，
   补齐接地装置 C11–C14（含 kg→t 显式换算与 3 源证据链）及交叉跨越 C15–C19。
"""
import csv
import os
import re

from ..provenance import UnitConverter, make_evidence, make_prov
from . import crossing as crossing_ext
from . import grounding as grounding_ext
from . import rho as rho_ext

_GANTRY_ID_RE = re.compile(r"^(AN\d+|GJ\d*)$", re.IGNORECASE)
_GANTRY_TYPE_RE = re.compile(r"(^GJ-|构架|门型)", re.IGNORECASE)
_SEG_CODE_RE = re.compile(r"^N([A-Z])\d+", re.IGNORECASE)
_STEEL_POLE_RE = re.compile(r"(GFJ|GFZ|GJD|钢管杆)", re.IGNORECASE)

DEFAULT_SEG_NAMES = {
    "A": "A回（220kV主干段）",
    "B": "B回（220kVπ接侧一）",
    "C": "C回（220kVπ接侧二）",
    "D": "D回（110kV双回段）",
    "E": "E回（110kVπ接侧一）",
    "F": "F回（110kVπ接侧二）",
}


def _load_head_heights(project_root):
    """尝试从 tower_kind.csv 读取可选 head_height 列（塔头高，m）。"""
    out = {}
    if not project_root:
        return out
    for rel in ("data/tower_kind.csv", "defaults/data/tower_kind.csv"):
        p = os.path.join(project_root, rel)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    tt = str(row.get("type") or "").strip().upper()
                    hh = row.get("head_height")
                    if tt and hh not in (None, ""):
                        try:
                            out[tt] = float(hh)
                        except ValueError:
                            pass
        except OSError:
            pass
    return out


def _is_gantry(tower_id: str, tower_type: str) -> bool:
    tid = str(tower_id or "").strip()
    tt = str(tower_type or "").strip()
    if _GANTRY_ID_RE.match(tid):
        return True
    if _GANTRY_TYPE_RE.search(tt):
        return True
    return False


def _extract_leg_heights(cells: dict):
    """从原始解析 cells 中找出四腿接腿高度 A~D。"""
    vals = []
    # 兼容自动探查列名（c6/c7/c8/c9 等）与具名列（leg_a..leg_d）
    named = [cells.get(k) for k in ("leg_a", "leg_b", "leg_c", "leg_d")]
    if any(str(v or "").strip() for v in named):
        raws = named
    else:
        raws = []
    for v in raws:
        m = re.search(r"(-?\d+(?:\.\d+)?)", str(v or ""))
        if m:
            vals.append(round(float(m.group(1)), 2))
    return vals


_LEG_VAL_RE = re.compile(r"^\d+(?:\.\d+)?\s*[-+]\s*\d+(?:\.\d+)?\s*m?$", re.IGNORECASE)


def _detect_leg_cols(layout: dict, rows: list):
    """数据驱动识别四腿接腿高度列（如 27-4.0m / 30+1.5m），免疫合并表头居中偏列 1 位缺陷。"""
    cols = (layout or {}).get("columns") or []
    named = [c["key"] for c in cols if (c.get("key") or "").startswith("leg_")]
    if len(named) == 4:
        return named
    if rows:
        sample = rows[: min(len(rows), 120)]
        scores = []
        for i, c in enumerate(cols):
            k = c.get("key") or ""
            hits = sum(1 for r in sample if _LEG_VAL_RE.match(str((r.get("cells") or {}).get(k) or "").strip()))
            scores.append((i, k, hits))
        best_win = None
        best_sum = 0
        for s in range(0, max(0, len(cols) - 3)):
            win = scores[s:s + 4]
            if all(w[2] >= 3 for w in win):
                tot = sum(w[2] for w in win)
                if tot > best_sum:
                    best_sum = tot
                    best_win = [w[1] for w in win]
        if best_win:
            return best_win
    for idx, c in enumerate(cols):
        lbl = re.sub(r"\s+", "", str(c.get("label") or c.get("name") or ""))
        if "接腿" in lbl:
            return [cols[j]["key"] for j in range(idx, min(idx + 4, len(cols)))]
    return []


def _find_leg_values(row: dict, leg_keys: list):
    cells = row.get("cells") or {}
    vals = []
    for k in leg_keys[:4]:
        s = str(cells.get(k) or "").strip()
        # 优先提取接腿偏移量（如 "27-4.0m" 中的 "-4.0"），否则取数值
        m_off = re.search(r"([-+]\d+(?:\.\d+)?)\s*m?$", s, re.IGNORECASE)
        if m_off:
            vals.append(round(float(m_off.group(1)), 2))
        else:
            m = re.search(r"(-?\d+(?:\.\d+)?)", s)
            if m:
                vals.append(round(float(m.group(1)), 2))
    return vals


def _to_float(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None


def build_tower_sheet_v2(
    *,
    rows: list,
    layout: dict,
    struct_rel: str,
    aux_sources: dict,
    project_root: str,
    cache_dir=None,
    seg_names=None,
    segment_registry=None,
    layout_profiles=None,
):
    """将结构册解析行与辅助资料源关联，产出院标《杆塔明细表》V2 行集 + 构架审计清单 + 校验汇总。"""
    seg_map = dict(DEFAULT_SEG_NAMES)
    if seg_names:
        seg_map.update(seg_names)

    head_heights = _load_head_heights(project_root)
    leg_keys = _detect_leg_cols(layout, rows)

    # 1) 按需解析辅助源（复用 PageFeatureCache，零重复开文件）
    rho_records = {}
    rho_rel = ""
    if aux_sources.get("rho") and os.path.isfile(aux_sources["rho"]["path"]):
        rho_rel = aux_sources["rho"]["rel"]
        rho_lp = (layout_profiles or {}).get("rho") if isinstance(layout_profiles, dict) else None
        rho_records = rho_ext.parse_rho_pdf(aux_sources["rho"]["path"], cache_dir=cache_dir, layout_profile=rho_lp)

    ground_lib = {}
    ground_rel = ""
    if aux_sources.get("grounding") and os.path.isfile(aux_sources["grounding"]["path"]):
        ground_rel = aux_sources["grounding"]["rel"]
        ground_lib = grounding_ext.parse_grounding_pdf(aux_sources["grounding"]["path"], cache_dir=cache_dir)

    crossings = {}
    cross_rel = ""
    if aux_sources.get("crossing") and os.path.isfile(aux_sources["crossing"]["path"]):
        cross_rel = aux_sources["crossing"]["rel"]
        crossings = crossing_ext.parse_crossing_pdf(
            aux_sources["crossing"]["path"], rel_path=cross_rel, cache_dir=cache_dir,
        )

    out_rows = []
    excluded_gantries = []
    stats = {
        "total_input": len(rows),
        "towers": 0,
        "excluded_gantries": 0,
        "grounding_matched": 0,
        "crossings_matched": 0,
        "angle_tower_weight_t": 0.0,
        "steel_pole_weight_t": 0.0,
        "grounding_weight_t": 0.0,
    }

    source_files = [f for f in (struct_rel, rho_rel, ground_rel, cross_rel) if f]

    for src_idx, r in enumerate(rows):
        out_old = r.get("out") or {}
        prov_old = r.get("prov") or {}
        cells_raw = r.get("cells") or {}
        page1 = r.get("page")
        row_bbox = r.get("bbox")

        tid = str(out_old.get("杆塔号") or out_old.get("杆号") or cells_raw.get("tower_id") or "").strip().upper()
        ttype = str(out_old.get("杆塔型号") or cells_raw.get("tower_type") or "").strip()

        if _is_gantry(tid, ttype):
            excluded_gantries.append({
                "source_row": src_idx,
                "tower_id": tid,
                "tower_type": ttype,
                "page": page1,
                "reason": "变电站门型构架（非输电线路杆塔，已剥离不计入塔基数）",
            })
            stats["excluded_gantries"] += 1
            continue

        # 提取主证据节点
        def _ev_for(field_name, role_name):
            opv = prov_old.get(field_name) or {}
            return make_evidence(
                role_name, struct_rel,
                page=opv.get("page") or page1,
                bbox=opv.get("bbox") or row_bbox,
                raw_text=f"{tid} | {ttype} | {out_old.get(field_name, '')}".strip(" |"),
            )

        m_seg = _SEG_CODE_RE.match(tid)
        seg_code = m_seg.group(1).upper() if m_seg else ""
        if segment_registry:
            seg_name = segment_registry.for_tower_prefix(seg_code)
        else:
            seg_name = seg_map.get(seg_code) or out_old.get("线路段") or (f"{seg_code} 段" if seg_code else "")

        ev_tid = _ev_for("杆塔号", "结构册塔位行")
        ev_tt = _ev_for("杆塔型号", "结构册塔型")

        # 线路转角：仅耐张塔填十进制度，直线塔强制留空
        ang_raw = out_old.get("线路转角") or cells_raw.get("angle") or ""
        is_ten = grounding_ext.is_tension_tower(ttype)
        ang_val = None
        ang_trace = ""
        ang_note = ""
        if is_ten:
            if isinstance(ang_raw, (int, float)):
                ang_val = round(float(ang_raw), 4)
                ang_trace = f"耐张塔转角 = {ang_val}°"
            else:
                ang_val, ang_trace = UnitConverter.dms_to_decimal(str(ang_raw), decimals=4)
            ang_note = "耐张塔度分秒转十进制度" if ang_val is not None else "耐张塔转角待核"
        else:
            ang_val = None
            ang_trace = f"塔型 {ttype} 判为直线塔，按模板要求 C05 留空"
            ang_note = "直线塔不填转角"

        # 呼称高 vs 全高（严禁静默拿呼高冒充全高）
        call_h = _to_float(out_old.get("呼称高"))
        hh_delta = head_heights.get(ttype.upper())
        ev_h = _ev_for("呼称高", "结构册呼称高")
        if call_h is not None and hh_delta is not None:
            full_h = round(call_h + hh_delta, 2)
            full_h_prov = make_prov(
                "derive", conf=0.95, status="confirmed", unit="m",
                machine_value=full_h,
                note=f"全高 = 呼称高({call_h:g}m) + 塔头高({hh_delta:g}m)",
                formula_trace=f"{call_h:g} m + {hh_delta:g} m = {full_h:g} m",
                evidences=[ev_h],
            )
        elif call_h is not None:
            full_h = call_h
            full_h_prov = make_prov(
                "extract", conf=0.75, status="pending", unit="m",
                machine_value=full_h,
                note="缺塔头高参数，当前暂为呼称高，请核对或补充塔头高",
                formula_trace=f"暂取呼称高 {call_h:g} m（缺 {ttype} 塔头高参数）",
                evidences=[ev_h],
            )
        else:
            full_h = None
            full_h_prov = make_prov(
                "extract", conf=0.0, status="pending", unit="m",
                machine_value=None, note="未提取到呼称高", evidences=[ev_h],
            )

        # 全重(t)
        wt_val = _to_float(out_old.get("全重"))
        wt_old_pv = prov_old.get("全重") or {}
        wt_prov = make_prov(
            wt_old_pv.get("method") or "interval",
            conf=wt_old_pv.get("conf", 0.9 if wt_val is not None else 0.0),
            status="confirmed" if wt_val is not None else "pending",
            unit="t",
            machine_value=wt_val,
            note=wt_old_pv.get("note") or ("按塔型重量表区间插值" if wt_val is not None else "查表未命中，待复核"),
            formula_trace=f"塔型 {ttype} 呼高 {call_h}m → 区间插值 {wt_val} t" if wt_val is not None else f"塔型 {ttype} 未命中重量区间",
            evidences=[ev_tt, ev_h],
        )

        # 是否高低腿（四腿接腿高度 A~D，数据驱动锁定精确 4 列）
        leg_vals = _find_leg_values(r, leg_keys)
        if not leg_vals:
            leg_vals = _extract_leg_heights(cells_raw)
        if len(leg_vals) >= 2:
            hl_val = "否" if max(leg_vals) == min(leg_vals) else "是"
            hl_trace = f"四腿接腿高 {leg_vals} → {'全等(否)' if hl_val == '否' else '不等(是)'}"
            hl_st = "confirmed"
        elif out_old.get("是否高低腿") in ("是", "否"):
            hl_val = out_old["是否高低腿"]
            hl_trace = f"来自映射派生：{hl_val}"
            hl_st = "confirmed"
        elif _STEEL_POLE_RE.search(ttype):
            hl_val = "否"
            hl_trace = "钢管杆无高低腿 → 否"
            hl_st = "confirmed"
        else:
            hl_val = None
            hl_trace = "未识别到四腿接腿列"
            hl_st = "pending"

        # 杆塔类型（C20 默认值列）
        t_kind = out_old.get("杆塔类型") or ("钢管杆" if _STEEL_POLE_RE.search(ttype) else "角钢塔")

        # 接地装置 C11–C14
        struct_ev_combo = make_evidence(
            "塔位与呼高", struct_rel, page=page1, bbox=row_bbox,
            raw_text=f"{tid} | {ttype} | 呼高 {call_h if call_h is not None else '-'}m",
        )
        g_res = None
        if rho_records and tid in rho_records:
            g_res = grounding_ext.derive_grounding_for_tower(
                tower_id=tid, seg_code=seg_code, ttype=ttype, call_height=call_h,
                rho_rec=rho_records[tid], ground_lib=ground_lib,
                struct_ev=struct_ev_combo, rho_rel=rho_rel, ground_rel=ground_rel,
            )

        # 交叉跨越 C15–C19
        c_res = crossing_ext.crossing_to_cells(crossings[tid]) if tid in crossings else None

        # 组装严格对齐母版 C01–C26 的行对象
        out = {
            "线路段名称": seg_name,
            "杆塔号": tid,
            "杆塔型号": ttype,
            "基数": 1,
            "线路转角度数（耐张），直线塔不填": ang_val,
            "杆塔.全高（m）": full_h,
            "杆塔.全重(t)": wt_val,
            "地脚螺栓（kg）": None,
            "基础正面根开（m）": None,
            "是否高低腿": hl_val,
            "接地装置.型号": None,
            "接地装置.长度（m）": None,
            "接地装置.埋深（m）": None,
            "接地装置.常规接地镀锌接地材料重量（t）": None,
            "交叉跨越.障碍物种类": None,
            "交叉跨越.电压等级": None,
            "交叉跨越.回路类型": None,
            "交叉跨越.跨越方式": None,
            "交叉跨越.跨越数量": None,
            # 默认值列 C20–C26
            "杆塔类型": t_kind,
            "避雷器类型": "氧化锌式",
            "线路类型": "导线",
            "均压环数量": 0,
            "屏蔽环数量": 0,
            "跳线类型": "软跳线" if is_ten else None,
            "每侧导线横担水平排列最大相数": "2相" if seg_code == "D" else "1相",
        }
        prov = {
            "线路段名称": make_prov(
                "derive", conf=1.0, status="confirmed", machine_value=seg_name,
                note="由杆塔号回路前缀映射", formula_trace=f"{tid} → {seg_name}",
                evidences=[ev_tid],
            ),
            "杆塔号": make_prov(
                "extract", conf=0.99, status="confirmed", machine_value=tid,
                note="结构册直取", evidences=[ev_tid],
            ),
            "杆塔型号": make_prov(
                "extract", conf=0.99, status="confirmed", machine_value=ttype,
                note="结构册直取", evidences=[ev_tt],
            ),
            "基数": make_prov(
                "constant", conf=1.0, status="confirmed", machine_value=1,
                note="逐基明细表每行基数固定为 1", evidences=[ev_tid],
            ),
            "线路转角度数（耐张），直线塔不填": make_prov(
                "derive", conf=0.99 if (not is_ten or ang_val is not None) else 0.5,
                status="confirmed" if (not is_ten or ang_val is not None) else "pending",
                unit="°", machine_value=ang_val, note=ang_note,
                formula_trace=ang_trace, evidences=[_ev_for("线路转角", "结构册线路转角")],
            ),
            "杆塔.全高（m）": full_h_prov,
            "杆塔.全重(t)": wt_prov,
            "地脚螺栓（kg）": make_prov(
                "extract", conf=0.0, status="empty", unit="kg",
                machine_value=None, note="结构明细表未列单基地脚螺栓重，总量见结构设备材料表",
            ),
            "基础正面根开（m）": make_prov(
                "extract", conf=0.0, status="empty", unit="m",
                machine_value=None, note="单基根开见各塔型结构图/根开表",
            ),
            "是否高低腿": make_prov(
                "derive", conf=0.99 if hl_val else 0.0, status=hl_st,
                machine_value=hl_val, note="由结构册接腿 A~D 自动比对",
                formula_trace=hl_trace, evidences=[ev_tid],
            ),
            "杆塔类型": make_prov(
                "lookup", conf=0.98, status="confirmed", machine_value=t_kind,
                note="按塔型分类表确定", evidences=[ev_tt],
            ),
            "避雷器类型": make_prov("constant", conf=0.95, status="confirmed", machine_value="氧化锌式", note="模板默认值"),
            "线路类型": make_prov("constant", conf=0.95, status="confirmed", machine_value="导线", note="模板默认值"),
            "均压环数量": make_prov("constant", conf=0.95, status="confirmed", machine_value=0, note="模板默认值"),
            "屏蔽环数量": make_prov("constant", conf=0.95, status="confirmed", machine_value=0, note="模板默认值"),
            "跳线类型": make_prov("derive", conf=0.95, status="confirmed", machine_value=out["跳线类型"], note="耐张塔默认软跳线"),
            "每侧导线横担水平排列最大相数": make_prov("derive", conf=0.95, status="confirmed", machine_value=out["每侧导线横担水平排列最大相数"], note="按单/双回默认推导"),
        }

        if g_res:
            stats["grounding_matched"] += 1
            for col_k, (val, pv) in g_res.items():
                out[col_k] = val
                prov[col_k] = pv
            if out["接地装置.常规接地镀锌接地材料重量（t）"] is not None:
                stats["grounding_weight_t"] += float(out["接地装置.常规接地镀锌接地材料重量（t）"])
        else:
            for col_k in ("接地装置.型号", "接地装置.长度（m）", "接地装置.埋深（m）", "接地装置.常规接地镀锌接地材料重量（t）"):
                prov[col_k] = make_prov(
                    "multi_join_lookup", conf=0.0,
                    status="pending" if rho_records else "empty",
                    machine_value=None,
                    note="未在土壤电阻率报告中匹配到该塔号" if rho_records else "当前工作空间缺土壤电阻率报告",
                )

        if c_res:
            stats["crossings_matched"] += 1
            for col_k, (val, pv) in c_res.items():
                out[col_k] = val
                prov[col_k] = pv

        if wt_val is not None:
            if t_kind == "钢管杆":
                stats["steel_pole_weight_t"] += wt_val
            else:
                stats["angle_tower_weight_t"] += wt_val

        out_rows.append({
            "row_id": tid or f"R{len(out_rows) + 1}",
            "page": page1,
            "bbox": row_bbox,
            "cells": cells_raw,
            "out": out,
            "prov": prov,
        })

    stats["towers"] = len(out_rows)
    stats["angle_tower_weight_t"] = round(stats["angle_tower_weight_t"], 2)
    stats["steel_pole_weight_t"] = round(stats["steel_pole_weight_t"], 2)
    stats["grounding_weight_t"] = round(stats["grounding_weight_t"], 3)

    return {
        "rows": out_rows,
        "excluded_gantries": excluded_gantries,
        "source_files": source_files,
        "stats": stats,
        "rho_records": rho_records,
        "ground_lib": ground_lib,
        "crossings": crossings,
    }
