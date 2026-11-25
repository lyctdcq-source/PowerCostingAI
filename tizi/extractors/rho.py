# -*- coding: utf-8 -*-
"""《土壤电阻率报告》T2 成果表提取器（优先 GridCellExtractor + LayoutProfile 动态列映射，消除 D-01 硬编码）。

输出：{tower_id: {"rho": float, "soil": str, "remark": str, "page": int(1-based), "bbox": [...], "raw_text": str}}
"""
from __future__ import annotations

import re

from .. import cache
from ..grid_parser import GridCellExtractor

_DEFAULT_BOUNDS = [
    ("塔号", 70, 118), ("桩号", 118, 155), ("日期", 155, 228), ("天气", 228, 270),
    ("湿度", 270, 297), ("棒深", 297, 333), ("棒距", 333, 370), ("R", 370, 435),
    ("rho", 435, 505), ("季节", 505, 540), ("推荐", 540, 590),
    ("岩土", 590, 700), ("备注", 700, 9999),
]

_TOWER_RE = re.compile(r"^(?:N?[A-Z]{1,3}\d+[A-Z]?|#\d+)$")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _col_of(xc, bounds):
    for name, a, b in bounds:
        if a <= xc < b:
            return name
    return None


def _detect_bounds(words):
    """依据实际表头词动态计算列区间（解决原实现永远回落 _DEFAULT_BOUNDS 的 D-01 缺陷）。"""
    hdr_hits = {}
    for w in words:
        t = str(w[4]).strip()
        xc = (w[0] + w[2]) / 2.0
        if ("塔号" in t or "杆号" in t) and "塔号" not in hdr_hits:
            hdr_hits["塔号"] = xc
        elif "推荐" in t and "推荐" not in hdr_hits:
            hdr_hits["推荐"] = xc
        elif ("岩土" in t or "地质" in t or "土质" in t) and "岩土" not in hdr_hits:
            hdr_hits["岩土"] = xc
        elif "备注" in t and "备注" not in hdr_hits:
            hdr_hits["备注"] = xc

    if "塔号" in hdr_hits and "推荐" in hdr_hits:
        tx = hdr_hits["塔号"]
        rx = hdr_hits["推荐"]
        # 落入默认版式容差内直接用精细基准；发生明显版式平移/缩放时按锚点仿射映射各列边界
        if abs(tx - 94.0) <= 18.0 and abs(rx - 565.0) <= 22.0:
            return _DEFAULT_BOUNDS
        span_ref = 565.0 - 94.0
        scale = (rx - tx) / span_ref if abs(rx - tx) > 60.0 else 1.0
        shifted = []
        for name, a, b in _DEFAULT_BOUNDS:
            na = tx + (a - 94.0) * scale
            nb = 9999.0 if b >= 9000 else tx + (b - 94.0) * scale
            shifted.append((name, na, nb))
        return shifted
    return _DEFAULT_BOUNDS


def _union_bbox(ws):
    if not ws:
        return None
    return [
        round(min(w[0] for w in ws), 1),
        round(min(w[1] for w in ws), 1),
        round(max(w[2] for w in ws), 1),
        round(max(w[3] for w in ws), 1),
    ]


def _resolve_grid_col_map(header_texts: list[str], layout_profile=None) -> dict[str, int]:
    """将 GridCellExtractor 表头或 LayoutProfile 映射为 {塔号, 推荐, 岩土, 备注} 列索引。"""
    cmap: dict[str, int] = {}
    if layout_profile and isinstance(layout_profile, dict):
        for k, v in (layout_profile.get("column_mapping") or {}).items():
            try:
                idx = int(k)
            except (TypeError, ValueError):
                continue
            name = str(v or "")
            if any(tok in name for tok in ("塔号", "杆塔号", "杆号")):
                cmap["塔号"] = idx
            elif any(tok in name for tok in ("推荐", "电阻率")):
                cmap["推荐"] = idx
            elif any(tok in name for tok in ("岩土", "地质", "土质")):
                cmap["岩土"] = idx
            elif "备注" in name:
                cmap["备注"] = idx
    for idx, raw in enumerate(header_texts or []):
        t = re.sub(r"\s+", "", str(raw or ""))
        if not t:
            continue
        if ("塔号" in t or "杆号" in t) and "塔号" not in cmap:
            cmap["塔号"] = idx
        elif "推荐" in t and "推荐" not in cmap:
            cmap["推荐"] = idx
        elif ("岩土" in t or "地质" in t or "土质" in t) and "岩土" not in cmap:
            cmap["岩土"] = idx
        elif "备注" in t and "备注" not in cmap:
            cmap["备注"] = idx
    return cmap


