# -*- coding: utf-8 -*-
"""升级版 T2 表格几何还原引擎：框线网格优先（GridCellExtractor）+ 行带聚类兜底（BandClusterExtractor）+ 合并表头树（HeaderTreeBuilder）。

核心解决《设备材料清册》《勘察一览表》《串型汇总表》中的三大难点：
1. 单元格内多行折行不再被 3.5pt y 聚类拆成多条残缺行；
2. 序号/分类列垂直居中或纵向合并单元格自动识别并继承，零错行、零漏字；
3. 合并双层/三层表头自动拼出复合列名，消除同名子列歧义。
"""
import re

from . import cache


def _cluster_coords(vals, tol=2.8):
    if not vals:
        return []
    s = sorted(vals)
    groups = [[s[0]]]
    for v in s[1:]:
        if v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [round(sum(g) / len(g), 2) for g in groups]


def _join_cell_words(words):
    """按视觉阅读顺序拼接单格内的多行文字，中文间不插多余空格，英文/数字词间保留空格。"""
    if not words:
        return ""
    ws = sorted(words, key=lambda w: (round(w[1] / 3.5), w[0]))
    lines = []
    cur = []
    for w in ws:
        if cur and abs(((w[1] + w[3]) / 2.0) - ((cur[-1][1] + cur[-1][3]) / 2.0)) > 4.2:
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)

    parts = []
    for ln in lines:
        ln.sort(key=lambda w: w[0])
        buf = ""
        for idx, w in enumerate(ln):
            tok = str(w[4]).strip()
            if not tok:
                continue
            if idx > 0 and buf:
                prev = buf[-1]
                nxt = tok[0]
                if prev.isascii() and prev.isalnum() and nxt.isascii() and nxt.isalnum():
                    if w[0] - ln[idx - 1][2] > 1.8:
                        buf += " "
            buf += tok
        if buf:
            parts.append(buf)
    return "".join(parts) if all(not p.isascii() for p in parts) else " ".join(parts)


def _has_h_sep(h_lines, y_target, x0, x1, y_tol=3.2):
    """检查在 y_target 处是否存在覆盖 [x0, x1] 至少 45% 宽度的水平分隔线。"""
    span = max(x1 - x0, 1.0)
    covered = 0.0
    for y, lx0, lx1 in h_lines:
        if abs(y - y_target) <= y_tol:
            ov = min(x1, lx1) - max(x0, lx0)
            if ov > 0:
                covered += ov
    return (covered / span) >= 0.45


def _has_v_sep(v_lines, x_target, y0, y1, x_tol=3.2):
    """检查在 x_target 处是否存在覆盖 [y0, y1] 至少 45% 高度的竖直分隔线。"""
    span = max(y1 - y0, 1.0)
    covered = 0.0
    for x, ly0, ly1 in v_lines:
        if abs(x - x_target) <= x_tol:
            ov = min(y1, ly1) - max(y0, ly0)
            if ov > 0:
                covered += ov
    return (covered / span) >= 0.45


