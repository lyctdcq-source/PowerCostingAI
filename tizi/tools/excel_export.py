# -*- coding: utf-8 -*-
"""Tool: tizi_excel_export — 院标 13-Sheet 双轨导出工具（强制执行 P0/P1 导出门禁）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .. import template_writer
from ..harness.registry import register_tool
from ..harness.store import ProjectTiziStore, evaluate_export_gate
from ..harness.workspace import TiziWorkspace
from ..schemas.base import ErrorCode, SideEffectLevel, ToolResult


@register_tool(
    "tizi_excel_export",
    SideEffectLevel.EXPORT,
    artifact_subdir="11_exports",
    description="校验 HITL 导出门禁后生成院标 13-Sheet 标准导入版与复核溯源版 Excel",
)
def tizi_excel_export(
    *,
    workspace: Optional[TiziWorkspace] = None,
    store: ProjectTiziStore,
    checks: list[dict[str, Any]],
    out_dir: Optional[str | Path] = None,
    human_decisions: Optional[list[dict[str, Any]]] = None,
    enforce_gate: bool = True,
    allow_draft_skip_unconfirmed: bool = False,
) -> ToolResult[dict[str, Any]]:
    if enforce_gate:
        gate = evaluate_export_gate(
            store,
            human_decisions=human_decisions,
            allow_draft_skip_unconfirmed=allow_draft_skip_unconfirmed,
        )
        if not gate.allowed:
            return ToolResult(
                ok=False,
                code=ErrorCode.HITL_BLOCKED,
                data=gate.model_dump(),
                summary=gate.message,
                recoverable=True,
            )

    target_dir = Path(out_dir) if out_dir else (workspace.resolve_safe("11_exports") if workspace else Path("."))
    target_dir.mkdir(parents=True, exist_ok=True)
    wb_res = template_writer.export_workbook_pair(store, checks, target_dir)

    return ToolResult(
        ok=True,
        code=ErrorCode.OK,
        data={
            "names": list(wb_res.get("names") or []),
            "standard": str(wb_res.get("standard") or ""),
            "audit": str(wb_res.get("audit") or ""),
        },
        summary=f"院标双轨提资单导出成功：{', '.join(wb_res.get('names') or [])}",
    )
