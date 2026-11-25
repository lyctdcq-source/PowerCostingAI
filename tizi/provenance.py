# -*- coding: utf-8 -*-
"""全链路数据可追溯结构（ProvenanceDAG）与强类型量纲换算器（UnitConverter）。

兼顾：
1. 向下兼容旧版 prov 字段（method / source / page / bbox / conf / status / note / flag）；
2. 升级支持多源证据链（evidences[]）、中文推导算式（formula_trace）、显式量纲换算（unit）、
   不可篡改机器基线（machine_value）、冲突候选列表（conflict_candidates）与追加式修订栈（revisions[]）。
"""
import copy
import re
from datetime import datetime


_UNIT_FACTORS = {
    ("kg", "t"): (0.001, "× 0.001"),
    ("t", "kg"): (1000.0, "× 1000"),
    ("m", "km"): (0.001, "× 0.001"),
    ("km", "m"): (1000.0, "× 1000"),
    ("mm", "m"): (0.001, "× 0.001"),
    ("m", "mm"): (1000.0, "× 1000"),
    ("g", "kg"): (0.001, "× 0.001"),
    ("kg", "g"): (1000.0, "× 1000"),
    ("元", "万元"): (0.0001, "× 0.0001"),
    ("万元", "元"): (10000.0, "× 10000"),
}

_DMS_RE = re.compile(r"(\d+)\s*[°度]\s*(\d+)\s*[′分']\s*(\d+(?:\.\d+)?)\s*[″秒\"]?")


class UnitConverter:
    """强类型显式量纲换算，返回 (换算后值, 可读换算轨迹)。"""

    @staticmethod
    def norm_unit(u: str) -> str:
        s = (u or "").strip().lower()
        aliases = {
            "吨": "t", "千克": "kg", "公斤": "kg",
            "米": "m", "千米": "km", "公里": "km", "毫米": "mm",
        }
        return aliases.get(s, s)

    @classmethod
    def convert(cls, value, src_unit: str, dst_unit: str, decimals: int = 3):
        if value is None:
            return None, ""
        su = cls.norm_unit(src_unit)
        du = cls.norm_unit(dst_unit)
        v = float(value)
        if not su or not du or su == du:
            out = round(v, decimals)
            return out, f"{v:g} {du or su}".strip()
        pair = _UNIT_FACTORS.get((su, du))
        if not pair:
            raise ValueError(f"不支持的量纲换算：{src_unit} -> {dst_unit}")
        factor, expr = pair
        out = round(v * factor, decimals)
        return out, f"{v:g} {su} {expr} = {out:g} {du}"

    @classmethod
    def dms_to_decimal(cls, raw: str, decimals: int = 4):
        m = _DMS_RE.search(str(raw or ""))
        if not m:
            return None, ""
        d, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        val = round(d + mi / 60.0 + s / 3600.0, decimals)
        return val, f"{d}°{mi}′{s:g}″ → {val}°"


def make_evidence(role: str, file_rel: str, page=None, bbox=None, raw_text: str = ""):
    """构造单条标准证据节点（page 为 1-based，bbox 为 [x0, y0, x1, y1]）。"""
    clean_bbox = None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        clean_bbox = [round(float(x), 1) for x in bbox]
    return {
        "role": str(role or "原始出处"),
        "file": str(file_rel or "").replace("\\", "/"),
        "page": int(page) if page not in (None, "") else None,
        "bbox": clean_bbox,
        "raw_text": str(raw_text or "")[:160],
    }


def make_prov(
    method: str = "extract",
    *,
    source: str = "",
    page=None,
    bbox=None,
    conf: float = 0.99,
    status: str = "confirmed",
    note: str = "",
    flag=None,
    machine_value=None,
    unit: str = "",
    formula_trace: str = "",
    evidences=None,
    revisions=None,
    conflict_candidates=None,
):
    """构造兼顾旧版与 ProvenanceDAG 的完整溯源字典。"""
    evs = list(evidences) if evidences else []
    if not evs and source and page is not None:
        evs = [make_evidence("原始提取", source, page=page, bbox=bbox)]
    primary_ev = evs[0] if evs else None
    src_val = source or (primary_ev["file"] if primary_ev else "")
    page_val = page if page is not None else (primary_ev["page"] if primary_ev else None)
    bbox_val = bbox if bbox is not None else (primary_ev["bbox"] if primary_ev else None)
    if isinstance(bbox_val, (list, tuple)) and len(bbox_val) == 4:
        bbox_val = [round(float(x), 1) for x in bbox_val]

    prov = {
        "method": method,
        "source": str(src_val or ""),
        "page": int(page_val) if page_val not in (None, "") else None,
        "bbox": bbox_val,
        "conf": float(conf),
        "status": status,
        "note": str(note or ""),
        "flag": flag,
        "machine_value": machine_value,
        "unit": str(unit or ""),
        "formula_trace": str(formula_trace or ""),
        "evidences": evs,
        "revisions": list(revisions) if revisions else [],
    }
    if conflict_candidates:
        prov["conflict_candidates"] = list(conflict_candidates)
    return prov


def append_revision(prov: dict, *, action: str, old_val, new_val, note: str = "", evidence=None):
    """在溯源对象上追加一条不可变修订事件，保留 machine_value 与初始证据链。"""
    out = copy.deepcopy(prov) if prov else make_prov("manual")
    if "machine_value" not in out or out.get("machine_value") is None:
        out["machine_value"] = old_val
    revs = list(out.get("revisions") or [])
    revs.append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "old": old_val,
        "new": new_val,
        "note": note,
    })
    out["revisions"] = revs
    if evidence:
        evs = list(out.get("evidences") or [])
        evs.insert(0, evidence)
        out["evidences"] = evs
        out["source"] = evidence["file"]
        out["page"] = evidence["page"]
        out["bbox"] = evidence["bbox"]
    return out


def normalize_evidences(prov: dict, resolve_rel=None):
    """返回标准化后的多源证据列表，确保每条都有可定位的 PDF 相对路径。"""
    if not prov:
        return []
    out = []
    evs = prov.get("evidences") or []
    if evs:
        for ev in evs:
            f_raw = ev.get("file") or ""
            rel = resolve_rel(f_raw) if (resolve_rel and f_raw and not f_raw.lower().endswith(".pdf")) else f_raw.replace("\\", "/")
            out.append({
                "role": ev.get("role") or "原始出处",
                "file": rel,
                "page": ev.get("page"),
                "bbox": ev.get("bbox"),
                "raw_text": ev.get("raw_text") or "",
            })
        return out
    src = prov.get("source") or ""
    page = prov.get("page")
    bbox = prov.get("bbox")
    rel = resolve_rel(src) if resolve_rel else src.replace("\\", "/")
    if rel and page:
        out.append({
            "role": "原始出处",
            "file": rel,
            "page": page,
            "bbox": bbox,
            "raw_text": prov.get("note") or "",
        })
    return out