class GridCellExtractor:
    """Tier-1 框线网格精确切分器。"""

    @staticmethod
    def extract_page(path, page_idx: int, cache_dir=None):
        """提取单页所有框线表格区域，返回行列表与表外标题文本。

        每行格式：
          {
            "page": page_idx + 1,
            "y0": float, "y1": float,
            "bbox": [x0, y0, x1, y1],
            "cells": [{"x0", "x1", "y0", "y1", "text", "col_idx", "merged_from_above": bool}, ...],
            "texts": [str, ...],
          }
        """
        doc = cache.get_doc_features(path, cache_dir=cache_dir)
        pg = doc["pages"][page_idx]
        words = pg["words"]
        draws = cache.get_page_drawings(path, page_idx)
        h_lines = draws["h_lines"]
        v_lines = draws["v_lines"]

        # 过滤过短装饰线（页眉细线等保留宽线）
        long_h = [ln for ln in h_lines if (ln[2] - ln[1]) >= 25.0]
        long_v = [ln for ln in v_lines if (ln[2] - ln[1]) >= 10.0]

        ys = _cluster_coords([ln[0] for ln in long_h], tol=2.8)
        xs = _cluster_coords([ln[0] for ln in long_v], tol=2.8)

        if len(ys) < 2 or len(xs) < 3:
            return BandClusterExtractor.extract_page(pg, page_idx)

        table_x0, table_x1 = xs[0], xs[-1]
        table_y0, table_y1 = ys[0], ys[-1]

        # 收集表外（表格上方或中间非网格区）标题行文本，供状态机切换工程名/章节
        above_words = [w for w in words if (w[1] + w[3]) / 2.0 < table_y0 + 1.0]
        below_words = [w for w in words if (w[1] + w[3]) / 2.0 > table_y1 - 1.0]
        pre_lines = _words_to_text_lines(above_words)
        post_lines = _words_to_text_lines(below_words)

        # 按行带切分，并合并无竖线分隔的横向合并单元格、记录纵向无横线分隔的合并格
        grid_rows = []
        prev_row_cells_by_x = {}  # (x0, x1) -> text

        for ri in range(len(ys) - 1):
            ry0, ry1 = ys[ri], ys[ri + 1]
            if (ry1 - ry0) < 6.0:
                continue
            # 该行带内部是否落有真实竖线（若整带左右无边线，说明是两张独立表格之间的文字说明带！）
            active_xs = [x for x in xs if _has_v_sep(long_v, x, ry0, ry1)]
            band_words = [
                w for w in words
                if ry0 - 1.5 <= ((w[1] + w[3]) / 2.0) <= ry1 + 1.5
                and table_x0 - 5.0 <= ((w[0] + w[2]) / 2.0) <= table_x1 + 5.0
            ]
            if len(active_xs) < 2:
                # 表格中间夹杂的章节标题或注记（例如 P19 下半页「光缆材料」、P41 「（B、C回）结构材料表」）
                if band_words:
                    inter_lines = _words_to_text_lines(band_words)
                    grid_rows.append({
                        "kind": "banner",
                        "page": page_idx + 1,
                        "y0": ry0, "y1": ry1,
                        "bbox": [table_x0, ry0, table_x1, ry1],
                        "lines": inter_lines,
                        "text": " ".join(inter_lines),
                        "cells": [],
                        "texts": [],
                    })
                    prev_row_cells_by_x = {}
                continue

            row_cells = []
            new_prev = {}
            for ci in range(len(active_xs) - 1):
                cx0, cx1 = active_xs[ci], active_xs[ci + 1]
                if (cx1 - cx0) < 4.0:
                    continue
                cwords = [
                    w for w in band_words
                    if cx0 - 1.5 <= ((w[0] + w[2]) / 2.0) <= cx1 + 1.5
                ]
                txt = _join_cell_words(cwords)
                merged_above = False
                if not txt and not _has_h_sep(long_h, ry0, cx0, cx1):
                    # 纵向合并单元格：向上继承
                    for (px0, px1), ptxt in prev_row_cells_by_x.items():
                        if abs(px0 - cx0) <= 5.0 and abs(px1 - cx1) <= 5.0 and ptxt:
                            txt = ptxt
                            merged_above = True
                            break
                if txt:
                    new_prev[(cx0, cx1)] = txt
                row_cells.append({
                    "col_idx": len(row_cells),
                    "x0": cx0, "x1": cx1, "y0": ry0, "y1": ry1,
                    "bbox": [round(cx0, 1), round(ry0, 1), round(cx1, 1), round(ry1, 1)],
                    "text": txt,
                    "merged_from_above": merged_above,
                })
            prev_row_cells_by_x = new_prev
            if any(c["text"] for c in row_cells):
                grid_rows.append({
                    "kind": "row",
                    "page": page_idx + 1,
                    "y0": ry0, "y1": ry1,
                    "bbox": [round(active_xs[0], 1), round(ry0, 1), round(active_xs[-1], 1), round(ry1, 1)],
                    "cells": row_cells,
                    "texts": [c["text"] for c in row_cells],
                })

        return {
            "mode": "grid",
            "page": page_idx + 1,
            "pre_lines": pre_lines,
            "post_lines": post_lines,
            "rows": grid_rows,
        }


