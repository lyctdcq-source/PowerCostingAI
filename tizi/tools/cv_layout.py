# -*- coding: utf-8 -*-
"""Tool: cv_layout_segment — 对 T3 矢量详图/T0 扫描件切分图框、材料表与图签，生成 visual_index.json。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

from ..harness import cache as h_cache
from ..harness.registry import register_tool
from ..harness.workspace import TiziWorkspace
from ..schemas.base import ErrorCode, SideEffectLevel, ToolResult

# 匹配基础型号（如 WZ1055 / WK1065 / GZZ24140）与常见塔型代号（如 2E1Y7-GFJ3 / 2E2Y6-J4）
_ENTITY_MODEL_RE = re.compile(
    r"\b(?:WZ|WK|TW|GZZ|GZ|LH|TJ|YS|QG|DB|BF)[A-Z0-9]{2,8}(?:\([0-9.]+\))?\b"
    r"|\b(?:V\d+-)?[125][A-Z][A-Z0-9]{1,5}-[A-Z]{1,4}\d{1,3}\b",
    re.IGNORECASE,
)


def segment_page_regions(pdf_path: str, page_idx: int, *, cache_dir: Optional[str] = None) -> dict[str, Any]:
    """基于矢量框线聚簇与文字锚点定位图签栏（title_block）、材料表（param_table）与主图区（diagram）。"""
    doc = h_cache.get_doc_features(pdf_path, cache_dir=cache_dir)
    if page_idx < 0 or page_idx >= doc["n_pages"]:
        raise IndexError(f"页码超出范围：{page_idx}")
    pg = doc["pages"][page_idx]
    w = float(pg.get("width") or 842.0)
    h = float(pg.get("height") or 595.0)
    words = pg.get("words") or []
    draws = h_cache.get_page_drawings(pdf_path, page_idx)
    h_lines = draws.get("h_lines") or []
    v_lines = draws.get("v_lines") or []

    regions: list[dict[str, Any]] = []

    # 1) 右下角图签栏（CAD 工程图国际/国标通用位置：右下 28%×18%）
    tb_bbox = [round(w * 0.68, 1), round(h * 0.78, 1), round(w * 0.99, 1), round(h * 0.99, 1)]
    tb_words = [str(wd[4]) for wd in words if wd[0] >= tb_bbox[0] - 20 and wd[1] >= tb_bbox[1] - 20]
    regions.append({
        "region_id": f"p{page_idx + 1}_title_block",
        "kind": "title_block",
        "page": page_idx,
        "bbox": tb_bbox,
        "text_snippet": " ".join(tb_words)[:180],
    })

    # 2) 材料表 / 参数表定位：通过关键词（材料表/工程量/砼/钢筋/混凝土/单基重）或密集网格框线聚簇
    table_anchors = [
        wd for wd in words
        if any(k in str(wd[4]) for k in ("材料表", "工程量", "混凝土", "砼", "钢筋", "HRB", "HPB", "单基重", "塔重", "基础尺寸"))
    ]
    if table_anchors:
        ax0 = max(0.0, min(wd[0] for wd in table_anchors) - w * 0.06)
        ay0 = max(0.0, min(wd[1] for wd in table_anchors) - h * 0.05)
        ax1 = min(w, max(wd[2] for wd in table_anchors) + w * 0.28)
        ay1 = min(h, max(wd[3] for wd in table_anchors) + h * 0.25)
        regions.append({
            "region_id": f"p{page_idx + 1}_param_table",
            "kind": "param_table",
            "page": page_idx,
            "bbox": [round(ax0, 1), round(ay0, 1), round(ax1, 1), round(ay1, 1)],
            "text_snippet": " ".join(str(wd[4]) for wd in table_anchors[:15])[:180],
        })
    elif len(h_lines) >= 6 and len(v_lines) >= 4:
        # 纯矢量无字层详图：取网格最密集区域（通常位于右上/右侧参数栏）
        hx0 = min(ln[1] for ln in h_lines)
        hx1 = max(ln[2] for ln in h_lines)
        hy0 = min(ln[0] for ln in h_lines)
        hy1 = max(ln[0] for ln in h_lines)
        # 右半区优先作为工程量表候选区
        rx0 = max(hx0, w * 0.45)
        regions.append({
            "region_id": f"p{page_idx + 1}_param_table",
            "kind": "param_table",
            "page": page_idx,
            "bbox": [round(rx0, 1), round(hy0, 1), round(min(w, hx1), 1), round(min(h, hy1), 1)],
            "text_snippet": "(vector_table_region)",
        })

    # 3) 主图区（整图裁剪去白边，用于 VLM 宏观确认或扫描页直读）
    regions.append({
        "region_id": f"p{page_idx + 1}_diagram",
        "kind": "diagram",
        "page": page_idx,
        "bbox": [round(w * 0.02, 1), round(h * 0.02, 1), round(w * 0.98, 1), round(h * 0.98, 1)],
        "text_snippet": (pg.get("text") or "")[:180],
    })

    # 4) 提取本页命中的实体型号（基础型号 / 杆塔型号）
    full_text = str(pg.get("text") or "")
    entities = sorted({m.group(0).upper() for m in _ENTITY_MODEL_RE.finditer(full_text)})

    return {
        "page": page_idx,
        "size": [round(w, 1), round(h, 1)],
        "entities": entities,
        "regions": regions,
    }


def build_visual_index_for_docs(
    docs: list[dict[str, Any]],
    *,
    workspace: Optional[TiziWorkspace] = None,
    cache_dir: Optional[str] = None,
    max_pages_per_doc: int = 40,
) -> dict[str, Any]:
    """为 T3 详图与 T0 扫描分册建立实体型号 -> (doc_rel, page, param_table_bbox) 视觉索引。"""
    entity_index: dict[str, list[dict[str, Any]]] = {}
    doc_summaries: list[dict[str, Any]] = []

    for d in docs or []:
        pdf_path = str(d.get("path") or "")
        rel = str(d.get("rel") or os.path.basename(pdf_path)).replace("\\", "/")
        if not pdf_path or not os.path.isfile(pdf_path):
            continue
        cached = h_cache.get_visual_index_cache(pdf_path, cache_dir) if cache_dir else None
        if cached is None:
            feat = h_cache.get_doc_features(pdf_path, cache_dir=cache_dir)
            n_pg = min(int(feat.get("n_pages") or 0), max_pages_per_doc)
            pages_seg = [segment_page_regions(pdf_path, i, cache_dir=cache_dir) for i in range(n_pg)]
            # 同时从文件名中提取型号线索（例如 "WZ1055基础详图.pdf"）
            fname_entities = sorted({m.group(0).upper() for m in _ENTITY_MODEL_RE.finditer(Path(pdf_path).stem)})
            cached = {"rel": rel, "n_pages": n_pg, "fname_entities": fname_entities, "pages": pages_seg}
            if cache_dir:
                h_cache.set_visual_index_cache(pdf_path, cache_dir, cached)

        doc_summaries.append({"rel": rel, "doc_role": str(d.get("doc_role") or ""), "n_pages": cached.get("n_pages", 0)})
        for pg in cached.get("pages") or []:
            param_region = next((r for r in pg["regions"] if r["kind"] == "param_table"), None) or pg["regions"][-1]
            ents = set(pg.get("entities") or []) | set(cached.get("fname_entities") or [])
            for ent in ents:
                entity_index.setdefault(ent, []).append({
                    "doc_rel": rel,
                    "doc_path": pdf_path,
                    "doc_role": str(d.get("doc_role") or ""),
                    "page": pg["page"],
                    "bbox": param_region["bbox"],
                    "kind": param_region["kind"],
                })

    payload = {
        "docs": doc_summaries,
        "entity_count": len(entity_index),
        "entities": entity_index,
    }
    if workspace is not None:
        workspace.write_json("01_catalog/visual_index.json", payload)
    return payload


@register_tool(
    "cv_layout_segment",
    SideEffectLevel.ARTIFACT_WRITE,
    artifact_subdir="03_cv_segments",
    description="对 T3 矢量图/扫描件切分图框、材料表区与图签栏并落盘区域 bbox",
)
def cv_layout_segment(
    *,
    workspace: Optional[TiziWorkspace] = None,
    pdf_path: str,
    doc_id: str = "",
    page: int = 0,
    cache_dir: Optional[str] = None,
) -> ToolResult[dict[str, Any]]:
    if not pdf_path or not os.path.isfile(pdf_path):
        return ToolResult(ok=False, code=ErrorCode.INVALID_INPUT, summary=f"PDF 不存在：{pdf_path}", recoverable=False)
    seg = segment_page_regions(pdf_path, int(page), cache_dir=cache_dir)
    uri = None
    if workspace is not None:
        safe_id = re.sub(r"[^A-Za-z0-9_\-]+", "_", doc_id or Path(pdf_path).stem)[:40]
        uri = workspace.write_json(f"03_cv_segments/{safe_id}_p{int(page) + 1}_regions.json", seg)
    return ToolResult(
        ok=True,
        code=ErrorCode.OK,
        data=seg,
        artifact_uri=uri,
        summary=f"切分完成：第 {int(page) + 1} 页 {len(seg['regions'])} 个区域，命中 {len(seg['entities'])} 个型号线索",
    )
