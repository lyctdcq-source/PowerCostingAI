# -*- coding: utf-8 -*-
"""《串型汇总表》（C01–C12）确定性提取器。

基于《电气设备材料表》与《光缆设备材料表》的解析结果，按线路段分组提取：
- 导线悬垂串（型号 / 类型 / 串数）
- 导线耐张串（型号 / 数量(组)）
- 导线跳线串（型号 / 类型 / 串数）
- 防振锤（型号 / 类型 / 数量(个)）

严格对齐院标母版《串型汇总表》C01–C12 列及《隐藏表（勿删）》标准串型字典，
并保留原始清册页码/坐标证据链与推导算式。
"""
import re
from collections import OrderedDict

from ..provenance import make_prov


def _classify_susp_type(name: str, spec: str) -> str:
    n = name or ""
    sp = (spec or "").upper()
    if "V型" in n or "2V-" in sp or "1V-" in sp:
        if "双" in n or "2S" in sp or "2E2" in sp or "2B2" in sp:
            return "V型双联串"
        return "V型单联串"
    if "双串" in n or "双联" in n or "2S" in sp or "2E2" in sp or "2B2" in sp:
        return "I型双联串"
    if "四联" in n:
        return "I型四联串"
    return "I型单联串"


def _classify_damper_kind(name: str, spec: str, is_optical: bool = False) -> str:
    """严格对齐母版《隐藏表（勿删）》A13:A15 合法枚举：['导线', '避雷线', '光缆']。"""
    if is_optical or "OPGW" in (name + spec).upper() or "光缆" in (name + spec):
        return "光缆"
    sp = (spec or "").upper()
    if "地线" in (name or "") or "避雷线" in (name or "") or re.match(r"^FR[YH]?-[12](?:\b|$)", sp):
        return "避雷线"
    return "导线"


def _is_ground_wire_string(name: str, spec: str) -> bool:
    s = f"{name or ''} {spec or ''}"
    return ("地线" in s or "OPGW" in s.upper() or re.match(r"^(BN|BX)-", (spec or "").upper()) is not None)


