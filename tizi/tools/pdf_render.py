# -*- coding: utf-8 -*-
"""Tool: pdf_crop_render — 高清局部裁切（内置 +8% 安全边距与方向性 +20% 截断自外扩）。"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Optional

import pymupdf

from ..harness.registry import register_tool
from ..harness.workspace import TiziWorkspace
from ..schemas.base import ErrorCode, SideEffectLevel, ToolResult

DEFAULT_MARGIN_RATIO = 0.08
EXPAND_STEP_RATIO = 0.20


def compute_expanded_bbox(
    bbox: Optional[list[float]],
    page_width: float,
    page_height: float,
    *,
    margin_ratio: float = DEFAULT_MARGIN_RATIO,
    expand_level: int = 0,
    expand_direction: str = "all",
) -> list[float]:
    """按工程规范 §F.4.3 计算含安全边距与截断外扩后的裁切框（严禁越出页面边界）。"""
    if not bbox or len(bbox) != 4:
        return [0.0, 0.0, round(page_width, 1), round(page_height, 1)]
    x0, y0, x1, y1 = [float(v) for v in bbox]
    bw = max(20.0, x1 - x0)
    bh = max(20.0, y1 - y0)

    # 基础 +8% 四周安全边距
    mx = bw * margin_ratio
    my = bh * margin_ratio
    nx0, ny0, nx1, ny1 = x0 - mx, y0 - my, x1 + mx, y1 + my

    # 截断自外扩：每递增 1 级按指定方向外扩 +20%
    if expand_level > 0:
        ex = bw * EXPAND_STEP_RATIO * expand_level
        ey = bh * EXPAND_STEP_RATIO * expand_level
        d = (expand_direction or "all").lower()
        if d in ("all", "left"):
            nx0 -= ex
        if d in ("all", "right"):
            nx1 += ex
        if d in ("all", "top"):
            ny0 -= ey
        if d in ("all", "bottom"):
            ny1 += ey

    return [
        round(max(0.0, nx0), 1),
        round(max(0.0, ny0), 1),
        round(min(page_width, nx1), 1),
        round(min(page_height, ny1), 1),
    ]


@register_tool(
    "pdf_crop_render",
    SideEffectLevel.ARTIFACT_WRITE,
    artifact_subdir="04_ocr_crops",
    description="将 PDF 指定区域按 185~260 dpi 裁切为 PNG 并写入 04_ocr_crops/（默认含 +8% 安全边距）",
)
def pdf_crop_render(
    *,
    workspace: Optional[TiziWorkspace] = None,
    pdf_path: str,
    doc_id: str = "",
    page: int = 0,
    bbox: Optional[list[float]] = None,
    dpi: int = 220,
    expand_level: int = 0,
    expand_direction: str = "all",
    margin_ratio: float = DEFAULT_MARGIN_RATIO,
) -> ToolResult[dict[str, Any]]:
    if not pdf_path or not os.path.isfile(pdf_path):
        return ToolResult(ok=False, code=ErrorCode.INVALID_INPUT, summary=f"PDF 不存在：{pdf_path}", recoverable=False)

    dpi_val = max(120, min(300, int(dpi)))
    doc = pymupdf.open(pdf_path)
    try:
        if page < 0 or page >= len(doc):
            return ToolResult(ok=False, code=ErrorCode.INVALID_INPUT, summary=f"页码超出范围：{page}", recoverable=False)
        pg = doc[int(page)]
        pw, ph = float(pg.rect.width), float(pg.rect.height)
        clip_bbox = compute_expanded_bbox(
            bbox, pw, ph,
            margin_ratio=margin_ratio,
            expand_level=int(expand_level),
            expand_direction=expand_direction,
        )
        rect = pymupdf.Rect(*clip_bbox)
        # 控制最大像素长边 <= 1568px
        scale = dpi_val / 72.0
        longest_px = max(rect.width, rect.height) * scale
        if longest_px > 1568.0 and longest_px > 0:
            scale = 1568.0 / max(rect.width, rect.height)
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, alpha=False)
        png_bytes = pix.tobytes("png")
        px_size = [int(pix.width), int(pix.height)]
    finally:
        doc.close()

    sig = hashlib.sha256(f"{pdf_path}|{page}|{clip_bbox}|{dpi_val}".encode("utf-8")).hexdigest()[:10]
    safe_id = re.sub(r"[^A-Za-z0-9_\-]+", "_", doc_id or Path(pdf_path).stem)[:32]
    rel_uri = f"04_ocr_crops/{safe_id}_p{int(page) + 1}_{sig}.png"

    abs_path = ""
    if workspace is not None:
        rel_uri = workspace.write_bytes(rel_uri, png_bytes)
        abs_path = str(workspace.resolve_safe(rel_uri))

    return ToolResult(
        ok=True,
        code=ErrorCode.OK,
        data={
            "png_uri": rel_uri,
            "abs_path": abs_path,
            "pixel_size": px_size,
            "pdf_bbox": clip_bbox,
            "page": int(page),
            "expand_level": int(expand_level),
        },
        artifact_uri=rel_uri,
        summary=f"裁切完成：{rel_uri} ({px_size[0]}x{px_size[1]}px)",
    )