class BandClusterExtractor:
    """Tier-2 无框线投影带聚类兜底（供无框线扫描/文字表格页使用）。"""

    @staticmethod
    def extract_page(pg: dict, page_idx: int):
        words = pg.get("words") or []
        if not words:
            return {"mode": "band", "page": page_idx + 1, "pre_lines": [], "post_lines": [], "rows": []}
        ws = sorted(words, key=lambda w: ((w[1] + w[3]) / 2.0, w[0]))
        lines = []
        cur = []
        for w in ws:
            yc = (w[1] + w[3]) / 2.0
            if cur and abs(yc - cur[-1][0]) > 4.2:
                lines.append([x[1] for x in cur])
                cur = []
            cur.append((yc, w))
        if cur:
            lines.append([x[1] for x in cur])

        rows = []
        for ln in lines:
            ln.sort(key=lambda w: w[0])
            # 按横向间隙 > 12pt 拆单元格
            segs = [[ln[0]]]
            for w in ln[1:]:
                if w[0] - segs[-1][-1][2] > 12.0:
                    segs.append([w])
                else:
                    segs[-1].append(w)
            cells = []
            for idx, seg in enumerate(segs):
                x0 = min(w[0] for w in seg)
                y0 = min(w[1] for w in seg)
                x1 = max(w[2] for w in seg)
                y1 = max(w[3] for w in seg)
                cells.append({
                    "col_idx": idx,
                    "x0": x0, "x1": x1, "y0": y0, "y1": y1,
                    "bbox": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
                    "text": _join_cell_words(seg),
                    "merged_from_above": False,
                })
            rows.append({
                "kind": "row",
                "page": page_idx + 1,
                "y0": min(c["y0"] for c in cells),
                "y1": max(c["y1"] for c in cells),
                "bbox": [round(min(c["x0"] for c in cells), 1), round(min(c["y0"] for c in cells), 1),
                         round(max(c["x1"] for c in cells), 1), round(max(c["y1"] for c in cells), 1)],
                "cells": cells,
                "texts": [c["text"] for c in cells],
            })
        return {"mode": "band", "page": page_idx + 1, "pre_lines": [], "post_lines": [], "rows": rows}


class HeaderTreeBuilder:
    """多行合并表头归一化工具：按空间覆盖关系拼出标准复合列名。"""

    @staticmethod
    def build(header_rows: list):
        if not header_rows:
            return []
        if len(header_rows) == 1:
            return [re.sub(r"\s+", "", c["text"]) for c in header_rows[0]["cells"]]
        bottom = header_rows[-1]["cells"]
        top_layers = header_rows[:-1]
        names = []
        for bc in bottom:
            xc = (bc["x0"] + bc["x1"]) / 2.0
            parts = []
            for layer in top_layers:
                for tc in layer["cells"]:
                    if tc["x0"] - 2.0 <= xc <= tc["x1"] + 2.0:
                        t = re.sub(r"\s+", "", tc["text"])
                        if t and t not in parts:
                            parts.append(t)
                        break
            bt = re.sub(r"\s+", "", bc["text"])
            if bt and bt not in parts:
                parts.append(bt)
            names.append(".".join(parts))
        return names


def _words_to_text_lines(words):
    if not words:
        return []
    ws = sorted(words, key=lambda w: ((w[1] + w[3]) / 2.0, w[0]))
    lines = []
    cur = []
    for w in ws:
        yc = (w[1] + w[3]) / 2.0
        if cur and abs(yc - cur[-1][0]) > 4.5:
            lines.append(_join_cell_words([x[1] for x in cur]))
            cur = []
        cur.append((yc, w))
    if cur:
        lines.append(_join_cell_words([x[1] for x in cur]))
    return [ln for ln in lines if ln]