def build_strings_sheet(electrical_rows: list, optical_rows: list):
    """从电气与光缆材料明细按线路段聚合并排生成《串型汇总表》行集。"""
    # 按线路段分桶收集 4 类组件
    segs = OrderedDict()

    def _bucket(seg):
        seg = seg or "线路段1"
        return segs.setdefault(seg, {"susp": [], "tens": [], "jump": [], "damp": []})

    for r in electrical_rows or []:
        out = r.get("out") or {}
        prov = r.get("prov") or {}
        seg = out.get("线路段名称") or "线路段1"
        name = str(out.get("材料名称") or "").strip()
        spec = str(out.get("规格型号") or "").strip()
        qty = out.get("设计用量（不含损耗）")
        if qty in (None, "", 0):
            continue
        evs = (prov.get("材料名称") or {}).get("evidences") or []

        if "防振锤" in name or "防振鞭" in name:
            _bucket(seg)["damp"].append({
                "model": spec or name,
                "kind": _classify_damper_kind(name, spec, is_optical=False),
                "qty": qty,
                "evs": evs,
                "raw": f"{name} {spec} = {qty}",
            })
            continue

        if _is_ground_wire_string(name, spec):
            continue

        if "跳线串" in name or re.match(r"^[12](GST|T)-", spec.upper()):
            _bucket(seg)["jump"].append({
                "model": spec or name,
                "kind": "I型双联串" if ("双" in name) else "I型单联串",
                "qty": qty,
                "evs": evs,
                "raw": f"{name} {spec} = {qty}",
            })
        elif "耐张" in name and ("串" in name or str(out.get("单位") or "") == "串"):
            _bucket(seg)["tens"].append({
                "model": spec or name,
                "qty": qty,
                "evs": evs,
                "raw": f"{name} {spec} = {qty}",
            })
        elif ("悬垂" in name and ("串" in name or str(out.get("单位") or "") == "串")) or re.match(r"^[12][XV]-", spec.upper()):
            _bucket(seg)["susp"].append({
                "model": spec or name,
                "kind": _classify_susp_type(name, spec),
                "qty": qty,
                "evs": evs,
                "raw": f"{name} {spec} = {qty}",
            })

    # 补充光缆防振锤
    for r in optical_rows or []:
        out = r.get("out") or {}
        prov = r.get("prov") or {}
        seg = out.get("线路段名称") or "线路段1"
        name = str(out.get("材料名称") or "").strip()
        spec = str(out.get("规格型号") or "").strip()
        qty = out.get("设计用量（不含损耗）")
        if qty in (None, "", 0):
            continue
        if "防振锤" in name or "防振鞭" in name:
            evs = (prov.get("材料名称") or {}).get("evidences") or []
            _bucket(seg)["damp"].append({
                "model": f"{name}({spec[:24]})" if spec else name,
                "kind": "光缆",
                "qty": qty,
                "evs": evs,
                "raw": f"{name} {spec} = {qty}",
            })

    rows = []
    source_files = set()
    for seg, b in segs.items():
        n = max(len(b["susp"]), len(b["tens"]), len(b["jump"]), len(b["damp"]), 0)
        for i in range(n):
            su = b["susp"][i] if i < len(b["susp"]) else None
            te = b["tens"][i] if i < len(b["tens"]) else None
            ju = b["jump"][i] if i < len(b["jump"]) else None
            da = b["damp"][i] if i < len(b["damp"]) else None

            out_r = {
                "线路段名称": seg,
                "导线悬垂串.型号": su["model"] if su else None,
                "导线悬垂串.类型": su["kind"] if su else None,
                "导线悬垂串.串数": su["qty"] if su else None,
                "导线耐张串.型号": te["model"] if te else None,
                "导线耐张串.数量(组)": te["qty"] if te else None,
                "导线跳线串.型号": ju["model"] if ju else None,
                "导线跳线串.类型": ju["kind"] if ju else None,
                "导线跳线串.串数": ju["qty"] if ju else None,
                "防振锤.型号": da["model"] if da else None,
                "防振锤.类型": da["kind"] if da else None,
                "防振锤.数量（个）": da["qty"] if da else None,
                # 默认值参考列
                "悬垂线夹类型": "非预绞丝线夹",
                "悬垂安装方式": "预绞丝",
            }
            prov_r = {}
            all_evs = []
            for grp, keys in (
                (su, ("导线悬垂串.型号", "导线悬垂串.类型", "导线悬垂串.串数")),
                (te, ("导线耐张串.型号", "导线耐张串.数量(组)")),
                (ju, ("导线跳线串.型号", "导线跳线串.类型", "导线跳线串.串数")),
                (da, ("防振锤.型号", "防振锤.类型", "防振锤.数量（个）")),
            ):
                evs = grp["evs"] if grp else []
                for e in evs:
                    if e.get("file"):
                        source_files.add(e["file"])
                all_evs.extend(evs)
                for k in keys:
                    v = out_r[k]
                    prov_r[k] = make_prov(
                        "derive" if k.endswith("类型") else "extract",
                        conf=0.96 if v is not None else 0.0,
                        status="confirmed" if v is not None else "empty",
                        machine_value=v,
                        note="由材料清册聚合提取" if v is not None else "",
                        formula_trace=grp["raw"] if grp else "",
                        evidences=evs,
                    )
            prov_r["线路段名称"] = make_prov(
                "derive", conf=1.0, status="confirmed", machine_value=seg,
                note="按清册单项工程/线路段分组", evidences=all_evs[:1],
            )
            prov_r["悬垂线夹类型"] = make_prov("constant", conf=0.95, status="confirmed", machine_value="非预绞丝线夹", note="模板默认值")
            prov_r["悬垂安装方式"] = make_prov("constant", conf=0.95, status="confirmed", machine_value="预绞丝", note="模板默认值")

            rows.append({
                "row_id": f"S{len(rows) + 1}",
                "out": out_r,
                "prov": prov_r,
            })

    return {
        "rows": rows,
        "source_files": sorted(source_files),
        "meta": {"segments": len(segs), "rows": len(rows)},
    }
