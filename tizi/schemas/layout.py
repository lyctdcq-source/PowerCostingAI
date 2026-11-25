# -*- coding: utf-8 -*-
"""跨设计院表头与版式学习契约（LayoutProfile + 5 行抽样值域自检报告）。"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class HeaderFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    doc_id: str
    target_sheet: str
    col_count: int = Field(ge=0)
    header_tokens: list[str] = Field(default_factory=list)


class SampleSanityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    checked_rows: int = Field(default=0, ge=0)
    non_empty_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    violations: list[str] = Field(default_factory=list)
    sample_preview: list[dict[str, Any]] = Field(default_factory=list)


class LayoutProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    doc_id: str
    target_sheet: str
    header_fingerprint: str
    institute_hint: str = ""
    source: Literal["enterprise_cache", "deterministic_match", "vlm_learned", "human_override"] = "deterministic_match"
    column_mapping: dict[int, str] = Field(default_factory=dict)
    unit_overrides: dict[str, str] = Field(default_factory=dict)
    tower_id_regex: Optional[str] = None
    segment_prefix_map: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    sanity_passed: bool = False
    sanity_report: Optional[SampleSanityReport] = None
    sample_crop_uri: Optional[str] = None
    prompt_version: str = ""
