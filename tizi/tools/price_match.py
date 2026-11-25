# -*- coding: utf-8 -*-
"""Tool: price_library_match — 只读平台价格库匹配工具。"""
from __future__ import annotations

from typing import Any, Optional

from .. import price_match
from ..harness.registry import register_tool
from ..harness.store import ProjectTiziStore
from ..harness.workspace import TiziWorkspace
from ..schemas.base import ErrorCode, SideEffectLevel, ToolResult


@register_tool(
    "price_library_match",
    SideEffectLevel.READ_ONLY,
    description="将电气/光缆/结构材料表只读匹配至平台参考价格库",
)
def price_library_match(
    *,
    workspace: Optional[TiziWorkspace] = None,
    store: ProjectTiziStore,
    sheet_name: str,
    prices: list[dict[str, Any]],
) -> ToolResult[dict[str, Any]]:
    res = price_match.match_sheet_prices(store, sheet_name, prices)
    return ToolResult(
        ok=True,
        code=ErrorCode.OK,
        data=res,
        summary=f"价格库匹配 {sheet_name}：命中 {res.get('matched', 0)}/{res.get('total', 0)} 项",
    )
