# -*- coding: utf-8 -*-
"""实体级冲突报告（ConflictReport）与局部视觉重访规划契约（RevisitTask）。"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConflictItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_id: str
    chk_code: str = ""
    severity: Literal["P0", "P1", "P2"] = "P1"
    sheet: str
    row_idx: Optional[int] = None
    entity_id: str
    model_code: str = ""
    target_rows: list[int] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    impact_score: float = Field(default=0.0, ge=0.0)
    impact_weight: str = ""
    candidate_docs: list[str] = Field(default_factory=list)
    revisitable: bool = False
    revisit_priority: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    reason: str = ""
    current_value: Any = None


class ConflictReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_index: int = Field(default=0, ge=0)
    chks: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[ConflictItem] = Field(default_factory=list)
    revisitable_gaps: list[ConflictItem] = Field(default_factory=list)
    p0_count: int = Field(default=0, ge=0)
    p1_count: int = Field(default=0, ge=0)
    p2_count: int = Field(default=0, ge=0)
    is_converged: bool = False
    summary: str = ""


class RevisitTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    conflict_id: str
    chk_code: str
    sheet: str
    row_idx: Optional[int] = None
    entity_id: str
    model_code: str = ""
    target_rows: list[int] = Field(default_factory=list)
    target_schema_id: Literal["foundation_detail", "tower_profile", "conflict_context", "generic_table"] = "foundation_detail"
    doc_rel: str
    page: int = Field(ge=0)
    bbox: Optional[list[float]] = None
    missing_fields: list[str] = Field(default_factory=list)
    constraint_hints: str = ""
    round_index: int = Field(default=1, ge=1)
    expand_level: int = Field(default=0, ge=0)
