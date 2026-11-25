# -*- coding: utf-8 -*-
"""阶段三：字段映射与领域转换器。"""
import re

_DMS = re.compile(r"(\d+)\s*[°度]\s*(\d+)\s*[′分']\s*(\d+(?:\.\d+)?)\s*[″秒\"]?")
_TENSION_RE = re.compile(r"(-J\d|-JG|-JD|GFJ|GJD)", re.IGNORECASE)


def segment(v):
    """NA1 -> A 段"""
    m = re.match(r"^N([A-F])", str(v or "").strip().upper())
    return f"{m.group(1)} 段" if m else ""


def angle(v):
    """左:9°7′16″ -> 9.1211（十进制度）"""
    m = _DMS.search(str(v or ""))
    if not m:
        return None
    d, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return round(d + mi / 60.0 + s / 3600.0, 4)


def angle_tension_only(angle_raw, tower_type=""):
    """仅耐张塔保留十进制度转角；直线塔按模板要求强制留空。"""
    tt = str(tower_type or "")
    if tt and "GFZ" not in tt.upper() and _TENSION_RE.search(tt):
        return angle(angle_raw)
    if not tt:
        return angle(angle_raw)
    return None


def angle_dir(v):
    s = str(v or "")
    for k in ("左", "右"):
        if k in s:
            return k
    return ""


def uneven_legs(*vals):
    """四腿长度是否全等；缺值的腿不参与比较。"""
    xs = []
    for v in vals:
        m = re.search(r"(-?\d+(?:\.\d+)?)", str(v or ""))
        if m:
            xs.append(round(float(m.group(1)), 2))
    if len(xs) < 2:
        return ""
    return "否" if max(xs) == min(xs) else "是"


def first_nonempty(*vals):
    for v in vals:
        if str(v or "").strip() not in ("", "-", "—"):
            return str(v).strip()
    return ""


TRANSFORMS = {
    "first_nonempty": first_nonempty,
    "segment": segment,
    "angle": angle,
    "angle_tension_only": angle_tension_only,
    "angle_dir": angle_dir,
    "uneven_legs": uneven_legs,
}


def apply(rows, mapping):
    """为每一行生成标准字段，并记录每个值的来源。"""
    for r in rows:
        out, prov = {}, {}
        for col in mapping["columns"]:
            tgt = col["target"]
            srcs = col["from"] if isinstance(col["from"], list) else [col["from"]]
            raw = [r["cells"].get(s, "") for s in srcs]

            method = "derive" if ("transform" in col or len(srcs) > 1) else "extract"
            conf = 0.99 if method == "extract" else 1.0

            try:
                if "transform" in col:
                    fn = TRANSFORMS[col["transform"]]
                    val = fn(*raw) if len(raw) > 1 else fn(raw[0])
                else:
                    val = raw[0]
                    if col.get("cast") == "float":
                        val = float(val) if str(val).strip() not in ("", "-", "—") else None
            except Exception:  # noqa: BLE001
                val = None
                conf = 0.0

            if val in ("", None):
                val = None

            out[tgt] = val
            prov[tgt] = {
                "method": method,
                "source": "struct",
                "page": r["page"],
                "bbox": r.get("cell_bbox", {}).get(srcs[0]) or r["bbox"],
                "conf": conf,
                "status": "confirmed" if conf > 0 else "pending",
                "note": col.get("note", "").strip(),
                "machine_value": val,
            }
        r["out"] = out
        r["prov"] = prov
    return rows
