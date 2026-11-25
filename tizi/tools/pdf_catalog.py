# -*- coding: utf-8 -*-
"""Tool: pdf_probe_catalog / carrier_classify — 目录探查、去重、分册定性与载体分级。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from .. import carrier, discover
from ..harness.registry import register_tool
from ..harness.workspace import TiziWorkspace
from ..schemas.base import ErrorCode, SideEffectLevel, ToolResult

# 预解析《隐藏表（勿删）》关键枚举卡（复现 PlanSightRAG 规则阈值预解析机理）
ENUM_ANCHOR_CARD: dict[str, Any] = {
    "foundation_types": ["掏挖基础", "挖孔桩基础", "阶梯式", "大板式", "岩石嵌固基础", "斜柱式", "灌注桩基础"],
    "tower_kinds": ["角钢塔", "钢管杆", "钢管塔"],
    "voltage_levels": ["10kV", "35kV", "110kV", "220kV", "330kV", "500kV"],
    "circuit_types": ["单回路", "双回路", "同塔双回", "同塔四回"],
    "units": {"weight": "t", "rebar": "kg", "concrete": "m³", "length": "km", "height": "m"},
    "chk_tolerances": {"CHK-01": 0.0, "CHK-02": 0.005, "CHK-03": 0.02, "CHK-04": 0.02, "CHK-05": 0.03, "CHK-08": 0.05},
}


@register_tool(
    "pdf_probe_catalog",
    SideEffectLevel.ARTIFACT_WRITE,
    artifact_subdir="01_catalog",
    description="扫描施工图目录、MD5 去重、分册角色侦测并输出 01_catalog/catalog.json",
)
def pdf_probe_catalog(
    *,
    workspace: Optional[TiziWorkspace] = None,
    folder_path: str,
    exclude_dirs: Optional[list[str]] = None,
) -> ToolResult[dict[str, Any]]:
    folder = str(folder_path or "").strip()
    if not folder or not os.path.isdir(folder):
        return ToolResult(ok=False, code=ErrorCode.INVALID_INPUT, summary=f"目录不存在：{folder}", recoverable=False)

    prof = discover.build(folder, exclude_dirs=exclude_dirs)
    aux = discover.detect_aux_sources(prof.get("files") or [], folder)
    cfg = {}
    if prof.get("files"):
        try:
            cfg = discover.to_configs(prof)
        except ValueError:
            # 仅含详图/扫描件目录尚未生成 T2 主数据源配置时，仍允许建立 catalog 与视觉索引
            cfg = {}
    sources_cfg = cfg.get("sources") or {}
    layout_cfg = cfg.get("layout") or {}
    map_cfg = cfg.get("mapping") or {}
    rule_cfg = cfg.get("rules") or {}

    # 识别真正包含 T3 基础详图或杆塔型式图的候选分册（严格排除 T2 明细表，避免无效 VLM 裁图）
    detail_docs = []
    for f in prof.get("files") or []:
        name = str(f.get("name") or f.get("rel") or "")
        if "明细表" in name and "编制说明" not in name:
            continue
        doc_role = ""
        if any(k in name for k in ("型式", "一览图")):
            doc_role = "tower_profile"
        elif any(k in name for k in ("基础详图", "基础施工图", "基础图")):
            doc_role = "foundation_detail"
        elif "编制说明" in name:
            doc_role = "notes"
        elif any(k in name for k in ("详图", "平断面", "接地装置图")):
            doc_role = "other_drawing"
        if doc_role:
            detail_docs.append({
                "rel": f.get("rel") or name,
                "name": name,
                "doc_role": doc_role,
                "pages": int(f.get("pages") or 0),
                "path": os.path.join(folder, f.get("rel") or name),
            })

    catalog_payload = {
        "folder": folder,
        "project": prof.get("project") or Path(folder).name,
        "files": prof.get("files") or [],
        "duplicates": prof.get("duplicates") or [],
        "aux_sources": aux,
        "detail_docs": detail_docs,
        "sources_cfg": sources_cfg,
        "layout_cfg": layout_cfg,
        "map_cfg": map_cfg,
        "rule_cfg": rule_cfg,
    }

    catalog_uri = None
    enum_uri = None
    if workspace is not None:
        catalog_uri = workspace.write_json("01_catalog/catalog.json", catalog_payload)
        enum_uri = workspace.write_json("01_catalog/enum_anchor_card.json", ENUM_ANCHOR_CARD)

    return ToolResult(
        ok=True,
        code=ErrorCode.OK,
        data={
            "project": catalog_payload["project"],
            "n_files": len(catalog_payload["files"]),
            "n_duplicates": len(catalog_payload["duplicates"]),
            "aux_keys": sorted(aux.keys()),
            "n_detail_docs": len(detail_docs),
            "catalog_uri": catalog_uri,
            "enum_card_uri": enum_uri,
        },
        artifact_uri=catalog_uri,
        summary=f"目录探查完成：{len(catalog_payload['files'])} 份有效 PDF，辅助源 {len(aux)} 类，详图候选 {len(detail_docs)} 份",
    )


@register_tool(
    "carrier_classify",
    SideEffectLevel.READ_ONLY,
    description="对施工图分册执行 T0~T3 载体分级判定",
)
def carrier_classify(
    *,
    workspace: Optional[TiziWorkspace] = None,
    pdf_path: str,
    cache_dir: Optional[str] = None,
) -> ToolResult[dict[str, Any]]:
    if not pdf_path or not os.path.isfile(pdf_path):
        return ToolResult(ok=False, code=ErrorCode.INVALID_INPUT, summary=f"PDF 不存在：{pdf_path}", recoverable=False)
    info = carrier.inspect(pdf_path, cache_dir=cache_dir)
    return ToolResult(
        ok=True,
        code=ErrorCode.OK,
        data=info,
        summary=f"载体分级：{Path(pdf_path).name} -> {info.get('tier', 'unknown')}（{info.get('n_pages', 0)} 页）",
    )
