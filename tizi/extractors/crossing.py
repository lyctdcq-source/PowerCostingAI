# -*- coding: utf-8 -*-
"""《敏感因素专章》重要交叉跨越提取器（修复 D-04 硬编码前缀 & D-08 同塔多跨越静默丢弃）。

识别第 10 节「重要交叉跨越」（及通用跨越小节）中的档号对（如 NA133-NA134、J18-J19），
按工程惯例归属到大号侧杆塔，支持同塔多条交叉跨越聚合计数与多源证据链留痕。
"""
import re

from .. import cache
from ..provenance import make_evidence, make_prov

# 放宽跨院杆塔编号前缀（支持 N[A-Z]、J/Z/T 等单双字母前缀），并排除电压等级/公路编号（kV/G/S）
_SPAN_RE = re.compile(
    r"(?<![A-Za-z0-9])(N[A-Z]|[A-HJ-RT-Z][A-Z]?)(\d{1,4})\s*[-～~—–]\s*(?:N[A-Z]|[A-HJ-RT-Z][A-Z]?)?(\d{1,4})(?![A-Za-z0-9])"
)
_SEC_HEAD_RE = re.compile(r"(?:^|\n)\s*(10\.\d+|十、)[^\n]{0,80}")

_OBSTACLE_PRIORITY = {
    "电气化铁路": 100,
    "一般铁路": 95,
    "高速公路(双向4车道以内)": 90,
    "高压电力线": 85,
    "河流(河宽50m以内)": 60,
    "一般公路(双向4车道以内)": 50,
    "低压线": 40,
    "房屋(10m以下)": 30,
}


def _classify_obstacle(heading: str, ctx: str):
    """严格对齐院标母版《隐藏表（勿删）》B13:B31 / C13:C19 / 杆塔表 R 列合法枚举。"""
    h = heading or ""
    s = ctx or ""
    if "下穿" in h or "下穿" in s or "穿越" in h:
        method = "穿越电力线"
    elif "封网" in s or "索道" in s:
        method = "无跨越架索道封网式跨越"
    elif "带电" in s or "高速" in h or "国道" in h or re.search(r"(500|330|220|110)\s*kV", h, re.IGNORECASE):
        method = "无跨越架索道封网式跨越"
    else:
        method = "脚手架普通跨越"

    for scope in (h, s):
        if not scope:
            continue
        if "高速" in scope:
            return "高速公路(双向4车道以内)", "", "", method
        if "国道" in scope or "省道" in scope or "公路" in scope or re.search(r"[GS]\d{2,4}", scope):
            return "一般公路(双向4车道以内)", "", "", method
        if "高铁" in scope or "电气化铁路" in scope:
            return "电气化铁路", "", "", method
        if "铁路" in scope:
            return "一般铁路", "", "", method
        m_kv = re.search(r"(?:跨越|下穿|上跨)[^。\n]{0,18}?(500|330|220|110|35|10)\s*kV", scope, re.IGNORECASE)
        if not m_kv and scope == h:
            m_kv = re.search(r"(500|330|220|110|35|10)\s*kV", scope, re.IGNORECASE)
        if m_kv:
            kv = f"{m_kv.group(1)}kV"
            if "双回" in s:
                circ = "双回路"
            elif "单回" in s:
                circ = "单回路"
            elif kv == "500kV":
                circ = "双回路"
            else:
                circ = "单回路"
            obs = "高压电力线" if int(m_kv.group(1)) >= 10 else "低压线"
            return obs, kv, circ, method
        if "河" in scope or "江" in scope:
            return "河流(河宽50m以内)", "", "", method
        if "看护房" in scope or "房屋" in scope:
            return "房屋(10m以下)", "", "", "脚手架普通跨越"
    return "一般公路(双向4车道以内)", "", "", method


def parse_material_book_crossings(path, rel_path="", cache_dir=None):
    """补充提取《设备材料清册》编制说明中的常规跨越统计（10kV/35kV/看护房/公路等），供交叉校验与常规跨越清单展示。"""
    doc = cache.get_doc_features(path, cache_dir=cache_dir)
    items = []
    for pg in doc["pages"][:14]:
        txt = pg.get("text") or ""
        if not any(k in txt for k in ("跨越", "大棚", "看护房", "公路", "电力线")):
            continue
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        for idx, ln in enumerate(lines):
            m_cnt = re.search(r"(?:跨越)?\s*(\d+)\s*[次处]", ln)
            if not m_cnt:
                continue
            ctx = " ".join(lines[max(0, idx - 2): min(len(lines), idx + 1)])
            if any(k in ctx for k in ("停运", "拆除后不涉及")):
                continue
            if not any(k in ctx for k in ("kV", "KV", "公路", "高速", "铁路", "看护房", "房屋", "河", "鱼塘", "通讯", "光缆")):
                continue
            obs, volt, circ, meth = _classify_obstacle(ctx, ctx)
            items.append({
                "obstacle": obs,
                "voltage": volt,
                "circuit": circ,
                "method": meth,
                "count": int(m_cnt.group(1)),
                "page": pg["page"] + 1,
                "raw_text": ctx[:120],
                "rel": (rel_path or "").replace("\\", "/"),
            })
    return items


def _span_bbox(words, token_a: str, token_b: str):
    hits = [w for w in words if token_a in str(w[4]) or token_b in str(w[4])]
    if not hits:
        return None
    y0 = hits[0][1]
    near = [w for w in words if abs(w[1] - y0) <= 18.0]
    if not near:
        near = hits
    return [
        round(min(w[0] for w in near), 1),
        round(min(w[1] for w in near), 1),
        round(max(w[2] for w in near), 1),
        round(max(w[3] for w in near), 1),
    ]


