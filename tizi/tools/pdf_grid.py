# -*- coding: utf-8 -*-
"""Tool: pdf_extract_grid — 框线网格还原 + 表头几何指纹计算 + LayoutProfile 动态列映射驱动。"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Optional

from ..grid_parser import GridCellExtractor
from ..harness.registry import register_tool
from ..harness.workspace import TiziWorkspace
from ..schemas.base import ErrorCode, SideEffectLevel, ToolResult
from ..schemas.layout import HeaderFingerprint


def compute_header_fingerprint(doc_id: str, target_sheet: str, header_tokens: list[str]) -> HeaderFingerprint:
    """基于归一化表头词序与列数生成稳定的跨设计院表头指纹。"""
    norm_tokens = [re.sub(r"\s+", "", str(t or "")).strip() for t in (header_tokens or []) if str(t or "").strip()]
    sig = f"{target_sheet}|{len(norm_tokens)}|{'|'.join(norm_tokens)}"
    fp = hashlib.sha256(sig.encode("utf-8")).hexdigest()[:16]
    return HeaderFingerprint(
        fingerprint=fp,
        doc_id=doc_id,
        target_sheet=target_sheet,
        col_count=len(norm_tokens),
        header_tokens=norm_tokens[:40],
    )


@register_tool(
    "pdf_extract_grid",
    SideEffectLevel.ARTIFACT_WRITE,
    artifact_subdir="03_cv_segments",
    description="对 T2/T2+T3 表格页执行框线网格几何还原，并按可选 LayoutProfile 动态映射标准字段",
)
def pdf_extract_grid(
    *,
    workspace: Optional[TiziWorkspace] = None,
    pdf_path: str,
    doc_id: str = "",
    pages: Optional[list[int]] = None,
    target_sheet: str = "",
    layout_profile: Optional[dict[str, Any]] = None,
    cache_dir: Optional[str] = None,
) -> ToolResult[dict[str, Any]]:
    if not pdf_path or not os.path.isfile(pdf_path):
        return ToolResult(ok=False, code=ErrorCode.INVALID_INPUT, summary=f"PDF 不存在：{pdf_path}", recoverable=False)

    # 强制执行风控红线：若传入了外部/VLM 学习的 LayoutProfile，必须 sanity_passed=True 才允许驱动批量取数
    if layout_profile and layout_profile.get("source") == "vlm_learned" and not layout_profile.get("sanity_passed"):
        return ToolResult(
            ok=False,
            code=ErrorCode.SANITY_CHECK_FAILED,
            summary="LayoutProfile 未通过 5 行抽样值域自检（sanity_passed=False），拒绝驱动批量提取",
            recoverable=True,
        )

    page_indices = list(pages) if pages is not None else [0]
    extracted_rows: list[dict[str, Any]] = []
    headers: list[str] = []
    banners: list[str] = []
    col_map: dict[int, str] = {}
    if isinstance(layout_profile, dict):
        for k, v in (layout_profile.get("column_mapping") or {}).items():
            try:
                col_map[int(k)] = str(v)
            except (TypeError, ValueError):
                continue

    for p_idx in page_indices:
        page_res = GridCellExtractor.extract_page(pdf_path, int(p_idx), cache_dir=cache_dir)
        for ln in page_res.get("pre_lines") or []:
            banners.append(str(ln))
        for r in page_res.get("rows") or []:
            if r.get("kind") == "banner":
                banners.append(str(r.get("text") or ""))
                continue
            texts = r.get("texts") or []
            if not headers and texts:
                headers = [str(t or "").strip() for t in texts]
            mapped = {}
            if col_map:
                for ci, cname in col_map.items():
                    if 0 <= ci < len(texts):
                        mapped[cname] = texts[ci]
            extracted_rows.append({
                "page": r.get("page", int(p_idx) + 1),
                "bbox": r.get("bbox"),
                "texts": texts,
                "mapped": mapped,
            })

    fp = compute_header_fingerprint(doc_id or os.path.basename(pdf_path), target_sheet or "generic", headers)
    payload = {
        "doc_id": doc_id or os.path.basename(pdf_path),
        "target_sheet": target_sheet,
        "header_fingerprint": fp.model_dump(),
        "row_count": len(extracted_rows),
        "headers": headers,
        "banners": banners[:30],
        "rows": extracted_rows,
    }
    return ToolResult(
        ok=True,
        code=ErrorCode.OK,
        data=payload,
        summary=f"网格提取完成：{len(extracted_rows)} 行，表头 {len(headers)} 列，指纹 {fp.fingerprint}",
    )
