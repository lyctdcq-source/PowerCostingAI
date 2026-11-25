# -*- coding: utf-8 -*-
"""L2 标准化工具注册表（ToolRegistry）：强 Schema 校验、副作用守卫、>2KB 自动外置与统一错误码。"""
from __future__ import annotations

import inspect
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Type

from pydantic import BaseModel, ValidationError

from ..schemas.base import ErrorCode, SideEffectLevel, ToolResult
from .workspace import TiziWorkspace

_LOG = logging.getLogger("tizi.harness.registry")


@dataclass
class ToolSpec:
    name: str
    side_effect: SideEffectLevel
    func: Callable[..., Any]
    input_schema: Optional[Type[BaseModel]] = None
    artifact_subdir: str = "03_cv_segments"
    description: str = ""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        side_effect: SideEffectLevel,
        *,
        input_schema: Optional[Type[BaseModel]] = None,
        artifact_subdir: str = "03_cv_segments",
        description: str = "",
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._tools[name] = ToolSpec(
                name=name,
                side_effect=side_effect,
                func=fn,
                input_schema=input_schema,
                artifact_subdir=artifact_subdir,
                description=description or (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "",
            )
            return fn

        return _decorator

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"未注册工具：{name}")
        return self._tools[name]

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "side_effect": spec.side_effect.value,
                "description": spec.description,
            }
            for spec in self._tools.values()
        ]

    def invoke(
        self,
        name: str,
        *,
        workspace: Optional[TiziWorkspace] = None,
        tracer: Any = None,
        **kwargs: Any,
    ) -> ToolResult[Any]:
        t0 = time.perf_counter()
        if name not in self._tools:
            return ToolResult(
                ok=False,
                code=ErrorCode.INVALID_INPUT,
                summary=f"未知工具：{name}",
                recoverable=False,
            )
        spec = self._tools[name]

        try:
            if spec.input_schema is not None:
                validated = spec.input_schema.model_validate(kwargs)
                call_kwargs = validated.model_dump()
            else:
                call_kwargs = dict(kwargs)
        except ValidationError as exc:
            elapsed = int((time.perf_counter() - t0) * 1000)
            return ToolResult(
                ok=False,
                code=ErrorCode.SCHEMA_INVALID,
                summary=f"工具 {name} 入参校验失败：{exc.error_count()} 处错误",
                elapsed_ms=elapsed,
                recoverable=False,
                meta={"errors": exc.errors()},
            )

        try:
            sig = inspect.signature(spec.func)
            if "tracer" in sig.parameters and "tracer" not in call_kwargs:
                call_kwargs["tracer"] = tracer
            raw_res = spec.func(workspace=workspace, **call_kwargs)
            elapsed = int((time.perf_counter() - t0) * 1000)
            if isinstance(raw_res, ToolResult):
                result = raw_res.model_copy(update={"elapsed_ms": raw_res.elapsed_ms or elapsed})
            else:
                result = ToolResult(ok=True, code=ErrorCode.OK, data=raw_res, elapsed_ms=elapsed)

            # Artifact-by-Reference：自动外置超大返回值（仅对未手工设置 artifact_uri 的成功结果生效）
            if result.ok and workspace is not None and result.data is not None and not result.artifact_uri:
                inline_data, ext_uri, ext_summary = workspace.externalize_if_large(
                    spec.artifact_subdir, name, result.data
                )
                if ext_uri:
                    result = result.model_copy(
                        update={
                            "data": inline_data,
                            "artifact_uri": ext_uri,
                            "summary": result.summary or ext_summary,
                        }
                    )
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.perf_counter() - t0) * 1000)
            _LOG.warning("Tool %s execution failed: %s", name, exc)
            code = ErrorCode.INTERNAL_ERROR
            msg = str(exc)
            if "越权" in msg:
                code = ErrorCode.PATH_VIOLATION
            elif "超时" in msg or "timeout" in msg.lower():
                code = ErrorCode.VLM_TIMEOUT
            elif "限速" in msg or "429" in msg:
                code = ErrorCode.VLM_RATE_LIMIT
            elif "VLM 未配置" in msg:
                code = ErrorCode.VLM_UNAVAILABLE
            result = ToolResult(
                ok=False,
                code=code,
                summary=f"{name} 执行失败：{msg[:240]}",
                elapsed_ms=elapsed,
                recoverable=code in (ErrorCode.VLM_TIMEOUT, ErrorCode.VLM_RATE_LIMIT, ErrorCode.CROP_TRUNCATED),
            )

        if tracer is not None:
            tracer.log_tool(
                tool=name,
                side_effect=spec.side_effect.value,
                ok=result.ok,
                code=result.code.value,
                elapsed_ms=result.elapsed_ms,
                artifact_uri=result.artifact_uri or "",
                summary=result.summary[:160],
            )
        return result


_DEFAULT_REGISTRY = ToolRegistry()


def get_default_registry() -> ToolRegistry:
    return _DEFAULT_REGISTRY


def register_tool(
    name: str,
    side_effect: SideEffectLevel,
    *,
    input_schema: Optional[Type[BaseModel]] = None,
    artifact_subdir: str = "03_cv_segments",
    description: str = "",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    return _DEFAULT_REGISTRY.register(
        name,
        side_effect,
        input_schema=input_schema,
        artifact_subdir=artifact_subdir,
        description=description,
    )
