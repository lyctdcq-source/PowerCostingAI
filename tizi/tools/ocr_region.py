# -*- coding: utf-8 -*-
"""Tool: local_ocr_region — 离线局部 OCR 兜底工具（封装 tizi/ocr_local.py）。"""
from __future__ import annotations

import os
from typing import Any, Optional

from .. import ocr_local
from ..harness.registry import register_tool
from ..harness.workspace import TiziWorkspace
from ..schemas.base import ErrorCode, SideEffectLevel, ToolResult


@register_tool(
    "local_ocr_region",
    SideEffectLevel.READ_ONLY,
    description="离线局部区域 OCR 识别（辅助文本检索与人工框选兜底）",
)
def local_ocr_region(
    *,
    workspace: Optional[TiziWorkspace] = None,
    pdf_path: str,
    page: int = 0,
    bbox: list[float],
    dpi: int = 240,
) -> ToolResult[dict[str, Any]]:
    if not pdf_path or not os.path.isfile(pdf_path):
        return ToolResult(ok=False, code=ErrorCode.INVALID_INPUT, summary=f"PDF 不存在：{pdf_path}", recoverable=False)
    res = ocr_local.ocr_pdf_region(pdf_path, int(page), bbox, dpi=int(dpi))
    return ToolResult(
        ok=bool(res.get("available")),
        code=ErrorCode.OK if res.get("available") else ErrorCode.VLM_UNAVAILABLE,
        data=res,
        summary=f"离线 OCR（{res.get('engine', 'none')}）：{str(res.get('text') or '')[:80]}",
    )
