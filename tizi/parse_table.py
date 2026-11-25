# -*- coding: utf-8 -*-
"""阶段二：按版式模板把页面还原成行 × 列。

核心只有两件事：
  1. 行聚类 —— 同一行内文字的 y 中心差小于容差
  2. 列归属 —— 文字 x 中心落入哪个列区间
列区间来自配置，不写死在代码里。
"""
import re
import pymupdf


from .grid_parser import GridCellExtractor
from .provenance import UnitConverter


def _cluster(words, tol):
    """按 y 中心聚类成行。"""
    lines, cur = [], []
    for w in sorted(words, key=lambda x: ((x[1] + x[3]) / 2, x[0])):
        yc = (w[1] + w[3]) / 2
        if cur and abs(yc - cur[-1][0]) > tol:
            lines.append(cur)
            cur = []
        cur.append((yc, w))
    if cur:
        lines.append(cur)
    return lines


def _col_of(xc, columns):
    for c in columns:
        if c["x0"] <= xc < c["x1"]:
            return c["key"]
    return None


def _parse_with_grid(path, layout, layout_profile, cache_dir=None):
    """按 GridCellExtractor 框线网格 + LayoutProfile 动态列映射与单位覆盖无损提取（支持跨院与多行折行）。"""
    col_map: dict[int, str] = {}
    for k, v in (layout_profile.get("column_mapping") or {}).items():
        try:
            col_map[int(k)] = str(v).strip()
        except (TypeError, ValueError):
            continue
    if not col_map:
        return None

    unit_overrides = layout_profile.get("unit_overrides") or {}
    custom_pat = layout_profile.get("tower_id_regex") or (layout or {}).get("row_pattern") or r"^(?:[A-Z]{1,3}|#\d+|N?[A-Z]{1,2})\d+[A-Z]?$"
    try:
        pattern = re.compile(custom_pat, re.IGNORECASE)
    except re.error:
        pattern = re.compile(r"^[A-Z0-9#-]{2,}$", re.IGNORECASE)

    import pymupdf

    doc = pymupdf.open(path)
    n_pages = len(doc)
    doc.close()

    rows = []
    # 查找塔号列索引
    key_col_idx = next((ci for ci, name in col_map.items() if name in ("杆塔号", "塔号", "杆号")), None)
    for pno in range(n_pages):
        gres = GridCellExtractor.extract_page(path, pno, cache_dir=cache_dir)
        for item in gres.get("rows") or []:
            if item.get("kind") == "banner":
                continue
            texts = item.get("texts") or []
            gcells = item.get("cells") or []
            key_val = ""
            if key_col_idx is not None and key_col_idx < len(texts):
                key_val = re.sub(r"\s+", "", str(texts[key_col_idx] or "")).strip()
            if not key_val or not pattern.match(key_val):
                continue
            # 排除表头行本身
            if any(h in key_val for h in ("杆塔号", "塔号", "序号")):
                continue

            cells, cbox = {}, {}
            for ci, cname in col_map.items():
                if ci < 0 or ci >= len(texts):
                    continue
                val_txt = str(texts[ci] or "").strip()
                if cname in unit_overrides and val_txt:
                    src_u = UnitConverter.norm_unit(str(unit_overrides[cname]))
                    m_num = re.search(r"-?\d+(?:\.\d+)?", val_txt)
                    if m_num and cname in ("全重", "杆塔.全重(t)", "塔重") and src_u == "kg":
                        conv, _ = UnitConverter.convert(float(m_num.group()), "kg", "t", decimals=3)
                        val_txt = f"{conv:g}"
                cells[cname] = val_txt
                cells[f"c{ci + 1}"] = val_txt
                if ci < len(gcells):
                    cb = gcells[ci]
                    cbox[cname] = [round(cb["x0"], 1), round(cb["y0"], 1), round(cb["x1"], 1), round(cb["y1"], 1)]
                    cbox[f"c{ci + 1}"] = cbox[cname]

            rows.append({
                "cells": cells,
                "cell_bbox": cbox,
                "page": pno + 1,
                "bbox": item.get("bbox") or [0.0, 0.0, 0.0, 0.0],
            })
    return rows


def parse(path, layout, layout_profile=None, cache_dir=None):
    """返回 [{cells, page, bbox}]，按行顺序。支持异院 LayoutProfile 框线网格优先还原。"""
    if (
        isinstance(layout_profile, dict)
        and layout_profile.get("column_mapping")
        and (
            layout_profile.get("source") == "vlm_learned"
            or (layout or {}).get("use_grid")
            or len((layout or {}).get("columns") or []) < 3
        )
    ):
        grid_rows = _parse_with_grid(path, layout, layout_profile, cache_dir=cache_dir)
        if grid_rows:
            return grid_rows

    pattern = re.compile(layout["row_pattern"])
    key_col = layout["row_key"]
    tol = layout.get("row_tolerance", 3.5)
    top = layout.get("body_top", 0)

    doc = pymupdf.open(path)
    rows = []
    for pno, page in enumerate(doc):
        words = [w for w in page.get_text("words") if w[1] > top]
        for line in _cluster(words, tol):
            cells, cbox = {}, {}
            xs, ys, xe, ye = [], [], [], []
            for _, w in sorted(line, key=lambda t: t[1][0]):
                k = _col_of((w[0] + w[2]) / 2, layout["columns"])
                if not k:
                    continue
                cells[k] = (cells.get(k, "") + w[4]).strip()
                b = cbox.get(k)
                cbox[k] = [w[0], w[1], w[2], w[3]] if not b else [
                    min(b[0], w[0]), min(b[1], w[1]), max(b[2], w[2]), max(b[3], w[3])]
                xs.append(w[0]); ys.append(w[1]); xe.append(w[2]); ye.append(w[3])

            if not cells:
                continue

            if pattern.match(cells.get(key_col, "")):
                rows.append({
                    "cells": cells,
                    "cell_bbox": {k: [round(v, 1) for v in b] for k, b in cbox.items()},
                    "page": pno + 1,
                    "bbox": [round(min(xs), 1), round(min(ys), 1),
                             round(max(xe), 1), round(max(ye), 1)],
                })
            elif rows and not cells.get(key_col) and cells.get("remark"):
                # 续行：只有备注，并入上一行
                prev = rows[-1]
                prev["cells"]["remark"] = (prev["cells"].get("remark", "") + " " + cells["remark"]).strip()

    doc.close()
    return rows
