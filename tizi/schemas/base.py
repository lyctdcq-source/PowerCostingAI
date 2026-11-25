# -*- coding: utf-8 -*-
"""基础契约：统一错误码、副作用级别、溯源引用与泛型 ToolResult 包装壳。"""
from __future__ import annotations

from enum import Enum
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ErrorCode(str, Enum):
    OK = "OK"
    INVALID_INPUT = "INVALID_INPUT"
    PATH_VIOLATION = "PATH_VIOLATION"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    CROP_TRUNCATED = "CROP_TRUNCATED"
    SANITY_CHECK_FAILED = "SANITY_CHECK_FAILED"
    VLM_UNAVAILABLE = "VLM_UNAVAILABLE"
    VLM_TIMEOUT = "VLM_TIMEOUT"
    VLM_RATE_LIMIT = "VLM_RATE_LIMIT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    HITL_BLOCKED = "HITL_BLOCKED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class SideEffectLevel(str, Enum):
    READ_ONLY = "READ_ONLY"
    ARTIFACT_WRITE = "ARTIFACT_WRITE"
    STORE_MUTATE = "STORE_MUTATE"
    EXPORT = "EXPORT"


class ProvenanceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = "原始出处"
    file: str = ""
    page: Optional[int] = None
    bbox: Optional[list[float]] = None
    crop_uri: Optional[str] = None
    raw_text: str = ""
    actor: str = "deterministic"  # deterministic | vlm | human


class ToolResult(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    code: ErrorCode = ErrorCode.OK
    data: Optional[T] = None
    artifact_uri: Optional[str] = None
    summary: str = ""
    elapsed_ms: int = Field(default=0, ge=0)
    recoverable: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)