def _obstacle_rank(rec: dict) -> tuple[int, int]:
    base = _OBSTACLE_PRIORITY.get(rec.get("obstacle") or "", 10)
    kv_num = 0
    m = re.search(r"\d+", str(rec.get("voltage") or ""))
    if m:
        kv_num = int(m.group())
    return (base, kv_num)


def parse_crossing_pdf(path, rel_path="", cache_dir=None, valid_prefixes=None):
    """解析《敏感因素专章》重要交叉跨越，返回 {tower_id: aggregated_crossing_record}。

    修复 D-08：同一塔位跨越多条不同障碍物时不再静默丢弃后续跨越，而是聚合至 `items[]` 并累加 `count`。
    """
    doc = cache.get_doc_features(path, cache_dir=cache_dir)
    results = {}
    in_cross_section = False
    cur_heading = ""
    allowed_set = {str(p).upper() for p in valid_prefixes} if valid_prefixes else None

    for pg in doc["pages"]:
        txt = pg.get("text") or ""
        if not txt:
            continue
        if "重要交叉跨越" in txt or "交叉跨越" in txt:
            in_cross_section = True
        if not in_cross_section:
            continue

        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        for idx, ln in enumerate(lines):
            if re.match(r"^(?:10\.\d+|十、)\s*", ln) or ("跨越" in ln and len(ln) <= 45):
                cur_heading = ln
            spans = list(_SPAN_RE.finditer(ln))
            if not spans:
                continue
            window = " ".join([cur_heading] + lines[max(0, idx - 1): min(len(lines), idx + 2)])
            if not any(k in window for k in ("跨", "下穿", "高速", "国道", "省道", "铁路", "kV", "KV")):
                continue
            obs, volt, circ, meth = _classify_obstacle(cur_heading, window)
            for m in spans:
                prefix, n1_s, n2_s = m.group(1).upper(), m.group(2), m.group(3)
                if prefix in ("KV", "GJ", "AN"):
                    continue
                if allowed_set and prefix not in allowed_set:
                    continue
                n1, n2 = int(n1_s), int(n2_s)
                if n1 == n2 or abs(n2 - n1) > 2:
                    continue
                big_n = max(n1, n2)
                tower_id = f"{prefix}{big_n}"
                span_str = f"{prefix}{n1}-{prefix}{n2}"
                bbox = _span_bbox(pg["words"], f"{prefix}{n1}", f"{prefix}{n2}")
                item = {
                    "tower": tower_id,
                    "span": span_str,
                    "obstacle": obs,
                    "voltage": volt,
                    "circuit": circ,
                    "method": meth,
                    "count": 1,
                    "page": pg["page"] + 1,
                    "bbox": bbox,
                    "raw_text": f"{cur_heading} | {ln}"[:150],
                    "rel": rel_path.replace("\\", "/"),
                }

                if tower_id not in results:
                    rec = dict(item)
                    rec["items"] = [item]
                    results[tower_id] = rec
                else:
                    existing = results[tower_id]
                    sig = (item["span"], item["obstacle"], item["voltage"], item["method"])
                    seen_sigs = {
                        (x["span"], x["obstacle"], x["voltage"], x["method"])
                        for x in existing.get("items") or []
                    }
                    if sig in seen_sigs:
                        continue
                    existing.setdefault("items", []).append(item)
                    existing["count"] = len(existing["items"])
                    # 保留风险等级更高的主障碍物到主列，所有障碍物详情均保留在 items[] 与 trace 中
                    if _obstacle_rank(item) > _obstacle_rank(existing):
                        existing["obstacle"] = item["obstacle"]
                        existing["voltage"] = item["voltage"]
                        existing["circuit"] = item["circuit"]
                        existing["method"] = item["method"]
                        existing["page"] = item["page"]
                        existing["bbox"] = item["bbox"]
                    existing["raw_text"] = " ; ".join(
                        f"{x['span']}({x['obstacle']}{x['voltage']})" for x in existing["items"]
                    )[:180]
    return results


def crossing_to_cells(rec: dict):
    """把单条或同塔多条聚合跨越记录转为模板 C15–C19 五列字典（值 + ProvenanceDAG）。"""
    items = rec.get("items") or [rec]
    evidences = [
        make_evidence(
            f"敏感因素专章跨越档#{i}",
            it.get("rel") or rec.get("rel") or "",
            page=it.get("page") or rec.get("page"),
            bbox=it.get("bbox") or rec.get("bbox"),
            raw_text=it.get("raw_text") or it.get("span") or "",
        )
        for i, it in enumerate(items, 1)
    ]
    if len(items) > 1:
        detail_desc = "；".join(
            f"{it['span']} {it['obstacle']}{it.get('voltage') or ''}（{it['method']}）" for it in items
        )
        trace = f"同塔聚合 {len(items)} 处交叉跨越（主列取最高风险项）：{detail_desc}"
        note = f"来源：敏感因素专章（同塔 {len(items)} 处跨越聚合）"
    else:
        trace = f"档距 {rec['span']} 归属大号塔 {rec['tower']}：{rec['obstacle']}（{rec['method']}）"
        note = f"来源：敏感因素专章（{rec['span']}）"
    pairs = [
        ("交叉跨越.障碍物种类", rec["obstacle"]),
        ("交叉跨越.电压等级", rec["voltage"] or None),
        ("交叉跨越.回路类型", rec["circuit"] or None),
        ("交叉跨越.跨越方式", rec["method"]),
        ("交叉跨越.跨越数量", rec["count"]),
    ]
    out = {}
    for col, val in pairs:
        out[col] = (
            val,
            make_prov(
                "cross_doc_extract", conf=0.95 if val is not None else 1.0,
                status="confirmed", note=note,
                machine_value=val, formula_trace=trace, evidences=evidences,
            ),
        )
    return out
