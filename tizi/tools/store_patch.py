# -*- coding: utf-8 -*-
"""Tool: workbook_store_patch — 唯一受控写库工具（保护人工已确认项 + 自动触发基础 DAG 联动）。"""
from __future__ import annotations

import threading
from typing import Any, Optional

from ..extractors import foundation as foundation_ext
from ..harness.registry import register_tool
from ..harness.store import ProjectTiziStore, patch_store_cell, save_store_to_workspace
from ..harness.workspace import TiziWorkspace
from ..schemas.base import ErrorCode, SideEffectLevel, ToolResult

_STORE_PATCH_LOCK = threading.RLock()


@register_tool(
    "workbook_store_patch",
    SideEffectLevel.STORE_MUTATE,
    artifact_subdir="08_store",
    description="原子回写 ProjectTiziStore（严禁覆盖已人工确认格）并自动触发基础库 DAG 增量重算",
)
def workbook_store_patch(
    *,
    workspace: Optional[TiziWorkspace] = None,
    store: ProjectTiziStore,
    patches: list[dict[str, Any]],
    actor: str = "deterministic",
) -> ToolResult[dict[str, Any]]:
    applied = 0
    skipped = 0
    touched_sheets: set[str] = set()
    details: list[dict[str, Any]] = []

    with _STORE_PATCH_LOCK:
        for p in patches or []:
            sheet = str(p["sheet"])
            row_idx = int(p["row_idx"])
            col_key = str(p["col_key"])
            res = patch_store_cell(
                store,
                sheet_name=sheet,
                row_idx=row_idx,
                col_key=col_key,
                value=p.get("value"),
                prov=p.get("prov"),
                actor=actor,
            )
            details.append(res)
            if res.get("applied"):
                applied += 1
                touched_sheets.add(sheet)
            else:
                skipped += 1

        dag_affected: list[str] = []
        if "基础库" in touched_sheets:
            foundation_ext.recalc_foundation_links(store)
            dag_affected.append("基础明细表")

        store_uri = None
        if workspace is not None:
            store_uri = save_store_to_workspace(store, workspace)

    return ToolResult(
        ok=True,
        code=ErrorCode.OK,
        data={
            "applied_count": applied,
            "skipped_count": skipped,
            "touched_sheets": sorted(touched_sheets),
            "dag_recalc_affected": dag_affected,
            "store_uri": store_uri,
            "details": details[:15],
        },
        artifact_uri=store_uri,
        summary=f"写库完成（actor={actor}）：应用 {applied} 格，保护跳过 {skipped} 格",
    )