def _extract_page_by_grid(path: str, page_idx: int, tower_re, cache_dir=None, layout_profile=None):
    """优先使用 GridCellExtractor 框线网格还原，彻底免疫换院列宽偏移。"""
    try:
        res = GridCellExtractor.extract_page(path, page_idx, cache_dir=cache_dir)
    except Exception:  # noqa: BLE001
        return {}
    if res.get("mode") != "grid":
        return {}
    rows = res.get("rows") or []
    col_map: dict[str, int] = {}
    page_records = {}
    for row in rows:
        if row.get("kind") == "banner":
            continue
        texts = row.get("texts") or []
        joined = "".join(texts)
        if ("塔号" in joined or "杆号" in joined) and ("推荐" in joined or "电阻率" in joined):
            col_map = _resolve_grid_col_map(texts, layout_profile=layout_profile)
            continue
        if not col_map or "塔号" not in col_map:
            continue
        ti = col_map["塔号"]
        if ti >= len(texts):
            continue
        th = re.sub(r"\s+", "", str(texts[ti] or "")).upper()
        if not tower_re.match(th):
            continue
        rec_i = col_map.get("推荐")
        soil_i = col_map.get("岩土")
        rem_i = col_map.get("备注")
        rec_str = str(texts[rec_i] or "").strip() if rec_i is not None and rec_i < len(texts) else ""
        soil_str = str(texts[soil_i] or "").strip() if soil_i is not None and soil_i < len(texts) else ""
        rem_str = str(texts[rem_i] or "").strip() if rem_i is not None and rem_i < len(texts) else ""
        m_num = _NUM_RE.search(rec_str)
        rho_val = float(m_num.group()) if m_num else None

        cells = row.get("cells") or []
        cell_bbox = None
        if rec_i is not None and rec_i < len(cells):
            c = cells[rec_i]
            cell_bbox = [round(c["x0"], 1), round(c["y0"], 1), round(c["x1"], 1), round(c["y1"], 1)]
        elif ti < len(cells):
            c = cells[ti]
            cell_bbox = [round(c["x0"], 1), round(c["y0"], 1), round(c["x1"], 1), round(c["y1"], 1)]

        page_records[th] = {
            "tower": th,
            "rho": rho_val,
            "soil": soil_str,
            "remark": rem_str,
            "page": page_idx + 1,
            "bbox": cell_bbox or row.get("bbox"),
            "raw_text": f"{th} | 推荐ρ={rec_str} Ω·m | {soil_str}".strip(" |") if rho_val is not None else f"{th} | {soil_str}".strip(" |"),
        }
    return page_records


def parse_rho_pdf(path, cache_dir=None, layout_profile=None):
    """解析土壤电阻率成果表：优先 GridCellExtractor，无框线页回退动态锚点聚类。"""
    doc = cache.get_doc_features(path, cache_dir=cache_dir)
    custom_pat = (layout_profile or {}).get("tower_id_regex") if isinstance(layout_profile, dict) else None
    tower_re = re.compile(custom_pat) if custom_pat else _TOWER_RE

    records = {}
    for pg in doc["pages"]:
        words = pg["words"]
        if len(words) < 12:
            continue

        grid_hits = _extract_page_by_grid(path, pg["page"], tower_re, cache_dir=cache_dir, layout_profile=layout_profile)
        if grid_hits:
            for th, rec in grid_hits.items():
                entry = records.setdefault(th, rec)
                if rec["rho"] is not None:
                    entry["rho"] = rec["rho"]
                    entry["page"] = rec["page"]
                    entry["bbox"] = rec["bbox"]
                    entry["raw_text"] = rec["raw_text"]
                if rec["soil"]:
                    entry["soil"] = rec["soil"]
                if rec["remark"]:
                    entry["remark"] = rec["remark"]
            continue

        bounds = _detect_bounds(words)
        body = [w for w in words if w[1] > 140]
        body.sort(key=lambda w: ((w[1] + w[3]) / 2.0, w[0]))

        lines = []
        cur = []
        for w in body:
            yc = (w[1] + w[3]) / 2.0
            if cur and abs(yc - cur[-1][0]) > 4.2:
                lines.append(cur)
                cur = []
            cur.append((yc, w))
        if cur:
            lines.append(cur)

        for ln in lines:
            cells = {}
            cell_words = {}
            for _, w in sorted(ln, key=lambda item: item[1][0]):
                col = _col_of((w[0] + w[2]) / 2.0, bounds)
                if not col:
                    continue
                cells[col] = (cells.get(col, "") + str(w[4])).strip()
                cell_words.setdefault(col, []).append(w)

            th = (cells.get("塔号") or "").strip().upper()
            if not tower_re.match(th):
                continue
            rec_str = (cells.get("推荐") or "").strip()
            m_num = _NUM_RE.search(rec_str)
            rho_val = float(m_num.group()) if m_num else None
            entry = records.setdefault(th, {
                "tower": th,
                "rho": None,
                "soil": "",
                "remark": "",
                "page": pg["page"] + 1,
                "bbox": None,
                "raw_text": "",
            })
            if rho_val is not None:
                entry["rho"] = rho_val
                entry["page"] = pg["page"] + 1
                entry["bbox"] = _union_bbox(cell_words.get("推荐") or cell_words.get("塔号"))
                entry["raw_text"] = f"{th} | 推荐ρ={rec_str} Ω·m | {cells.get('岩土', '')}".strip(" |")
            if cells.get("岩土"):
                entry["soil"] = cells["岩土"]
            if cells.get("备注"):
                entry["remark"] = cells["备注"]
            if entry["bbox"] is None:
                entry["bbox"] = _union_bbox(cell_words.get("塔号"))
                entry["raw_text"] = f"{th} | {cells.get('岩土', '')}".strip(" |")
    return records
