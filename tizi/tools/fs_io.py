# -*- coding: utf-8 -*-
"""Tool: artifact_fs_io — 受沙箱保护的协作工件分片读写工具。"""
from __future__ import annotations

from typing import Any, Optional

from ..harness.registry import register_tool
from ..harness.workspace import TiziWorkspace
from ..schemas.base import ErrorCode, SideEffectLevel, ToolResult


@register_tool(
    "artifact_fs_io",
    SideEffectLevel.ARTIFACT_WRITE,
    description="按分片安全读取或写入会话协作目录下的 JSON 工件（受沙箱路径守卫）",
)
def artifact_fs_io(
    *,
    workspace: Optional[TiziWorkspace] = None,
    op: str,
    uri: str,
    payload: Any = None,
    offset: int = 0,
    limit: int = 20,
    key: str = "",
) -> ToolResult[Any]:
    if workspace is None:
        return ToolResult(ok=False, code=ErrorCode.INVALID_INPUT, summary="缺少 workspace 实例", recoverable=False)

    if op == "read_slice":
        sl = workspace.read_slice(uri, offset=offset, limit=limit, key=key)
        return ToolResult(ok=True, code=ErrorCode.OK, data=sl, summary=f"读取分片 {uri} (offset={offset}, limit={limit})")
    if op == "write_json":
        written = workspace.write_json(uri, payload)
        return ToolResult(ok=True, code=ErrorCode.OK, data={"uri": written}, artifact_uri=written, summary=f"写入工件 {written}")
    return ToolResult(ok=False, code=ErrorCode.INVALID_INPUT, summary=f"不支持的 op：{op}", recoverable=False)
